"""RCA 사람 수정 피드백 저장소 — 승인 시 사람이 고친 내용을 모아 성능 개선에 쓴다.

저장: tmp_db/rca_feedback.json (append-only 리스트). 각 항목:
  {key, summary, source, original_body, final_body, edited(bool), citations, approved_at}

활용:
  - recent_edits(): 사람이 수정한 최근 사례 → explain 프롬프트의 few-shot 가이드
    (사람이 선호하는 문체·수준·정정 방향을 LLM이 모방).
  - 전체 (draft → 사람 수정) 쌍은 향후 평가/파인튜닝 데이터로도 사용.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDBACK_FILE = ROOT / "tmp_db" / "rca_feedback.json"


def _load() -> list:
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def record(key: str, summary: str, source: str, original_body: str,
           final_body: str, citations: list, approved_at: str,
           category: str = "", template: str = "", symptom: str = "", chip: str = "") -> dict:
    data = _load()
    entry = {
        "key": key, "summary": summary, "source": source,
        "category": category, "template": template,   # 클래스 매칭용(고장 분류/템플릿)
        "symptom": symptom, "chip": chip,              # KB 환류용
        "original_body": original_body, "final_body": final_body,
        "edited": original_body.strip() != final_body.strip(),
        "citations": citations, "approved_at": approved_at,
    }
    data.append(entry)
    FEEDBACK_FILE.parent.mkdir(exist_ok=True)
    FEEDBACK_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def relevant_edits(category: str = "", template: str = "", n: int = 2, max_len: int = 500) -> list[dict]:
    """현재 질의와 **같은 고장 클래스**(동일 템플릿 우선, 없으면 동일 분류)의 사람 수정만
    최근순 n건. 무관 클래스 예시 주입을 막아 이슈별 차이를 존중한다(상한 n으로 증가 방지)."""
    edits = [e for e in _load() if e.get("edited")]
    # 1순위: 동일 템플릿, 2순위: 동일 분류. 매칭 없으면 빈 리스트(전역 폴백 안 함).
    matched = [e for e in edits if template and e.get("template") == template]
    if not matched and category:
        matched = [e for e in edits if e.get("category") == category]
    return [{"key": e["key"], "summary": e.get("summary", ""),
             "final_body": (e.get("final_body") or "")[:max_len]} for e in matched[-n:]]


def _section(md: str, *titles: str) -> str:
    """마크다운 '### ...제목...' 섹션 본문 추출(다음 ### 또는 끝까지)."""
    import re
    for t in titles:
        m = re.search(rf"###[^\n]*{re.escape(t)}[^\n]*\n+(.+?)(?=\n###|\Z)", md, flags=re.S)
        if m:
            return m.group(1).strip()
    return ""


def kb_records() -> list[dict]:
    """승인된 사람-검토 RCA → 큐레이션 KB 레코드(추천기 소스 환류).

    봇 댓글은 parse_issue에서 제외되므로, 사람이 승인/수정한 분석을 별도 채널로
    KB에 주입한다. 같은 클래스의 향후 검색·제안을 직접 개선(verified=True 가중).
    키는 '{원본}-rca'로 원본과 분리.
    """
    out, seen = [], set()
    for e in _load():
        rk = f"{e['key']}-rca"
        if rk in seen:
            continue
        seen.add(rk)
        body = e.get("final_body", "")
        rc = _section(body, "예상 근본원인", "근본 원인", "근본원인") or body[:600]
        res = _section(body, "권장 해결", "적용 해결", "해결책")
        wa = _section(body, "임시 우회책", "우회책")
        out.append({
            "key": rk, "summary": e.get("summary", ""), "status": "완료",
            "priority": "", "labels": [], "components": [],
            "chip": e.get("chip", ""), "category": e.get("category", ""),
            "severity": "", "customer": "", "fw_version": "",
            "symptom": e.get("symptom", ""), "debug_approach": "",
            "root_cause": rc, "resolution": res, "workaround": wa,
            "investigation": "", "verified": True, "context_text": body,
            "entities": [], "curated": True,
        })
    return out


def stats() -> dict:
    data = _load()
    return {"total": len(data), "edited": sum(1 for e in data if e.get("edited"))}
