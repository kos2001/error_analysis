"""자기 개선 제안 큐 (L3) — loop가 '지식 변경'을 사람에게 제안만 한다.

배경(자기 개선 loop L3):
  loop가 측정·진단(L1)·검증된 파라미터(L2)를 넘어 '지식 자체의 변경'을 다룰 때는
  절대 자동 실행하면 안 된다(Jira/KB 오염 위험). 대신 신호에서 actionable 제안을
  도출해 **사람 검토 큐**에 쌓고, 사람이 기존 HITL 엔드포인트로 실행한다.

상태(git 추적 data/improve_queue.json): open | done | dismissed | superseded.
  - sync(generated): 신호로 생성한 제안을 병합. 서명(type|target)으로 dedup하며
    기존 상태(done/dismissed)는 보존 — 사람이 거부/완료한 제안을 되살리지 않는다.
  - superseded: 이번 생성분에 없어진 open 제안. **회수가 없으면 큐는 단조 증가한다** —
    근거를 잃은 제안(사례가 이미 승격됨·군집이 흩어짐·생성 규칙이 바뀜)이 그대로
    남아 사람이 보는 목록을 덮는다. 실제로 생성 규칙을 고친 뒤에도 옛 제안 51건이
    남아 수정이 사용자에게 전달되지 않았다. 지우지 않고 상태로 남기는 이유는
    "왜 사라졌나" 를 추적할 수 있어야 하기 때문이다.
"""
from __future__ import annotations

from pathlib import Path

from json_store import read_json, write_json_atomic, now_iso as _now

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "improve_queue.json"
STATES = ("open", "done", "dismissed", "superseded")


def _load() -> list:
    d = read_json(STORE_FILE, [])
    return d if isinstance(d, list) else []


def _save(items: list) -> None:
    write_json_atomic(STORE_FILE, items)


def _sig(s: dict) -> str:
    return f"{s.get('type')}|{s.get('target')}"


def _next_id(items: list) -> int:
    """가장 큰 일련번호 + 1. len(items) 기반이면 항목이 빠졌을 때 id가 겹친다."""
    n = 0
    for it in items:
        sid = str(it.get("id", ""))
        if sid.startswith("S-") and sid[2:].isdigit():
            n = max(n, int(sid[2:]))
    return n + 1


def sync(generated: list[dict], *, prune: bool = True) -> dict:
    """생성된 제안을 큐에 병합 — 신규는 open, 기존 서명은 상태 보존(거부/완료 존중).

    refreshed: 기존 open 제안의 근거(rationale/evidence)는 최신으로 갱신.
    prune:     이번 생성분에 없는 open 제안을 superseded 로 회수. **전량 생성일 때만**
               참이어야 한다 — 일부만 생성한 결과로 부르면 멀쩡한 제안이 회수된다.
    """
    items = _load()
    by_sig = {_sig(it): it for it in items}
    seen: set[str] = set()
    added, seq = 0, _next_id(items)
    for g in generated:
        sig = _sig(g)
        seen.add(sig)
        if sig in by_sig:
            cur = by_sig[sig]
            if cur.get("state") == "open":             # 거부/완료는 건드리지 않음
                cur["rationale"], cur["evidence"] = g.get("rationale", ""), g.get("evidence")
                cur["priority"], cur["updated_at"] = g.get("priority", cur.get("priority")), _now()
        else:
            g.update({"id": f"S-{seq}", "state": "open",
                      "created_at": _now(), "updated_at": _now()})
            items.append(g)
            by_sig[sig] = g
            seq += 1
            added += 1

    superseded = 0
    if prune and generated:      # 생성이 통째로 실패해 빈 목록이면 회수하지 않는다
        for it in items:
            if it.get("state") == "open" and _sig(it) not in seen:
                it["state"], it["updated_at"] = "superseded", _now()
                it["superseded_reason"] = "최신 생성분에 더 이상 나타나지 않음"
                superseded += 1
    _save(items)
    return {"added": added, "superseded": superseded, "counts": counts()}


def items(state: str = "") -> list[dict]:
    its = _load()
    return [it for it in its if it.get("state") == state] if state else its


def set_state(sid: str, state: str) -> dict | None:
    if state not in STATES:
        raise ValueError(f"state는 {STATES} 중 하나여야 함: {state!r}")
    its = _load()
    for it in its:
        if it.get("id") == sid:
            it["state"] = state
            it["updated_at"] = _now()
            _save(its)
            return it
    return None


def counts() -> dict:
    from collections import Counter
    c = Counter(it.get("state") for it in _load())
    return {s: c.get(s, 0) for s in STATES}


def stats() -> dict:
    return {"counts": counts(), "store_path": str(STORE_FILE.relative_to(ROOT))}
