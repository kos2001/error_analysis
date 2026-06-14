"""결과·효능 추적 (자기개선 #1) — 게시한 RCA가 현장에서 실제로 해결됐는지.

배경(고려사항 #1):
  P1-3은 "추천이 도움됐나(클릭)"만 잡았다. 궁극의 ground truth는 "추천한 해결책이
  현장에 적용되어 실제로 문제를 해결했는가"다. Jira 상태 전이로 이를 근사한다.

방법:
  승인·게시된 RCA(knowledge_store의 source_issue+comment_id)의 현재 Jira 상태를 조회.
  - 게시 후 '완료'   → resolved_after_rca (효능 양성 신호)
  - 아직 미해결      → pending
  저장: data/outcomes.json (git 추적). report()로 효능율 집계 + L3/추천 환류.

정직한 한계: "게시 후 해결"은 상관이지 인과가 아니다(다른 경로로 해결됐을 수 있음).
재오픈(해결→재발)은 상태 이력이 필요해 본 MVP는 현재 상태만 본다. 그래도 가장
싸게 얻는 실데이터 신호다.
"""
from __future__ import annotations

from pathlib import Path

from json_store import read_json, write_json_atomic, now_iso

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "outcomes.json"
RESOLVED_STATUS = "완료"


def _load() -> dict:
    d = read_json(STORE_FILE, {})
    return d if isinstance(d, dict) else {}


def _save(d: dict) -> None:
    write_json_atomic(STORE_FILE, d)


def refresh(project: str | None = None) -> dict:
    """게시된 RCA 대상 이슈의 현재 Jira 상태를 조회해 효능 결과를 갱신."""
    import os
    import knowledge_store
    from ingest import jira_session, _all_keys

    s, base = jira_session()
    proj = project or os.getenv("JIRA_PROJECT_KEY", "LSI")
    statuses = dict(_all_keys(s, base, proj))   # {key: status_name} (1회 페이지네이션)

    # 게시된 RCA = comment_id 있는 큐레이션 지식의 source_issue
    targets = {}
    for r in knowledge_store.records():
        si, cid = r.get("source_issue"), r.get("comment_id")
        if si and cid:
            targets[si] = {"comment_id": str(cid), "approved_at": r.get("approved_at", "")}

    data = _load()
    now = now_iso()
    updated = 0
    for si, meta in targets.items():
        cur = statuses.get(si, "")
        outcome = ("resolved_after_rca" if cur == RESOLVED_STATUS
                   else "pending" if cur else "unknown")
        prev = data.get(si, {})
        data[si] = {
            "comment_id": meta["comment_id"], "approved_at": meta["approved_at"],
            "status_now": cur, "outcome": outcome,
            "first_checked_at": prev.get("first_checked_at", now), "checked_at": now,
        }
        updated += 1
    _save(data)
    return {"tracked": updated, **report()}


def efficacy_of(source_issue: str) -> dict:
    return _load().get(source_issue, {})


def report() -> dict:
    """효능 집계 — resolved_after_rca / pending 비율."""
    from collections import Counter
    data = _load()
    c = Counter(v.get("outcome") for v in data.values())
    resolved = c.get("resolved_after_rca", 0)
    pending = c.get("pending", 0)
    denom = resolved + pending
    return {
        "total_tracked": len(data),
        "resolved_after_rca": resolved,
        "pending": pending,
        "unknown": c.get("unknown", 0),
        "efficacy_rate": round(resolved / denom, 3) if denom else None,
        "store_path": str(STORE_FILE.relative_to(ROOT)),
    }


def resolved_source_issues() -> set:
    """효능 양성(게시 후 해결)으로 확인된 원본 이슈 키 집합 — 추천 가중·승격 환류용."""
    return {si for si, v in _load().items() if v.get("outcome") == "resolved_after_rca"}
