"""FastAPI server — LSI 고장 분석 추천 API.

Endpoints:
    POST /recommend          -> 유사 해결 사례 + root-cause/해결책 제안 (+ LLM 종합)
    GET  /issues/unresolved  -> 미해결 이슈 목록
    GET  /reco/stats         -> KB 통계
    GET  /health
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lang_validator import validate_and_fix  # noqa: E402
from preprocess import parse_issue  # noqa: E402
from recommender import Recommender, template_key  # noqa: E402
import app_config  # noqa: E402

# 저장된 온보딩 설정(Hermes Gateway/Jira)을 env에 주입 — 서버 기동 시 1회.
app_config.load_into_env()

# LLM 설명 생성 엔진 선택: agno(OpenRouter) | hermes(Hermes Agent CLI)
ENGINE = os.getenv("RVP_ENGINE", "agno").lower()
if ENGINE == "hermes":
    from hermes_engine import HermesEngine  # noqa: E402
    _HERMES = HermesEngine()

app = FastAPI(title="LSI Failure Analysis API")

# ---------------------------------------------------------------------------
# 추천 엔진 (과거 해결 이슈 → 미해결 이슈의 root-cause/해결책 제안)
# ---------------------------------------------------------------------------
ALL_RAW = ROOT / "data" / "all_raw_issues.json"
RESOLVED_STATUS = "완료"

_RECO_STATE: dict = {}


def _reco_state() -> dict:
    """all_raw_issues.json 로드 → 레코드 파싱 → recommender(해결 KB) 1회 빌드(캐시)."""
    if _RECO_STATE:
        return _RECO_STATE
    if not ALL_RAW.exists():
        raise RuntimeError(
            "data/all_raw_issues.json 없음 — 먼저 실행: "
            ".venv/bin/python src/eval_recommender.py (또는 src/ingest.py --status all)")
    raw = json.loads(ALL_RAW.read_text(encoding="utf-8"))
    records = [parse_issue(r) for r in raw]
    resolved = [r for r in records if r["status"] == RESOLVED_STATUS]
    unresolved = [r for r in records if r["status"] != RESOLVED_STATUS]
    # 인입 품질 게이트(P1-2): 무음 추출 실패를 서빙 시점에 표면화(차단 아님, 경고).
    try:
        import quality_gate
        _q = quality_gate.validate(records, resolved_status=RESOLVED_STATUS)
        if not _q["ok"]:
            print("[server] ⚠ KB 품질 경고: " + " / ".join(_q["violations"]))
    except Exception:
        pass
    # KB 환류: 사람이 승인·수정한 RCA를 큐레이션 KB로 추가(같은 클래스 검색·제안 개선).
    # 1순위는 영속 저장소(data/knowledge_store.json, git 추적), rca_feedback는 폴백.
    # 동일 key는 영속 저장소 우선으로 dedupe.
    try:
        import knowledge_store
        import rca_feedback
        curated, seen = [], set()
        for r in knowledge_store.kb_records() + rca_feedback.kb_records():
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            curated.append(r)
        if curated:
            resolved = resolved + curated
            records = records + curated
    except Exception:
        pass
    _RECO_STATE.update({
        "records": records,
        "by_key": {r["key"]: r for r in records},
        "resolved": resolved,
        "unresolved": unresolved,
        # hybrid_embed + 단계 인지 문서(제기+분석). RVP_RERANK=1 → 2차 cross-encoder
        # 재순위 + rerank 강도 게이트(측정: paraphrase P@1 .898→1.0, 무관 완전 분리).
        # (A/B: tmp_db/ab_reranker.json, claudedocs/similarity_search_plan.md)
        "reco": Recommender(
            resolved,
            method=os.getenv("RVP_RECO_METHOD", "hybrid_embed"),
            rerank=os.getenv("RVP_RERANK", "0") == "1",
            rerank_model=os.getenv("RVP_RERANK_MODEL", "cohere/rerank-v3.5"),
        ),
    })
    return _RECO_STATE

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------------------
# 설정 온보딩 (Hermes Gateway / Jira) — 미설정 시 프론트가 강제 진입
# ---------------------------------------------------------------------------
class ConfigBody(BaseModel):
    jira: Optional[dict] = None       # {base_url, project_key, email, api_token, pat}
    hermes: Optional[dict] = None     # {gateway_url, api_key, model}


@app.get("/config/status")
def config_status():
    return app_config.status()


@app.post("/config")
def config_save(body: ConfigBody):
    st = app_config.save(body.jira, body.hermes)
    _RECO_STATE.clear()  # Jira 변경 반영 위해 KB 캐시 무효화
    return st


@app.post("/config/test/jira")
def config_test_jira(body: ConfigBody):
    import requests
    j = body.jira or {}
    base = (j.get("base_url") or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
    project = j.get("project_key") or os.getenv("JIRA_PROJECT_KEY", "")
    if not base or not project:
        return {"ok": False, "error": "base_url 과 project_key 가 필요합니다."}
    s = requests.Session()
    pat = j.get("pat") or os.getenv("JIRA_PAT")
    if pat:
        s.headers["Authorization"] = f"Bearer {pat}"
    else:
        email = j.get("email") or os.getenv("JIRA_EMAIL")
        token = j.get("api_token") or os.getenv("JIRA_API_TOKEN")
        if not (email and token):
            return {"ok": False, "error": "인증 정보 부족: PAT 또는 (email + API token)"}
        s.auth = (email, token)
    try:
        r = s.get(f"{base}/rest/api/2/myself", timeout=15)
        r.raise_for_status()
        me = r.json().get("displayName") or r.json().get("name")
        rp = s.get(f"{base}/rest/api/2/project/{project}", timeout=15)
        rp.raise_for_status()
        return {"ok": True, "user": me, "project": rp.json().get("name")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/config/test/hermes")
def config_test_hermes(body: ConfigBody):
    import requests
    h = body.hermes or {}
    base = (h.get("gateway_url") or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip("/")
    key = h.get("api_key") or os.getenv("OPENROUTER_API_KEY")
    if not key:
        return {"ok": False, "error": "API key 가 필요합니다."}
    try:
        r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        r.raise_for_status()
        n = len(r.json().get("data", []))
        return {"ok": True, "models": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Hermes Agent(CLI 프로필) 설정 처리 — 미설정 감지 + 자동 등록/안내 ---
HERMES_PROFILE = "lsi"


def _hermes_base_bin() -> str:
    import shutil
    return shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")


def _hermes_probe() -> dict:
    """hermes agent 셋업 상태 점검 — 온보딩 체크리스트용."""
    import subprocess
    base = _hermes_base_bin()
    installed = os.path.exists(base)
    prof_dir = Path(os.path.expanduser(f"~/.hermes/profiles/{HERMES_PROFILE}"))
    profile_exists = prof_dir.exists()
    has_key, model = False, ""
    if installed:
        try:
            args = [base] + (["-p", HERMES_PROFILE] if profile_exists else []) + ["status"]
            out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
            for line in out.splitlines():
                s = line.strip()
                if s.startswith("Model:"):
                    model = s.split(":", 1)[1].strip()
                if "✓" in s and any(p in s for p in ("OpenRouter", "OpenAI", "Gemini", "Google", "Anthropic")):
                    has_key = True
        except Exception:
            pass
    steps = [
        {"key": "install", "label": "hermes CLI 설치", "done": installed, "auto": False,
         "hint": "pipx install hermes-agent  (또는 pip install hermes-agent)"},
        {"key": "auth", "label": "제공자 인증 / API 키", "done": has_key, "auto": False,
         "hint": "hermes login  또는  hermes setup  (브라우저 OAuth 또는 키 입력)"},
        {"key": "profile", "label": f"'{HERMES_PROFILE}' 프로필(agent) 등록", "done": profile_exists, "auto": True,
         "hint": f"hermes profile create {HERMES_PROFILE} --clone"},
    ]
    return {"installed": installed, "bin": base, "profile_exists": profile_exists,
            "has_key": has_key, "model": model,
            "ready": installed and profile_exists and has_key, "steps": steps}


@app.get("/config/hermes/probe")
def hermes_probe():
    return _hermes_probe()


@app.post("/config/hermes/ensure-profile")
def hermes_ensure_profile():
    """자동화 가능한 단계 처리: 'lsi' 프로필이 없으면 생성(clone) + HERMES_BIN 연결."""
    import subprocess
    base = _hermes_base_bin()
    if not os.path.exists(base):
        return {"ok": False, "error": "hermes CLI가 설치되어 있지 않습니다. 먼저 설치하세요.",
                "probe": _hermes_probe()}
    prof_dir = Path(os.path.expanduser(f"~/.hermes/profiles/{HERMES_PROFILE}"))
    created = False
    if not prof_dir.exists():
        r = subprocess.run(
            [base, "profile", "create", HERMES_PROFILE, "--clone", "--description",
             "LSI 불량 분석 어시스턴트: 과거 해결 이슈 기반 근본원인·해결책 추천 + Jira RCA 댓글"],
            capture_output=True, text=True, timeout=90)
        created = r.returncode == 0
        if not created:
            return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300],
                    "probe": _hermes_probe()}
    # 앱이 등록된 프로필로 LLM을 호출하도록 HERMES_BIN(래퍼) 영속화
    wrapper = os.path.expanduser(f"~/.local/bin/{HERMES_PROFILE}")
    if os.path.exists(wrapper):
        app_config.set_env("HERMES_BIN", wrapper)
    return {"ok": True, "created": created,
            "hermes_bin": wrapper if os.path.exists(wrapper) else base,
            "probe": _hermes_probe()}


# ---------------------------------------------------------------------------
# 고장 분석 추천 엔드포인트
# ---------------------------------------------------------------------------


class RecommendRequest(BaseModel):
    key: Optional[str] = None          # 미해결 이슈 키 (예: LSI-7)
    summary: Optional[str] = None      # 또는 자유 입력
    symptom: Optional[str] = None
    chip: Optional[str] = None
    category: Optional[str] = None
    labels: Optional[list[str]] = None
    k: int = 3
    explain: bool = False              # LLM으로 종합 설명 생성


@app.get("/reco/stats")
def reco_stats():
    st = _reco_state()
    reco = st["reco"]
    from collections import Counter
    cats = Counter(r["category"] for r in st["resolved"])
    return {
        "resolved": len(st["resolved"]),
        "unresolved": len(st["unresolved"]),
        "templates": len(set(template_key(r["summary"]) for r in st["resolved"])),
        "by_category": dict(cats),
        "method": reco.method,
    }


class RecoFeedbackBody(BaseModel):
    query_key: str = ""
    query_summary: str = ""
    match_key: str
    rating: str                       # "helpful" | "not_helpful"
    is_actual_root_cause: bool = False
    match_rank: Optional[int] = None
    match_score: Optional[float] = None
    note: str = ""


@app.post("/reco/feedback")
def reco_feedback(req: RecoFeedbackBody):
    """추천 유용성/결과 피드백 기록(P1-3) — 도움됨·아님, 실제 근본원인 여부."""
    import reco_feedback
    try:
        ev = reco_feedback.record(
            query_key=req.query_key, match_key=req.match_key, rating=req.rating,
            query_summary=req.query_summary,
            query_template=template_key(req.query_summary) if req.query_summary else "",
            is_actual_root_cause=req.is_actual_root_cause,
            match_rank=req.match_rank, match_score=req.match_score, note=req.note)
        return {"ok": True, "event": ev, "stats": reco_feedback.stats()}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.get("/reco/feedback/stats")
def reco_feedback_stats():
    """유용성 집계 + ROI 프록시 + 실전형 평가셋 정답 쌍."""
    import reco_feedback
    return {"stats": reco_feedback.stats(), "eval_pairs": reco_feedback.eval_pairs()}


@app.get("/issues/unresolved")
def unresolved_issues():
    st = _reco_state()
    out = []
    for r in st["unresolved"]:
        out.append({
            "key": r["key"], "summary": r["summary"], "status": r["status"],
            "chip": r["chip"], "category": r["category"],
            "priority": r["priority"], "severity": r.get("severity", ""),
            "symptom": r["symptom"],
        })
    # 상태(진행 중 먼저) → 키 순
    out.sort(key=lambda x: (0 if x["status"] == "진행 중" else 1, x["key"]))
    return {"count": len(out), "issues": out}


@app.get("/graph")
def issue_graph(key: Optional[str] = None, k: int = 12, min_shared: int = 2):
    """이슈 간 관계 그래프 — 공유 엔티티(칩/분류/기술용어/라벨) 기반.

    key 지정 시: 그 이슈 중심 ego-그래프(가장 많이 겹치는 이웃 top-k + 이웃 간 엣지).
    미지정 시: 미해결 이슈를 시드로 한 소규모 샘플.
    엣지 가중치=공유 엔티티 수, same_template=동일 근본원인 클래스(굵게 표시용).
    """
    st = _reco_state()
    recs = st["records"]
    by_key = st["by_key"]
    ent = {r["key"]: set(r.get("entities", [])) for r in recs}

    from recommender import _doc_text  # KB 문서 표현(요약+증상+분석) 재사용

    rr: dict[str, float] = {}   # center→이웃 rerank 관련도(0~1)
    if key and key in by_key:
        c = ent[key]
        scored = sorted(
            ((r, len(c & ent[r["key"]])) for r in recs if r["key"] != key),
            key=lambda x: -x[1])
        neigh = [r for r, w in scored if w >= min_shared][:k]
        # 엣지 강도를 reranker(cross-encoder)로 계산 — center를 질의로, 이웃을 문서로.
        # 1회 호출. 실패/미설정 시 공유 엔티티 가중치로 폴백.
        try:
            from reranker import rerank as _rerank
            docs = [_doc_text(r, analysis=True) for r in neigh]
            order = _rerank(_doc_text(by_key[key], analysis=True), docs)
            rr = {neigh[idx]["key"]: float(sc) for idx, sc in order}
            neigh.sort(key=lambda r: -rr.get(r["key"], 0.0))  # 관련도 내림차순
        except Exception:
            rr = {}
        nodeset = [by_key[key]] + neigh
    else:
        nodeset = st["unresolved"][:k] or recs[:k]

    ekeys = [r["key"] for r in nodeset]
    nodes = [{
        "id": r["key"], "label": r["summary"], "status": r["status"],
        "category": r["category"], "chip": r["chip"],
        "template": template_key(r["summary"]),
        "center": bool(key) and r["key"] == key,
        # center 대비 rerank 관련도(0~1) — 노드 크기/거리 인코딩용. center=1.0.
        "relevance": 1.0 if (key and r["key"] == key) else rr.get(r["key"]),
    } for r in nodeset]
    edges = []
    for i in range(len(ekeys)):
        for j in range(i + 1, len(ekeys)):
            a, b = ekeys[i], ekeys[j]
            w = len(ent[a] & ent[b])
            if w < min_shared:
                continue
            touches_center = bool(key) and (a == key or b == key)
            other = (b if a == key else a) if touches_center else None
            edges.append({
                "source": a, "target": b, "weight": w,
                "same_template": (template_key(by_key[a]["summary"])
                                  == template_key(by_key[b]["summary"])),
                # center 엣지는 rerank 관련도(0~1)를 강도로 — 굵기/투명도 인코딩.
                "rerank": rr.get(other) if touches_center else None,
            })
    return {"center": key, "nodes": nodes, "edges": edges, "has_rerank": bool(rr)}


class RcaExplanation(BaseModel):
    """LLM 종합 분석의 구조화 출력 (agno output_schema)."""
    root_cause: str = Field(description="예상 근본 원인 (한국어, 간결)")
    resolution: str = Field(description="권장 해결 단계 (한국어, 간결)")
    workaround: str = Field(default="", description="임시 우회책 (한국어, 없으면 빈 문자열)")
    cited_keys: list[str] = Field(
        default_factory=list,
        description="분석 근거로 인용한 과거 이슈 키 목록. 반드시 제공된 '과거 해결 사례'의 키 중에서만 선택(새 키 창작 금지). 예: LSI-49")


def _explain_prompt(query_rec: dict, matches: list[dict]) -> str:
    cases = "\n\n".join(
        f"[{m['key']}] {m['summary']}\n근본원인: {m['root_cause']}\n해결책: {m['resolution']}\n우회책: {m['workaround']}"
        for m in matches)
    return (
        "당신은 LSI 칩/펌웨어 불량 분석 시니어 엔지니어입니다. 아래 미해결 이슈에 대해, "
        "제공된 '과거 해결 사례'만 근거로 예상 근본원인·권장 해결 단계·임시 우회책을 한국어로 간결히 작성하세요. "
        "cited_keys에는 근거로 쓴 과거 사례의 키만 넣으세요(새 키 창작 금지). 한자/CJK 한자 금지.\n\n"
        f"## 미해결 이슈\n{query_rec.get('summary','')}\n증상: {query_rec.get('symptom','')}\n"
        f"칩: {query_rec.get('chip','')} / 분류: {query_rec.get('category','')}\n\n"
        f"## 과거 해결 사례\n{cases}\n")


def _agno_explain(prompt: str) -> "RcaExplanation | None":
    """agno Agent + output_schema 로 구조화 RCA 생성 (citations 포함)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    from agno.agent import Agent
    from agno.models.openrouter import OpenRouter
    model_id = os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    agent = Agent(
        model=OpenRouter(id=model_id, api_key=api_key, base_url=base),
        output_schema=RcaExplanation,
        use_json_mode=True,  # 모델 무관 호환(네이티브 structured 미지원 모델 대비)
        instructions=[
            "LSI 칩/펌웨어 불량 분석 시니어 엔지니어로서 답한다.",
            "제공된 '과거 해결 사례'만 근거로 사용하고, cited_keys에는 그 사례의 키만 넣는다(창작 금지).",
            "모든 텍스트는 한국어. 한자/CJK 한자 금지 — 한글/영문/숫자/문장부호만.",
        ],
        markdown=False, telemetry=False,
    )
    out = agent.run(input=prompt)
    return out.content if isinstance(out.content, RcaExplanation) else None


