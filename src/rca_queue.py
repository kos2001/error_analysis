"""RCA 댓글 HITL 승인 큐 — 초안을 보관하고, 사람 승인 시에만 Jira에 게시한다.

부작용(Jira 쓰기)은 approve 시점에만 발생. 상태: pending → approved | rejected.
저장: tmp_db/rca_pending.json (단일 책임: 큐 영속화/조회/상태전이).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "tmp_db" / "rca_pending.json"


def _load() -> dict:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    QUEUE_FILE.parent.mkdir(exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert(item: dict) -> dict:
    """초안 추가/갱신 (key 기준). 이미 approved면 덮어쓰지 않음."""
    data = _load()
    prev = data.get(item["key"])
    if prev and prev.get("state") == "approved":
        return prev
    item.setdefault("state", "pending")
    data[item["key"]] = item
    _save(data)
    return item


def get(key: str) -> dict | None:
    return _load().get(key)


def set_state(key: str, state: str, **extra) -> dict | None:
    data = _load()
    if key not in data:
        return None
    data[key].update(state=state, **extra)
    _save(data)
    return data[key]


def items(state: str | None = None) -> list[dict]:
    out = list(_load().values())
    if state:
        out = [x for x in out if x.get("state") == state]
    return sorted(out, key=lambda x: x.get("created_at", ""))


def counts() -> dict:
    from collections import Counter
    c = Counter(x.get("state", "pending") for x in _load().values())
    return {"pending": c.get("pending", 0), "approved": c.get("approved", 0),
            "rejected": c.get("rejected", 0)}
