"""자기 개선 loop — L1(측정·진단·제안, 무변경).

설계(loop = 측정 → 진단 → 개선 → 검증 → 거버넌스):
  이번에 만든 신호들(P1-3 유용성, P1-2 품질게이트, P3-8 지식공백, 자산 통계)을
  한 번에 집계하고, 직전 회차 대비 드리프트를 계산하며, 신호에서 우선순위 개선
  액션을 도출한다. **부작용이 없는 L1**이라 안전하게 주기 실행 가능(cron/엔드포인트).

  L2(파라미터 자동 튜닝 + 회귀 게이트)·L3(지식 변경 HITL 제안)은 이 측정 토대 위에
  올린다. 여기서는 측정·진단·제안까지만.

산출:
  - snapshot(): 현재 지표 묶음.
  - run(): snapshot + 직전 대비 드리프트 + 개선 제안 → 이력 적재(데이터) +
    날짜별 리포트(claudedocs/self_improve/). 반환 dict.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "data" / "self_improve_history.json"
REPORT_DIR = ROOT / "claudedocs" / "self_improve"


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return {"_error": str(e)[:120]} if default is None else default


def snapshot(records: list[dict] | None = None) -> dict:
    """모든 측정 신호를 한 번에 수집. records 미지정 시 KB 스냅숏 로드."""
    import quality_gate
    import reco_feedback
    import knowledge_gaps
    import knowledge_store
    import failure_modes
    import lifecycle
    import ontology
    import negative_knowledge

    if records is None:
        import preprocess
        raw = json.loads((ROOT / "data" / "all_raw_issues.json").read_text(encoding="utf-8"))
        records = [preprocess.parse_issue(r) for r in raw]
    base = [r for r in records if not r.get("curated")]

    quality = _safe(lambda: quality_gate.validate(base), default={})
    return {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "reco_feedback": _safe(reco_feedback.stats, {}),
        "kb_quality": {"ok": quality.get("ok"), "violations": quality.get("violations", []),
                       "fill": (quality.get("report") or {}).get("fill", {}),
                       "deficient": len((quality.get("report") or {}).get("deficient_resolved_keys", []))},
        "knowledge_gaps": _safe(lambda: knowledge_gaps.report(top=10), {}),
        "assets": {
            "curated_knowledge": _safe(knowledge_store.stats, {}),
            "known_issues": _safe(failure_modes.stats, {}),
            "lifecycle": _safe(lifecycle.stats, {}),
            "ontology": _safe(ontology.stats, {}),
            "negative_knowledge": _safe(negative_knowledge.stats, {}),
        },
    }


def recommendations(snap: dict) -> list[dict]:
    """신호 → 우선순위 개선 액션(진단). L1은 제안만, 실행은 L2/L3."""
    out = []
    fb = snap.get("reco_feedback", {})
    rate = fb.get("helpful_rate")
    if rate is not None and rate < 0.7 and fb.get("total", 0) >= 5:
        out.append({"priority": "P1", "area": "랭킹",
                    "action": f"유용성 {rate} < 0.7 — helpfulness_prior로 재순위 보정 검토(회귀 게이트 후 적용)"})
    if not snap.get("kb_quality", {}).get("ok", True):
        out.append({"priority": "P1", "area": "KB 품질",
                    "action": f"품질 게이트 위반: {snap['kb_quality'].get('violations')} — 인입 마커/추출 점검"})
    gaps = snap.get("knowledge_gaps", {}).get("top_underserved_templates", [])
    if gaps:
        top = ", ".join(f"{g['template'][:24]}({g['count']})" for g in gaps[:3])
        out.append({"priority": "P2", "area": "지식 공백",
                    "action": f"사례 없는 빈출 영역 RCA 작성·시드 필요: {top}"})
    ki = snap.get("assets", {}).get("known_issues", {}).get("total_articles", 0)
    if ki == 0:
        out.append({"priority": "P3", "area": "고장모드",
                    "action": "승격된 Known-Issue 기사 0건 — 중복 군집을 기사로 묶기 권장(/knowledge/clusters)"})
    onto = snap.get("assets", {}).get("ontology", {})
    if onto.get("synonym_groups", 0) == 0:
        out.append({"priority": "P3", "area": "온톨로지",
                    "action": "동의어 그룹 0건 — /knowledge/ontology/review의 산재 표기 정규화 권장"})
    if not out:
        out.append({"priority": "—", "area": "전반", "action": "임계 신호 없음 — 안정적"})
    return out


def _load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            d = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
        except Exception:
            pass
    return []


def _key_metrics(snap: dict) -> dict:
    fb = snap.get("reco_feedback", {})
    return {
        "ts": snap.get("ts"),
        "helpful_rate": fb.get("helpful_rate"),
        "feedback_total": fb.get("total", 0),
        "roi_queries": fb.get("actual_root_cause_queries", 0),
        "kb_quality_ok": snap.get("kb_quality", {}).get("ok"),
        "gap_events": snap.get("knowledge_gaps", {}).get("total_gap_events", 0),
        "curated": snap.get("assets", {}).get("curated_knowledge", {}).get("total", 0),
        "known_issues": snap.get("assets", {}).get("known_issues", {}).get("total_articles", 0),
    }


def _drift(curr: dict, prev: dict | None) -> dict:
    if not prev:
        return {"first_run": True}
    d = {}
    for k in ("helpful_rate", "feedback_total", "roi_queries", "gap_events", "curated", "known_issues"):
        a, b = curr.get(k), prev.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            d[k] = round(a - b, 3)
    d["since"] = prev.get("ts")
    return d


def run(records: list[dict] | None = None, *, save: bool = True) -> dict:
    """측정 → 진단 → (이력 적재 + 리포트). 부작용은 데이터/리포트 쓰기뿐(지식 불변)."""
    snap = snapshot(records)
    recs = recommendations(snap)
    metrics = _key_metrics(snap)
    history = _load_history()
    drift = _drift(metrics, history[-1] if history else None)
    result = {"snapshot": snap, "recommendations": recs, "metrics": metrics, "drift": drift}
    if save:
        history.append(metrics)
        HISTORY_FILE.parent.mkdir(exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
        _write_report(result)
    return result


def _write_report(result: dict) -> Path:
    snap, metrics, drift = result["snapshot"], result["metrics"], result["drift"]
    ts = snap["ts"].replace(":", "").replace("-", "")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"selfcheck_{ts}.md"
    lines = [f"# 자기 개선 점검 — {snap['ts']}", "",
             "## 핵심 지표", "| 지표 | 값 | 직전 대비 |", "|---|---|---|"]
    for k, label in (("helpful_rate", "추천 유용성"), ("roi_queries", "ROI(실제RC 질의)"),
                     ("gap_events", "지식 공백 이벤트"), ("curated", "큐레이션 지식"),
                     ("known_issues", "고장모드 기사")):
        dv = drift.get(k)
        lines.append(f"| {label} | {metrics.get(k)} | {('+' if isinstance(dv,(int,float)) and dv>0 else '')}{dv if dv is not None else ('초회' if drift.get('first_run') else '—')} |")
    lines += ["", f"- KB 품질 게이트: {'통과' if metrics.get('kb_quality_ok') else '위반'}", ""]
    lines += ["## 개선 제안 (L1 — 제안만, 실행은 L2/L3)", "", "| 우선 | 영역 | 액션 |", "|---|---|---|"]
    for r in result["recommendations"]:
        lines.append(f"| {r['priority']} | {r['area']} | {r['action']} |")
    gaps = snap.get("knowledge_gaps", {}).get("top_underserved_templates", [])
    if gaps:
        lines += ["", "## 지식 공백 상위(문서화 우선순위)", ""]
        lines += [f"- {g['template'][:50]} — {g['count']}회" for g in gaps[:10]]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    out = run()
    print(f"[self_improve] {out['metrics']['ts']} — 제안 {len(out['recommendations'])}건")
    for r in out["recommendations"]:
        print(f"  [{r['priority']}] {r['area']}: {r['action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
