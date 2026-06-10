"""FastAPI server exposing the RVP support agent.

Endpoints:
    POST /chat            -> non-streaming JSON answer
    POST /chat/stream     -> Server-Sent Events stream
    GET  /memories        -> list a user's stored memories
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
from pydantic import BaseModel

from agent import build_agent  # noqa: E402
from lang_validator import validate, validate_and_fix  # noqa: E402
from preprocess import parse_issue  # noqa: E402
from recommender import Recommender, template_key  # noqa: E402

# 채팅/설명 생성 엔진 선택: agno(OpenRouter) | hermes(Hermes Agent CLI)
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
        "reco": Recommender(resolved, method="hybrid"),
    })
    return _RECO_STATE

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    user_id: str = "web-user"
    session_id: str = "web-session"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(req: ChatRequest):
    if ENGINE == "hermes":
        raw = _HERMES.run(req.message, user_id=req.user_id, session_id=req.session_id)
    else:
        agent = build_agent(user_id=req.user_id, session_id=req.session_id)
        resp = agent.run(req.message)
        raw = resp.content if hasattr(resp, "content") else str(resp)
    vr = validate_and_fix(raw)
    return {
        "answer": vr.rewritten if (not vr.ok and vr.rewritten) else raw,
        "language_check": {"ok": vr.ok, "violations": vr.violations},
        "user_id": req.user_id,
        "session_id": req.session_id,
    }


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    # hermes CLI는 토큰 스트리밍이 없으므로 완성된 답변을 delta 1건으로 전송
    if ENGINE == "hermes":
        def hermes_gen():
            raw = _HERMES.run(req.message, user_id=req.user_id, session_id=req.session_id)
            yield f"data: {json.dumps({'delta': raw})}\n\n"
            vr = validate_and_fix(raw) if raw else None
            if vr and not vr.ok and vr.rewritten:
                yield f"data: {json.dumps({'replace': vr.rewritten, 'violations': vr.violations})}\n\n"
            elif vr:
                yield f"data: {json.dumps({'language_ok': vr.ok, 'violations': vr.violations})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(hermes_gen(), media_type="text/event-stream")

    agent = build_agent(user_id=req.user_id, session_id=req.session_id)

    def event_gen():
        buf = []
        for chunk in agent.run(req.message, stream=True):
            text = getattr(chunk, "content", None)
            if text:
                buf.append(text)
                yield f"data: {json.dumps({'delta': text})}\n\n"
        # Post-stream language compliance check + fix
        full = "".join(buf)
        vr = validate_and_fix(full) if full else None
        if vr and not vr.ok and vr.rewritten:
            yield f"data: {json.dumps({'replace': vr.rewritten, 'violations': vr.violations})}\n\n"
        elif vr:
            yield f"data: {json.dumps({'language_ok': vr.ok, 'violations': vr.violations})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/memories")
def memories(user_id: str = "web-user"):
    if ENGINE == "hermes":
        # hermes 엔진은 세션 컨텍스트로 대화를 유지하며, agno 식 user memory는 없음
        return {"user_id": user_id, "memories": []}
    agent = build_agent(user_id=user_id, session_id="memories-readonly")
    mems = agent.get_user_memories(user_id=user_id) if hasattr(agent, "get_user_memories") else []
    return {"user_id": user_id, "memories": [getattr(m, "memory", str(m)) for m in (mems or [])]}


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
    result = st["reco"].recommend(query_rec, k=req.k)
    out = {
        "query": {"key": query_rec.get("key"), "summary": query_rec.get("summary"),
                  "symptom": query_rec.get("symptom"), "chip": query_rec.get("chip"),
                  "category": query_rec.get("category"), "status": query_rec.get("status")},
        "matches": result["matches"],
        "proposal": result["proposal"],
        "coverage": bool(result["matches"]),
    }
    if req.explain and result["matches"]:
        out["explanation"] = _llm_explain(query_rec, result["matches"])
    return out


if __name__ == "__main__":
    import uvicorn
    import os as _os
    uvicorn.run("server:app", host="127.0.0.1", port=int(_os.getenv("RVP_PORT", "8001")), reload=False)