def _compose_explanation(exp: "RcaExplanation", valid_keys: set[str]) -> tuple[str, list[str], list[str]]:
    """구조화 출력 → 표시용 마크다운 + 검증된 인용/탈락 인용. (인용 게이트가 구조적으로 해결됨)"""
    cited = [k for k in exp.cited_keys if k in valid_keys]
    dropped = [k for k in exp.cited_keys if k not in valid_keys]  # 환각/무관 키
    md = f"### 🔍 예상 근본원인\n{exp.root_cause}\n\n### ✅ 권장 해결책\n{exp.resolution}\n"
    if (exp.workaround or "").strip():
        md += f"\n### ↪ 임시 우회책\n{exp.workaround}\n"
    md += f"\n_근거(검증됨): {', '.join(cited)}_" if cited else "\n_근거로 인용된 과거 사례 없음_"
    return md, cited, dropped


def _llm_explain(query_rec: dict, matches: list[dict]) -> dict:
    """상위 매치 근거로 종합 설명 생성. 반환: {markdown, citations, dropped}.

    agno output_schema 로 구조화 출력 → cited_keys 를 매치 키와 대조 검증해
    환각 인용을 제거(기존 정규식 인용 게이트를 구조적으로 대체).
    """
    import re
    prompt = _explain_prompt(query_rec, matches)
    valid_keys = {m["key"] for m in matches}
    if ENGINE == "hermes":
        try:
            raw = _HERMES.complete(prompt)
            vr = validate_and_fix(raw)
            text = vr.rewritten if (not vr.ok and vr.rewritten) else raw
        except Exception as e:
            return {"markdown": f"(LLM 설명 생성 실패: {e})", "citations": [], "dropped": []}
        cited = sorted({k for k in re.findall(r"LSI-\d+", text)} & valid_keys)
        return {"markdown": text, "citations": cited, "dropped": []}
    try:
        exp = _agno_explain(prompt)
    except Exception as e:
        return {"markdown": f"(LLM 설명 생성 실패: {e})", "citations": [], "dropped": []}
    if exp is None:
        return {"markdown": "", "citations": [], "dropped": []}
    md, cited, dropped = _compose_explanation(exp, valid_keys)
    vr = validate_and_fix(md)  # CJK 안전망
    if not vr.ok and vr.rewritten:
        md = vr.rewritten
    return {"markdown": md, "citations": cited, "dropped": dropped}


