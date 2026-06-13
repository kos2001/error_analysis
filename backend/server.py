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


if __name__ == "__main__":
    import uvicorn
    import os as _os
    uvicorn.run("server:app", host="127.0.0.1", port=int(_os.getenv("RVP_PORT", "8001")), reload=False)
