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
from pydantic import BaseModel

from lang_validator import validate_and_fix  # noqa: E402
from preprocess import parse_issue  # noqa: E402
from recommender import Recommender, template_key  # noqa: E402

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


def _llm_explain(query_rec: dict, matches: list[dict]) -> str:
    """상위 매치들을 근거로 LLM이 종합 root-cause/해결책을 한국어로 작성."""
    import requests
    cases = "\n\n".join(
        f"[{m['key']}] {m['summary']}\n근본원인: {m['root_cause']}\n해결책: {m['resolution']}\n우회책: {m['workaround']}"
        for m in matches)
    prompt = (
        "당신은 LSI 칩/펌웨어 고장 분석 시니어 엔지니어입니다. 아래는 새로 들어온 미해결 이슈와, "
        "과거에 해결된 유사 이슈들입니다. 과거 사례를 근거로 이 미해결 이슈의 "
        "**예상 근본원인**과 **권장 해결 단계**, **임시 우회책**을 한국어로 간결히 제시하세요. "
        "반드시 근거가 된 과거 이슈 키(예: LSI-6)를 인용하세요. 한자/CJK 한자 금지.\n\n"
        f"## 미해결 이슈\n{query_rec.get('summary','')}\n증상: {query_rec.get('symptom','')}\n"
        f"칩: {query_rec.get('chip','')} / 분류: {query_rec.get('category','')}\n\n"
        f"## 과거 해결 사례\n{cases}\n")
    if ENGINE == "hermes":
        try:
            raw = _HERMES.complete(prompt)
            vr = validate_and_fix(raw)
            return vr.rewritten if (not vr.ok and vr.rewritten) else raw
        except Exception as e:
            return f"(LLM 설명 생성 실패: {e})"
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return ""
    model = os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    try:
        r = requests.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {api_key}"},
                          json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.2}, timeout=60)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
        vr = validate_and_fix(raw)
        return vr.rewritten if (not vr.ok and vr.rewritten) else raw
    except Exception as e:
        return f"(LLM 설명 생성 실패: {e})"


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
        out["explanation"] = _llm_explain(query_rec, result["matches"])
    return out


if __name__ == "__main__":
    import uvicorn
    import os as _os
    uvicorn.run("server:app", host="127.0.0.1", port=int(_os.getenv("RVP_PORT", "8001")), reload=False)