def _case_block(r: dict) -> str:
    """근거 사례를 풍부하게 직렬화 — 증상/디버깅 접근/근본원인/해결책/우회책."""
    parts = [f"[{r.get('key','')}] {r.get('summary','')}"]
    for label, field in (("증상", "symptom"), ("디버깅 접근", "debug_approach"),
                         ("근본원인", "root_cause"), ("해결책", "resolution"), ("우회책", "workaround")):
        v = (r.get(field) or "").strip()
        if v:
            parts.append(f"{label}: {v}")
    return "\n".join(parts)


def _explain_prompt_md(query_rec: dict, match_recs: list[dict]) -> str:
    """스트리밍용 심화 분석 프롬프트 — 인과/사례종합/검증방법/재발방지/불확실성 + 인라인 인용."""
    cases = "\n\n".join(_case_block(r) for r in match_recs)
    q = query_rec
    q_extra = f"\n진행 단서(조사/트리아지): {q.get('investigation','')}" if (q.get("investigation") or "").strip() else ""
    # 성능 개선 루프: 사람이 검토·수정한 과거 분석을 문체/수준 가이드(few-shot)로 주입
    fewshot = ""
    try:
        import rca_feedback
        # 같은 고장 클래스(동일 템플릿/분류)의 사람 수정만 — 무관 이슈 예시 주입 방지
        ex = rca_feedback.relevant_edits(category=q.get("category", ""),
                                         template=template_key(q.get("summary", "")),
                                         n=2, max_len=450)
        if ex:
            blocks = "\n\n".join(f"[{e['key']}] {e['summary'][:50]}\n{e['final_body']}" for e in ex)
            fewshot = ("\n\n## 같은 유형에서 사람이 검토·수정한 분석 예시 (문체·정정 방향 참고, 내용 복붙 금지)\n"
                       + blocks + "\n")
    except Exception:
        pass
    return (
        "당신은 LSI 칩/펌웨어 불량 분석 시니어 엔지니어입니다. 제공된 '과거 해결 사례'만 근거로 "
        "아래 미해결 이슈를 깊이 있게 분석하세요. 다음 섹션을 순서대로 **모두 빠짐없이** 한국어 마크다운으로 작성합니다:\n"
        "### 🎯 예상 근본원인\n"
        "### 🔍 증상→원인 인과 분석  (관찰 증상이 어떤 메커니즘으로 해당 원인을 시사하는지 단계적으로)\n"
        "### ✅ 권장 해결 단계  (번호가 있는 구체적 순서)\n"
        "### ↪ 임시 우회책\n"
        "### 🔬 근본원인 검증 방법  (어떤 신호·측정·재현 절차로 확인하는지 구체적으로)\n"
        "### 🧩 사례 종합 / 재발 방지  (인용 사례의 공통 패턴·차이점 + 예방 포인트)\n"
        "### ⚠ 불확실성·주의  (근거가 약하거나 사례와 다른 부분)\n"
        "각 핵심 주장 옆에 근거 사례 키를 (LSI-49)처럼 인라인 인용하세요(제공된 키만, 창작 금지). 한자/CJK 한자 금지.\n\n"
        f"## 미해결 이슈\n{q.get('summary','')}\n증상: {q.get('symptom','')}\n"
        f"칩: {q.get('chip','')} / 분류: {q.get('category','')}{q_extra}\n\n"
        f"## 과거 해결 사례\n{cases}\n{fewshot}")


