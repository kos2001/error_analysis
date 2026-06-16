"""VOC(Voice of Customer) 저장소 — 이 서비스에 대한 사용자 피드백 수집.

추천/RCA 품질 피드백(reco_feedback)과 별개로, **서비스 자체**에 대한 의견
(버그·개선요청·칭찬·문의)을 모은다. git 추적 data/voc.json(버전·공유).
상태: open → triaged | resolved | wont_fix.
"""
from __future__ import annotations

from pathlib import Path

from json_store import read_json, write_json_atomic, now_iso

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "voc.json"

CATEGORIES = ("bug", "improvement", "praise", "question", "other")
STATES = ("open", "triaged", "resolved", "wont_fix")


def _load() -> list:
    d = read_json(STORE_FILE, [])
    return d if isinstance(d, list) else []


def _save(items: list) -> None:
    write_json_atomic(STORE_FILE, items)


def add(category: str, message: str, *, author: str = "", context: str = "") -> dict:
    """VOC 1건 등록. category는 CATEGORIES 중 하나(아니면 'other'), message 필수."""
    if not (message or "").strip():
        raise ValueError("message 필수")
    cat = category if category in CATEGORIES else "other"
    items = _load()
    item = {
        "id": f"VOC-{len(items) + 1}",
        "category": cat, "message": message.strip()[:4000],
        "author": (author or "").strip()[:80], "context": (context or "").strip()[:200],
        "state": "open", "created_at": now_iso(), "updated_at": now_iso(),
    }
    items.append(item)
    _save(items)
    return item


def set_state(voc_id: str, state: str) -> dict | None:
    if state not in STATES:
        raise ValueError(f"state는 {STATES} 중 하나여야 함: {state!r}")
    items = _load()
    for it in items:
        if it.get("id") == voc_id:
            it["state"] = state
            it["updated_at"] = now_iso()
            _save(items)
            return it
    return None


def items(state: str = "") -> list[dict]:
    its = sorted(_load(), key=lambda x: x.get("created_at", ""), reverse=True)
    return [it for it in its if it.get("state") == state] if state else its


def stats() -> dict:
    from collections import Counter
    its = _load()
    return {
        "total": len(its),
        "by_category": dict(Counter(it.get("category") for it in its)),
        "by_state": dict(Counter(it.get("state") for it in its)),
        "open": sum(1 for it in its if it.get("state") == "open"),
    }
