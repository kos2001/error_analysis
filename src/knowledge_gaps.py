"""지식 공백 관측성 — "자주 묻지만 사례가 없는" 영역을 드러낸다.

배경(지식자산화 갭 P3-8):
  coverage 게이트 미통과("유사 사례 없음")·저신뢰 질의가 로깅·집계되지 않아,
  어느 고장군이 얇은지 안 보였다 → 문서화 우선순위 근거가 없었다.

구성(git 추적 data/knowledge_gaps.json):
  - record(query, reason): 공백 이벤트 적재(no_coverage | low_confidence).
  - report(): 템플릿/분류/칩별 빈도 집계 → 자기 개선 loop의 ①측정 입력 +
    "지식 공백 대시보드"(가장 자주 질의되나 사례 없는 영역).
"""
from __future__ import annotations

import threading

import os
from collections import Counter
from pathlib import Path

from json_store import read_json, write_json_atomic, now_iso

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "knowledge_gaps.json"
MAX_EVENTS = int(os.getenv("RVP_GAPS_MAX_EVENTS", "5000"))  # 무한 증가 방지(오래된 것부터 제거)


def _load() -> list:
    d = read_json(STORE_FILE, [])
    return d if isinstance(d, list) else []


def _save(events: list) -> None:
    write_json_atomic(STORE_FILE, events)


_LOCK = threading.Lock()


def record(query: dict, *, reason: str = "no_coverage", template: str = "",
           top_score: float | None = None) -> None:
    """공백 이벤트 적재. 동일 (template|summary, reason)의 잦은 반복도 모두 남겨 빈도 집계."""
    ev = {
        "summary": query.get("summary", ""), "symptom": query.get("symptom", ""),
        "chip": query.get("chip", ""), "category": query.get("category", ""),
        "template": template or "", "reason": reason, "top_score": top_score,
        "key": query.get("key", ""),
        "created_at": now_iso(),
    }
    # 락 없이는 read-modify-write 가 겹쳐 동시 요청이 서로의 이벤트를 덮어쓴다
    # (뒤 쓰기가 앞 이벤트를 통째로 지운다). 이 파일은 자기개선 loop 의 측정 입력이라
    # 조용한 유실이 곧 잘못된 진단이 된다.
    with _LOCK:
        events = _load()
        events.append(ev)
        if len(events) > MAX_EVENTS:
            events = events[-MAX_EVENTS:]
        _save(events)


def report(top: int = 20) -> dict:
    """템플릿/분류/칩별 공백 빈도 집계 + 최근 샘플."""
    events = _load()
    by_template = Counter(e.get("template") or e.get("summary", "")[:40] for e in events)
    by_category = Counter(e.get("category", "") for e in events if e.get("category"))
    by_chip = Counter(e.get("chip", "") for e in events if e.get("chip"))
    by_reason = Counter(e.get("reason", "") for e in events)
    return {
        "total_gap_events": len(events),
        "by_reason": dict(by_reason),
        "top_underserved_templates": [{"template": t, "count": n} for t, n in by_template.most_common(top)],
        "by_category": dict(by_category.most_common(top)),
        "by_chip": dict(by_chip.most_common(top)),
        "recent": events[-5:],
    }


def stats() -> dict:
    events = _load()
    return {"total_gap_events": len(events),
            "distinct_templates": len({e.get("template") or e.get("summary", "")[:40] for e in events}),
            "store_path": str(STORE_FILE.relative_to(ROOT))}