def _llm_stream(prompt: str, reasoning: bool = False):
    """OpenRouter chat/completions 스트리밍 — 콘텐츠 토큰(str)만 순차 yield.

    agno 스트리밍 래퍼는 추론 모델(deepseek-v4-flash 등)에서 콘텐츠 스트림을
    조기 종료시켜 답변이 헤더/문장 도중에 잘리는 문제가 있다(비스트리밍/직접
    스트리밍은 정상 완결). 따라서 OpenRouter SSE를 직접 호출한다. 추론 델타는
    별도 'reasoning' 필드로 오므로 무시하고 최종 콘텐츠만 전송한다.
    """
    import urllib.request
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return
    model_id = os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    # 한국어는 토큰 소모가 커 상한이 낮으면 도중에 잘린다. 기본 8000, env로 조정.
    max_tokens = int(os.getenv("RVP_EXPLAIN_MAX_TOKENS", "8000"))
    sys_msg = (
        "LSI 칩/펌웨어 불량 분석 시니어 엔지니어로서 한국어 마크다운으로 깊이 있게 답한다. "
        "지시된 모든 섹션을 순서대로 빠짐없이 작성한다(특히 권장 해결 단계·우회책 누락 금지). "
        "제공된 '과거 해결 사례'만 근거로 사용하고, 근거 키는 (LSI-49)처럼 본문에 인라인 인용한다. "
        "표면적 요약이 아니라 메커니즘 수준의 인과와 검증 방법까지 제시한다. "
        "한자/CJK 한자 금지 — 한글/영문/숫자/문장부호만."
    )
    payload = {
        "model": model_id, "max_tokens": max_tokens, "stream": True,
        "messages": [{"role": "system", "content": sys_msg},
                     {"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except Exception:
                continue
            piece = ev.get("choices", [{}])[0].get("delta", {}).get("content")
            if piece:
                yield piece


@app.get("/recommend/explain/stream")
def explain_stream(key: Optional[str] = None, summary: str = "", symptom: str = "",
                   chip: str = "", category: str = "", k: int = 4):
    """LLM 종합 분석 SSE 스트리밍 — 본문은 토큰 단위로, 인용 검증은 완료 시.

    이벤트: {type:delta,text} 반복 → {type:done,citations,dropped} | {type:error,message}.
    """
    st = _reco_state()
    query_rec = (st["by_key"].get(key) if key else None) or {
        "summary": summary, "symptom": symptom, "chip": chip, "category": category, "labels": []}
    result = st["reco"].recommend(query_rec, k=k, exclude_key=key)
    matches = result["matches"]
    coverage = result.get("coverage", bool(matches))

    def gen():
        if not matches or not coverage:
            yield f"data: {json.dumps({'type': 'done', 'citations': [], 'no_coverage': True}, ensure_ascii=False)}\n\n"
            return
        import re
        valid = {m["key"] for m in matches}
        # 근거 컨텍스트 강화: 매치를 전체 레코드(증상/디버깅 접근 포함)로 확장
        match_recs = [st["by_key"].get(m["key"], m) for m in matches]
        prompt = _explain_prompt_md(query_rec, match_recs)
        reasoning = os.getenv("RVP_EXPLAIN_REASONING", "0") == "1"
        acc: list[str] = []
        try:
            if ENGINE == "hermes":  # 스트리밍 미지원 → 1회 전송
                text = _HERMES.complete(prompt)
                acc.append(text)
                yield f"data: {json.dumps({'type': 'delta', 'text': text}, ensure_ascii=False)}\n\n"
            else:
                for delta in _llm_stream(prompt, reasoning=reasoning):
                    acc.append(delta)
                    yield f"data: {json.dumps({'type': 'delta', 'text': delta}, ensure_ascii=False)}\n\n"
            full = "".join(acc)
            cited = sorted({m for m in re.findall(r"LSI-\d+", full)} & valid)
            yield f"data: {json.dumps({'type': 'done', 'citations': cited}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/recommend")
def recommend(req: RecommendRequest):
    st = _reco_state()
    if req.key:
        rec = st["by_key"].get(req.key)
        if not rec:
            return {"error": f"이슈 {req.key} 없음"}
        query_rec = rec
    else:
        query_rec = {
            "summary": req.summary or "", "symptom": req.symptom or "",
            "chip": req.chip or "", "category": req.category or "",
            "labels": req.labels or [],
        }
    # 해결 이슈 키로 질의해도 자기 자신은 매치에서 제외
    result = st["reco"].recommend(query_rec, k=req.k, exclude_key=req.key)
    # 매치에 메타(생성일·FW) 보강 — 수명주기 신선도/경고 산출용
    for m in result["matches"]:
        src = st["by_key"].get(m.get("key"), {})
        m.setdefault("created", src.get("created", ""))
        m.setdefault("fw_version", src.get("fw_version", ""))
    # 고장모드 기사 주석(P2-4): 매치가 Known-Issue 기사에 속하면 묶어 노출하도록 표시
    try:
        import failure_modes
        failure_modes.annotate(result["matches"])
    except Exception:
        pass
    # 신선도·폐기 수명주기 주석(P2-5): 오래/폐기/대체 사례 경고 + 강등 정렬
    try:
        import lifecycle
        lifecycle.annotate(result["matches"])
    except Exception:
        pass
    out = {
        "query": {"key": query_rec.get("key"), "summary": query_rec.get("summary"),
                  "symptom": query_rec.get("symptom"), "chip": query_rec.get("chip"),
                  "category": query_rec.get("category"), "status": query_rec.get("status")},
        "matches": result["matches"],
        "proposal": result["proposal"],
        "coverage": result.get("coverage", bool(result["matches"])),
        "gate": result.get("gate"),
    }
    # 게이트 미통과 시 LLM 설명 생성 안 함 (무관 사례 기반 환각 방지)
    if req.explain and result["matches"] and out["coverage"]:
        ex = _llm_explain(query_rec, result["matches"])
        out["explanation"] = ex["markdown"]
        out["explanation_citations"] = ex["citations"]          # 검증된 인용 키
        if ex["dropped"]:
            out["explanation_dropped_citations"] = ex["dropped"]  # 환각으로 제거된 키
    return out


# ---------------------------------------------------------------------------
# RCA 자동 댓글 — HITL 승인 큐 (생성 → 대기 → 사람 승인 시에만 Jira 게시)
# ---------------------------------------------------------------------------
BOT_MARKER = "자동 근본원인 분석"  # preprocess.BOT_COMMENT_MARKER 와 동일(파싱 제외용)


def _md_to_jira(md: str) -> str:
    """게시 직전 마크다운 → Jira wiki markup 변환(api/2가 wiki를 렌더하므로).

    헤딩 #..# → h1.~h6., **굵게** → *굵게*, '- ' 글머리 → '* '. 본문/큐/미리보기는
    마크다운 정본을 유지하고, 게시 시점에만 변환한다.
    """
    import re
    lines = []
    for ln in md.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lines.append(f"h{len(m.group(1))}. {m.group(2)}")
        else:
            lines.append(re.sub(r"^(\s*)-\s+", r"\1* ", ln))  # 글머리 - → *
    s = "\n".join(lines)
    s = re.sub(r"`([^`\n]+)`", r"{{\1}}", s)              # 인라인 코드 → Jira monospace
    # 인라인 강조 마커(**, *)는 평문화한다. Jira 볼드 *x*는 닫는 *에 한글 조사가 붙으면
    # (*CRC*를) 렌더가 깨져 '*'가 그대로 노출되고, 변환 잔재 단독 '*'도 남는다. RCA 본문엔
    # 정상 '*'가 없으므로, 줄머리 글머리표('* ')만 남기고 그 외 '*'는 모두 제거한다.
    out = []
    for ln in s.split("\n"):
        m = re.match(r"^(\s*\*\s)(.*)$", ln)              # 글머리표 줄
        out.append((m.group(1) + m.group(2).replace("*", "")) if m else ln.replace("*", ""))
    s = "\n".join(out)
    # 이슈 키(LSI-123) monospace 래핑: 맨키워드는 Jira가 요약·상태 카드로 자동 확장돼
    # 참조가 길어진다. {{...}}로 감싸면 짧은 평문으로 렌더(카드 미확장). 중복 래핑 방지.
    s = re.sub(r"\{\{LSI-\d+\}\}|LSI-\d+",
               lambda m: m.group(0) if m.group(0).startswith("{{") else "{{" + m.group(0) + "}}", s)
    return s


def _strip_preamble(md: str) -> str:
    """첫 헤딩(###) 이전의 LLM 서두(예: '네, ...하겠습니다')를 제거."""
    idx = md.find("\n### ")
    if md.lstrip().startswith("### "):
        return md.strip()
    return (md[idx + 1:].strip() if idx != -1 else md.strip())


def _rca_comment_body(query_rec: dict, result: dict) -> str:
    """RCA 댓글 본문(마크다운 정본; 게시 시 _md_to_jira로 변환). 참조는 Jira ID만."""
    p = result.get("proposal") or {}
    matches = result.get("matches", [])
    cited = ", ".join(m["key"] for m in matches[:3])
    conf = p.get("confidence", 0)
    label = "높음" if (conf >= 0.67 and p.get("based_on_verified")) else "중간"
    return (
        f"🤖 **{BOT_MARKER}** (RCA-bot · 신뢰도 {label})\n\n"
        f"### 예상 근본원인\n{p.get('root_cause','')}\n\n"
        f"### 권장 해결책\n{p.get('resolution','')}\n\n"
        f"### 임시 우회책\n{p.get('workaround') or '—'}\n\n"
        f"참고 사례: {cited}\n\n"
        f"_과거 해결 이슈 기반 자동 분석 (사람 승인 후 게시)._")


class KeyBody(BaseModel):
    key: str


@app.post("/rca/draft")
def rca_draft(req: KeyBody):
    """미해결 이슈에 대한 RCA 댓글 초안 생성 → 승인 큐(pending)에 적재. Jira 쓰기 없음."""
    import datetime as _dt
    import rca_queue
    st = _reco_state()
    rec = st["by_key"].get(req.key)
    if not rec:
        return {"error": f"이슈 {req.key} 없음"}
    if rec.get("status") == RESOLVED_STATUS:
        return {"error": "이미 해결된 이슈입니다(미해결 이슈만 대상)."}
    result = st["reco"].recommend(rec, k=4, exclude_key=req.key)
    if not result["matches"] or not result.get("coverage"):
        return {"error": "유사 사례 없음(coverage 미통과) — 시니어 검토 필요, 자동 게시 대상 아님."}
    p = result["proposal"] or {}
    conf = p.get("confidence", 0)
    verified = bool(p.get("based_on_verified"))
    item = {
        "key": req.key, "summary": rec.get("summary", ""), "status": rec.get("status", ""),
        "body": _rca_comment_body(rec, result),
        "confidence": conf, "based_on_verified": verified,
        # 신뢰도 낮거나 미검증 근거면 반드시 사람 검토(조건부 HITL)
        "needs_review": (conf < 0.8) or (not verified),
        "based_on": p.get("based_on", ""),
        "source": "proposal",
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "state": "pending",
    }
    return {"item": rca_queue.upsert(item), "counts": rca_queue.counts()}


class AnalysisDraftBody(BaseModel):
    key: str
    analysis_md: str            # 화면에 표시된 시니어 종합 분석(마크다운)
    citations: list[str] = []   # 검증된 인용 키


@app.post("/rca/draft-from-analysis")
def rca_draft_from_analysis(req: AnalysisDraftBody):
    """시니어 종합 분석(LLM)을 RCA 댓글 본문으로 → 승인 큐. 생성물이라 항상 검토 필요."""
    import datetime as _dt
    import re
    import rca_queue
    st = _reco_state()
    rec = st["by_key"].get(req.key)
    if not rec:
        return {"error": f"이슈 {req.key} 없음"}
    if rec.get("status") == RESOLVED_STATUS:
        return {"error": "이미 해결된 이슈입니다(미해결 이슈만 대상)."}
    if not (req.analysis_md or "").strip():
        return {"error": "분석 본문이 비어 있습니다. 먼저 시니어 종합 분석을 생성하세요."}
    # 인용 검증: 본문/전달 키 ∩ KB 키 (환각 차단)
    valid = set(st["by_key"].keys())
    cited = sorted({k for k in (set(req.citations) | set(re.findall(r"LSI-\d+", req.analysis_md)))} & valid)
    cited_str = ", ".join(cited) if cited else "없음"
    body = (
        f"🤖 **{BOT_MARKER}** (RCA-bot · AI 심층 분석 · 근거: {cited_str})\n\n"
        f"{_strip_preamble(req.analysis_md)}\n\n"
        f"_과거 해결 이슈 기반 AI 심층 분석 (사람 승인 후 게시)._")
    item = {
        "key": req.key, "summary": rec.get("summary", ""), "status": rec.get("status", ""),
        "body": body, "confidence": None, "based_on_verified": False,
        "needs_review": True,  # LLM 생성물 → 항상 사람 검토
        "based_on": cited_str, "source": "analysis",
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "state": "pending",
    }
    return {"item": rca_queue.upsert(item), "counts": rca_queue.counts()}


@app.get("/rca/pending")
def rca_pending():
    import rca_queue
    return {"items": rca_queue.items("pending"), "counts": rca_queue.counts()}


class ApproveBody(BaseModel):
    key: str
    body: Optional[str] = None   # 사람이 수정한 본문(있으면 이걸 게시·기록)


@app.post("/rca/approve")
def rca_approve(req: ApproveBody):
    """HITL 게이트 — 사람 승인(+수정) 시에만 Jira에 게시. 수정 내용은 피드백에 기록."""
    import datetime as _dt
    import rca_queue
    import rca_feedback
    import knowledge_store
    item = rca_queue.get(req.key)
    if not item:
        return {"error": "큐에 없음"}
    if item.get("state") == "approved":
        return {"ok": True, "already": True, "item": item}
    original = item.get("body", "")
    final = (req.body if (req.body and req.body.strip()) else original)  # 마크다운 정본
    try:
        from jira_commenter import post_comment
        res = post_comment(req.key, _md_to_jira(final))  # 게시 직전 Jira wiki로 변환
        now = _dt.datetime.now().isoformat(timespec="seconds")
        comment_id = str(res.get("id", ""))
        updated = rca_queue.set_state(req.key, "approved", comment_id=comment_id,
                                      final_body=final, edited=(original.strip() != final.strip()))
        # 사람 수정 피드백 저장(성능 개선용) — 클래스 매칭을 위해 분류/템플릿 동봉
        rec = _reco_state()["by_key"].get(req.key, {})
        cited = sorted(set(re.findall(r"LSI-\d+", final)))
        rca_feedback.record(req.key, item.get("summary", ""), item.get("source", ""),
                            original, final, item.get("based_on", ""), now,
                            category=rec.get("category", ""),
                            template=template_key(item.get("summary", "")),
                            symptom=rec.get("symptom", ""), chip=rec.get("chip", ""))
        # 영속화: 큐레이션 지식을 git 추적 저장소에 적재(버전·백업·공유). 실패해도 게시는 유효.
        persisted = None
        try:
            persisted = knowledge_store.upsert(
                req.key, item.get("summary", ""), final,
                comment_id=comment_id, citations=cited,
                category=rec.get("category", ""), template=template_key(item.get("summary", "")),
                symptom=rec.get("symptom", ""), chip=rec.get("chip", ""),
                author=os.getenv("JIRA_EMAIL", ""), approved_at=now)
        except Exception:
            pass
        _RECO_STATE.clear()  # KB 환류 반영 — 다음 요청 시 큐레이션 항목 포함해 재빌드
        return {"ok": True, "item": updated, "edited": original.strip() != final.strip(),
                "counts": rca_queue.counts(), "feedback": rca_feedback.stats(),
                "persisted": bool(persisted), "knowledge": knowledge_store.stats()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/rca/feedback")
def rca_feedback_stats():
    import rca_feedback
    return {"stats": rca_feedback.stats(), "recent_edits": rca_feedback.recent_edits(5)}


# ---------------------------------------------------------------------------
# 지식 자산 영속화·환류 (P1-1)
# ---------------------------------------------------------------------------
@app.get("/knowledge/stats")
def knowledge_stats():
    """영속 큐레이션 지식 저장소 현황(건수·출처·저장 경로)."""
    import knowledge_store
    return {"knowledge": knowledge_store.stats()}


@app.get("/knowledge/quality")
def knowledge_quality():
    """인입 KB 품질 리포트(P1-2) — 상태별 필드 충족률 + 무음 실패 의심 키."""
    import quality_gate
    st = _reco_state()
    # 큐레이션(-rca) 항목 제외하고 원본 인입 KB만 평가
    base = [r for r in st["records"] if not r.get("curated")]
    return quality_gate.validate(base, resolved_status=RESOLVED_STATUS)


# ---------------------------------------------------------------------------
# 고장모드(Known-Issue) 기사 계층 (P2-4)
# ---------------------------------------------------------------------------
@app.get("/knowledge/clusters")
def knowledge_clusters(threshold: float = 0.80, min_size: int = 2):
    """해결 KB 임베딩 군집 → 고장모드 후보(중복 사례 묶음). 승격 검토용."""
    import failure_modes
    st = _reco_state()
    clusters = failure_modes.cluster_from_recommender(
        st["reco"], threshold=threshold, min_size=min_size)
    return {"threshold": threshold, "min_size": min_size,
            "count": len(clusters), "clusters": clusters, "stats": failure_modes.stats()}


class PromoteBody(BaseModel):
    title: str
    members: list[str]
    failure_summary: str = ""
    root_cause: str = ""
    resolution: str = ""
    workaround: str = ""
    chips: Optional[list] = None
    categories: Optional[list] = None
    article_id: str = ""             # 지정 시 기존 기사 갱신(멤버 합집합)


@app.post("/knowledge/known-issue")
def knowledge_promote(req: PromoteBody):
    """후보 군집(또는 선택 사례)을 정규 Known-Issue 기사로 승격/갱신."""
    import failure_modes
    st = _reco_state()
    by_key = st["by_key"]
    # 본문 미지정 시 대표(검증 우선) 사례에서 정규 내용 자동 채움 — 사람이 추후 정제
    rc, rs, wa = req.root_cause, req.resolution, req.workaround
    if not (rc or rs):
        rep = next((by_key[m] for m in req.members
                    if by_key.get(m, {}).get("verified")), None) \
            or next((by_key[m] for m in req.members if m in by_key), None)
        if rep:
            rc = rc or rep.get("root_cause", "")
            rs = rs or rep.get("resolution", "")
            wa = wa or rep.get("workaround", "")
    chips = req.chips or sorted({by_key[m].get("chip", "") for m in req.members
                                 if by_key.get(m, {}).get("chip")})
    cats = req.categories or sorted({by_key[m].get("category", "") for m in req.members
                                     if by_key.get(m, {}).get("category")})
    try:
        art = failure_modes.promote(
            title=req.title, members=req.members, failure_summary=req.failure_summary,
            root_cause=rc, resolution=rs, workaround=wa, chips=chips, categories=cats,
            author=os.getenv("JIRA_EMAIL", ""), article_id=req.article_id)
        return {"ok": True, "article": art, "stats": failure_modes.stats()}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.get("/knowledge/known-issues")
def knowledge_known_issues():
    """승격된 Known-Issue 기사 목록."""
    import failure_modes
    return {"articles": failure_modes.articles(), "stats": failure_modes.stats()}


# ---------------------------------------------------------------------------
# 신선도·폐기 수명주기 (P2-5)
# ---------------------------------------------------------------------------
class LifecycleBody(BaseModel):
    key: str
    state: str                       # active | deprecated | superseded
    superseded_by: str = ""
    reason: str = ""


@app.post("/knowledge/lifecycle")
def knowledge_lifecycle(req: LifecycleBody):
    """사례 수명주기 상태 설정(폐기/대체). 폐기·대체 사례는 추천에서 강등·경고."""
    import lifecycle
    try:
        info = lifecycle.set_state(req.key, req.state,
                                   superseded_by=req.superseded_by, reason=req.reason)
        _RECO_STATE.clear()
        return {"ok": True, "lifecycle": info, "stats": lifecycle.stats()}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.get("/knowledge/lifecycle/stats")
def knowledge_lifecycle_stats():
    import lifecycle
    return {"stats": lifecycle.stats()}


# ---------------------------------------------------------------------------
# 온톨로지 거버넌스 (P2-6)
# ---------------------------------------------------------------------------
@app.get("/knowledge/ontology")
def knowledge_ontology():
    """통제 어휘(동의어 그룹·통제 분류) 현황."""
    import ontology
    return {"vocab": ontology.vocab(), "stats": ontology.stats()}


@app.get("/knowledge/ontology/review")
def knowledge_ontology_review(top: int = 40):
    """통제 어휘에 없는 엔티티/분류를 빈도순으로 — canonical 승격 검토 큐."""
    import ontology
    st = _reco_state()
    base = [r for r in st["records"] if not r.get("curated")]
    return ontology.review(base, top=top)


class SynonymBody(BaseModel):
    canonical: str
    aliases: list[str] = []


@app.post("/knowledge/ontology/synonym")
def knowledge_ontology_synonym(req: SynonymBody):
    """동의어 그룹 추가/확장(alias→canonical). 다음 재빌드부터 엔티티 통합."""
    import ontology
    try:
        out = ontology.add_synonym(req.canonical, req.aliases)
        _RECO_STATE.clear()  # 정규화 반영을 위해 KB 재빌드
        return {"ok": True, "group": out, "stats": ontology.stats()}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


class CategoriesBody(BaseModel):
    categories: list[str]


@app.post("/knowledge/ontology/categories")
def knowledge_ontology_categories(req: CategoriesBody):
    """통제 분류 어휘 설정."""
    import ontology
    out = ontology.set_categories(req.categories)
    return {"ok": True, **out, "stats": ontology.stats()}


@app.post("/reco/reload")
def reco_reload():
    """추천 KB 캐시 무효화 — 새 큐레이션 지식을 서버 재시작 없이 즉시 반영."""
    _RECO_STATE.clear()
    st = _reco_state()
    return {"ok": True, "kb_size": len(st["resolved"]), "by_key": len(st["by_key"])}


@app.post("/knowledge/rebuild-from-jira")
def knowledge_rebuild_from_jira():
    """재해 복구/머신 간 동기화 — Jira 봇 댓글(조직 SoT)에서 지식 자산을 재구성한다.

    로컬 data/knowledge_store.json 유실 시에도 Jira에서 큐레이션 지식을 복원.
    """
    import knowledge_store
    try:
        out = knowledge_store.rebuild_from_jira(BOT_MARKER)
        _RECO_STATE.clear()  # 복원된 지식 즉시 반영
        return {"ok": True, **out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/rca/reject")
def rca_reject(req: KeyBody):
    import rca_queue
    updated = rca_queue.set_state(req.key, "rejected")
    return {"ok": bool(updated), "item": updated, "counts": rca_queue.counts()}


class JudgeScore(BaseModel):
    """수정사항 검증 채점(구조화 출력)."""
    score: int = Field(description="1~10 정수 — 근거 충실도·인용 정합·실행가능성 종합")
    passed: bool = Field(description="7점 이상이면 true")
    reasoning: str = Field(description="채점 근거 (한국어 1~2문장)")


def _judge_rca(ctx: str, body: str) -> "JudgeScore | None":
    """RCA 분석을 근거 사례 대비 채점 — RcaExplanation과 동일한 구조화 출력 경로."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        from agno.agent import Agent
        from agno.models.openrouter import OpenRouter
        jm = os.getenv("RVP_JUDGE_MODEL") or os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        agent = Agent(
            model=OpenRouter(id=jm, api_key=api_key, base_url=base),
            output_schema=JudgeScore, use_json_mode=True, markdown=False, telemetry=False,
            instructions=["LSI 불량 분석 RCA 채점관. 제공된 근거 사례에 비추어 평가한다.",
                          "기준: 근거 충실도(날조·환각 감점), 인용 정합, 권장 해결 단계의 구체성·실행가능성, 한자 금지 준수.",
                          "한국어로 간단히 채점한다."])
        out = agent.run(input=f"## 근거 사례\n{ctx}\n\n## 채점할 RCA 분석\n{body}")
        return out.content if isinstance(out.content, JudgeScore) else None
    except Exception:
        return None


class ValidateBody(BaseModel):
    key: str
    body: Optional[str] = None   # 현재(수정된) 본문; 없으면 큐의 원본


@app.post("/rca/validate")
def rca_validate(req: ValidateBody):
    """수정사항 검증 — (1) 가드레일: 인용 키 ⊆ KB, 한자/CJK, 빈값  (2) Agent-as-Judge:
    근거 충실도·인용 정합·실행가능성 1~10 채점. 승인 전 품질 확인용(차단 아님)."""
    import re
    import rca_queue
    st = _reco_state()
    body = (req.body if (req.body and req.body.strip()) else (rca_queue.get(req.key) or {}).get("body", "")).strip()
    if not body:
        return {"error": "검증할 본문이 없습니다."}
    valid = set(st["by_key"].keys())
    cited = set(re.findall(r"LSI-\d+", body))
    invalid = sorted(c for c in cited if c not in valid)
    vr = validate_and_fix(body)
    out = {
        "citations_ok": not invalid, "invalid_citations": invalid,
        "lang_ok": bool(vr.ok), "non_empty": True,
    }
    # LLM 판정 — 구조화 출력(검증된 use_json_mode 경로)으로 근거 충실도·실행가능성 채점
    rec = st["by_key"].get(req.key, {})
    cited_recs = [st["by_key"][k] for k in cited if k in st["by_key"]]
    ctx = (f"미해결 이슈: {rec.get('summary','')}\n증상: {rec.get('symptom','')}\n\n근거 사례:\n"
           + "\n".join(f"[{r['key']}] 근본원인: {r.get('root_cause','')} / 해결: {r.get('resolution','')}"
                       for r in cited_recs))
    jr = _judge_rca(ctx, body)
    if jr is not None:
        out["judge_score"] = jr.score
        out["judge_passed"] = jr.passed
        out["judge_reasoning"] = jr.reasoning[:400]
    return out


if __name__ == "__main__":
    import uvicorn
    import os as _os
    uvicorn.run("server:app", host="127.0.0.1", port=int(_os.getenv("RVP_PORT", "8001")), reload=False)
