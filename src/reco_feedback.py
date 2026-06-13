"""추천 유용성/결과 피드백 저장소 — "추천이 실제로 맞았나·도움됐나"를 수집한다.

배경(지식자산화 갭 P1-3):
  기존 rca_feedback는 *초안 텍스트 수정*만 저장했고, 정작 추천된 과거 사례·제안이
  실제로 도움됐는지/실제 근본원인이었는지 신호가 없었다. → 자산이 적합도를 학습하거나
  ROI(시간 절감·정답률)를 증명할 수 없었다.

해결:
  매치/제안 카드에서 사람이 남기는 라벨(도움됨/아님, 실제 근본원인)을 영속 저장하고,
  ① 랭킹 학습 prior ② 실전형 평가셋 자동 확장(정답 쌍) ③ ROI 지표로 환류한다.
  저장 경로는 P1-1과 동일하게 git 추적되는 data/(버전·백업·공유) — 신호 유실 방지.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "reco_feedback.json"

RATINGS = ("helpful", "not_helpful")


def _load() -> list:
    if STORE_FILE.exists():
        try:
            d = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
        except Exception:
            pass
    return []


def _save(events: list) -> None:
    STORE_FILE.parent.mkdir(exist_ok=True)
    tmp = STORE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_FILE)  # 원자적 교체


def record(*, query_key: str, match_key: str, rating: str,
           query_summary: str = "", query_template: str = "",
           is_actual_root_cause: bool = False, match_rank: int | None = None,
           match_score: float | None = None, note: str = "") -> dict:
    """피드백 1건 기록. 동일 (query_key, match_key)는 최신으로 갱신(중복 누적 방지)."""
    if rating not in RATINGS:
        raise ValueError(f"rating은 {RATINGS} 중 하나여야 함: {rating!r}")
    if not match_key:
        raise ValueError("match_key 필수")
    ev = {
        "query_key": query_key or "", "query_summary": query_summary or "",
        "query_template": query_template or "", "match_key": match_key,
        "rating": rating, "is_actual_root_cause": bool(is_actual_root_cause),
        "match_rank": match_rank, "match_score": match_score, "note": note or "",
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    events = _load()
    qk, mk = ev["query_key"], ev["match_key"]
    events = [e for e in events if not (e.get("query_key") == qk and e.get("match_key") == mk)]
    events.append(ev)
    _save(events)
    return ev


def stats() -> dict:
    """집계 — 유용성 비율 + ROI 프록시(실제 근본원인으로 확인된 고유 질의 수)."""
    events = _load()
    rc = Counter(e["rating"] for e in events if e.get("rating") in RATINGS)
    helpful, not_helpful = rc.get("helpful", 0), rc.get("not_helpful", 0)
    total = helpful + not_helpful
    queries = {e["query_key"] for e in events if e.get("query_key")}
    # ROI 프록시: 추천이 실제 근본원인으로 확인된 고유 질의 수(=주니어가 직접 분석 안 해도 된 건)
    roi_queries = {e["query_key"] for e in events
                   if e.get("is_actual_root_cause") and e.get("query_key")}
    # 가장 도움된 사례(순효용=helpful-not_helpful)
    net = defaultdict(int)
    for e in events:
        net[e["match_key"]] += 1 if e.get("rating") == "helpful" else -1
    top = sorted(net.items(), key=lambda x: -x[1])[:5]
    return {
        "total": len(events), "helpful": helpful, "not_helpful": not_helpful,
        "helpful_rate": round(helpful / total, 3) if total else None,
        "queries_with_feedback": len(queries),
        "actual_root_cause_queries": len(roi_queries),
        "top_helpful_matches": [{"match_key": k, "net": v} for k, v in top if v > 0],
    }


def eval_pairs() -> list[dict]:
    """실제 근본원인으로 확인된 (질의→정답 사례) 쌍 — 실전형 평가셋 자동 확장용.

    중복 (query_key, match_key) 제거. query_template이 있으면 클래스 기준 평가에 활용.
    """
    seen, out = set(), []
    for e in _load():
        if not e.get("is_actual_root_cause"):
            continue
        sig = (e.get("query_key"), e.get("match_key"))
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"query_key": e.get("query_key", ""), "query_summary": e.get("query_summary", ""),
                    "query_template": e.get("query_template", ""), "gold_match_key": e["match_key"]})
    return out


def helpfulness_prior(template: str = "") -> dict:
    """매치별 순효용(helpful-not_helpful). template 지정 시 동일 클래스 피드백만.

    랭킹 prior로 활용 가능(현재는 노출만, 추천기 반영은 평가 후 별도 단계).
    """
    net = defaultdict(int)
    for e in _load():
        if template and e.get("query_template") != template:
            continue
        net[e["match_key"]] += 1 if e.get("rating") == "helpful" else -1
    return dict(net)
