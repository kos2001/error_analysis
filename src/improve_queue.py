"""자기 개선 제안 큐 (L3) — loop가 '지식 변경'을 사람에게 제안만 한다.

배경(자기 개선 loop L3):
  loop가 측정·진단(L1)·검증된 파라미터(L2)를 넘어 '지식 자체의 변경'을 다룰 때는
  절대 자동 실행하면 안 된다(Jira/KB 오염 위험). 대신 신호에서 actionable 제안을
  도출해 **사람 검토 큐**에 쌓고, 사람이 기존 HITL 엔드포인트로 실행한다.

상태(git 추적 data/improve_queue.json): open | done | dismissed.
  - sync(generated): 신호로 생성한 제안을 병합. 서명(type|target)으로 dedup하며
    기존 상태(done/dismissed)는 보존 — 사람이 거부/완료한 제안을 되살리지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "improve_queue.json"
STATES = ("open", "done", "dismissed")


def _load() -> list:
    if STORE_FILE.exists():
        try:
            d = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
        except Exception:
            pass
    return []


def _save(items: list) -> None:
    STORE_FILE.parent.mkdir(exist_ok=True)
    tmp = STORE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_FILE)


def _sig(s: dict) -> str:
    return f"{s.get('type')}|{s.get('target')}"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def sync(generated: list[dict]) -> dict:
    """생성된 제안을 큐에 병합 — 신규는 open, 기존 서명은 상태 보존(거부/완료 존중).

    refreshed: 기존 open 제안의 근거(rationale/evidence)는 최신으로 갱신.
    """
    items = _load()
    by_sig = {_sig(it): it for it in items}
    added = 0
    for g in generated:
        sig = _sig(g)
        if sig in by_sig:
            cur = by_sig[sig]
            if cur.get("state") == "open":             # 거부/완료는 건드리지 않음
                cur["rationale"], cur["evidence"] = g.get("rationale", ""), g.get("evidence")
                cur["priority"], cur["updated_at"] = g.get("priority", cur.get("priority")), _now()
        else:
            g.update({"id": f"S-{len(items) + 1}", "state": "open",
                      "created_at": _now(), "updated_at": _now()})
            items.append(g)               # len(items) 증가가 곧 다음 일련번호
            added += 1
    _save(items)
    return {"added": added, "counts": counts()}


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
