"""지식 신선도·폐기 수명주기 — 오래되거나 무효가 된 사례를 구분한다.

배경(지식자산화 갭 P2-5):
  FW/칩이 진화하는데 사례에 유효기간·대체(superseded-by)·신뢰 감쇠가 없어,
  오래되어 무효일 수 있는 근본원인이 최신과 동급으로 추천됐다.

구성:
  - 신선도(freshness): created 날짜 기반 시간 감쇠 점수(automatic). 추천 표시·정렬 보조.
  - 수명주기 상태(human-set, 영속): active | deprecated | superseded.
    superseded는 대체 사례(superseded_by)를 링크. git 추적 data/lifecycle.json.
  - annotate: 추천 매치에 freshness/state/경고를 주석 → UI가 "FW X 기준·YYYY",
    "폐기/대체됨" 배지로 노출. 폐기/대체 사례는 추천에서 강등(penalty)한다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "lifecycle.json"
STATES = ("active", "deprecated", "superseded")

# 신선도 반감기(일) — 이 일수마다 freshness가 절반으로. 기본 540일(약 1.5년).
HALFLIFE_DAYS = int(os.getenv("RVP_FRESHNESS_HALFLIFE_DAYS", "540"))


def _load() -> dict:
    if STORE_FILE.exists():
        try:
            d = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _save(d: dict) -> None:
    STORE_FILE.parent.mkdir(exist_ok=True)
    tmp = STORE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_FILE)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _parse_date(s: str):
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00").split(".")[0].split("+")[0])
    except Exception:
        return None


def freshness(created: str, *, now: _dt.datetime | None = None) -> float | None:
    """created로부터 경과에 따른 0~1 신선도(지수 감쇠). 날짜 파싱 불가 시 None."""
    d = _parse_date(created)
    if not d:
        return None
    now = now or _dt.datetime.now()
    age_days = max((now - d).days, 0)
    return round(0.5 ** (age_days / HALFLIFE_DAYS), 3)


def set_state(key: str, state: str, *, superseded_by: str = "", reason: str = "") -> dict:
    """이슈 수명주기 상태 설정. superseded면 대체 사례(superseded_by) 권장."""
    if state not in STATES:
        raise ValueError(f"state는 {STATES} 중 하나여야 함: {state!r}")
    if not key:
        raise ValueError("key 필수")
    d = _load()
    if state == "active":
        d.pop(key, None)            # active는 기본값 — 레코드 제거(저장소 간결 유지)
    else:
        d[key] = {"state": state, "superseded_by": superseded_by or "",
                  "reason": reason or "", "updated_at": _now()}
    _save(d)
    return {"key": key, **(d.get(key) or {"state": "active"})}


def state_of(key: str) -> dict:
    return _load().get(key) or {"state": "active"}


def annotate(matches: list[dict]) -> list[dict]:
    """매치에 freshness/lifecycle/경고 주석. 폐기·대체 사례는 강등(penalty) 정렬."""
    if not matches:
        return matches
    lc = _load()
    for m in matches:
        info = lc.get(m.get("key")) or {}
        state = info.get("state", "active")
        fr = freshness(m.get("created", ""))
        warn = []
        if state == "deprecated":
            warn.append("폐기됨" + (f" ({info['reason']})" if info.get("reason") else ""))
        elif state == "superseded":
            sb = info.get("superseded_by")
            warn.append(f"대체됨 → {sb}" if sb else "대체됨")
        if fr is not None and fr < 0.5:
            warn.append("오래된 사례(신선도 낮음)")
        m["lifecycle"] = {
            "state": state, "superseded_by": info.get("superseded_by", ""),
            "freshness": fr, "fw_version": m.get("fw_version", ""), "warnings": warn,
        }
    # 폐기/대체 사례를 뒤로(원래 점수 순서는 유지하되 강등). 안정 정렬.
    def _penalty(m):
        return 1 if m.get("lifecycle", {}).get("state") in ("deprecated", "superseded") else 0
    matches.sort(key=_penalty)
    return matches


def stats() -> dict:
    d = _load()
    from collections import Counter
    c = Counter(v.get("state") for v in d.values())
    return {"deprecated": c.get("deprecated", 0), "superseded": c.get("superseded", 0),
            "halflife_days": HALFLIFE_DAYS, "store_path": str(STORE_FILE.relative_to(ROOT))}
