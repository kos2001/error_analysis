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

import json
from pathlib import Path

from json_store import read_json, write_json_atomic, now_iso

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
        "ts": now_iso(),
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
    d = read_json(HISTORY_FILE, [])
    return d if isinstance(d, list) else []


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
        write_json_atomic(HISTORY_FILE, history[-200:])
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


# --------------------------------------------------------------------------- #
# L2 — 파라미터 후보를 동결 평가셋에 shadow 평가 + 무회귀 게이트 (가장 안전)
# --------------------------------------------------------------------------- #
# 튜닝 가능 파라미터 화이트리스트: {param: (env_override, recommender_kwarg)}.
# 1차 검색 파라미터만(리랭커 불필요 → shadow 평가가 빠르고 비용 0). 미설정 시 현행 기본값.
TUNABLE = {
    "gate_cos": "RVP_GATE_COS",   # coverage 게이트 임베딩 임계
    "boost": "RVP_BOOST",         # 동일 칩/분류 가산
}
# 무회귀 게이트가 지키는 지표(모두 현행 이상이어야 '안전'). 허용 오차 0(엄격).
_GUARDED = ("P@1", "P@3", "MRR")
_GUARDED_PARA = ("P@1", "P@3", "MRR", "gate_pass", "junk_blocked")


def _resolved_kb():
    import preprocess
    raw = json.loads((ROOT / "data" / "all_raw_issues.json").read_text(encoding="utf-8"))
    records = [preprocess.parse_issue(r) for r in raw]
    return [r for r in records if r.get("status") == "완료" and not r.get("curated")]


def _eval_with(resolved, overrides: dict) -> dict:
    """주어진 파라미터 override로 Recommender를 만들어 LOO + 동결 paraphrase 평가.

    리랭커 없이(1차 검색 기준) 평가 — 빠르고 외부 비용 0. 동결셋: data/eval_paraphrase.json.
    """
    import eval_recommender as ev
    from recommender import Recommender
    import os as _os
    kw = {"method": _os.getenv("RVP_RECO_METHOD", "hybrid_embed"), "rerank": False}
    kw.update(overrides)
    rec = Recommender(resolved, **kw)
    kb_keys = {r["key"] for r in resolved}
    loo = ev.evaluate(rec, resolved, kb_keys, loo=True, k=3)
    out = {"loo": loo}
    para_path = ROOT / "data" / "eval_paraphrase.json"
    if para_path.exists():
        out["paraphrase"] = ev.evaluate_paraphrase(rec, json.loads(para_path.read_text(encoding="utf-8")))
    return out


def evaluate_param(param: str, value: float) -> dict:
    """후보 파라미터 값을 동결 평가셋에 shadow 평가하고 무회귀 여부를 판정(READ-ONLY).

    live 상태(_RECO_STATE)·설정을 일절 건드리지 않는다. safe=True는 모든 가드 지표가
    현행 이상일 때만(엄격). 안전하다고 자동 적용하지 않음 — 적용은 별도 명시 단계.
    """
    if param not in TUNABLE:
        raise ValueError(f"튜닝 가능 파라미터 아님: {param} (가능: {list(TUNABLE)})")
    import os as _os
    resolved = _resolved_kb()
    cur_val = _os.getenv(TUNABLE[param])
    cur_overrides = {param: float(cur_val)} if cur_val else {}
    base = _eval_with(resolved, cur_overrides)
    cand = _eval_with(resolved, {param: float(value)})

    regressions, deltas = [], {}
    for setname, guarded in (("loo", _GUARDED), ("paraphrase", _GUARDED_PARA)):
        b, c = base.get(setname), cand.get(setname)
        if not (b and c):
            continue
        for k in guarded:
            if k in b and k in c:
                d = round(c[k] - b[k], 4)
                deltas[f"{setname}.{k}"] = d
                if d < 0:
                    regressions.append(f"{setname}.{k} {b[k]}→{c[k]} ({d})")
    safe = not regressions
    return {
        "param": param, "current_value": cur_val, "candidate_value": value,
        "current": base, "candidate": cand, "deltas": deltas,
        "safe": safe, "regressions": regressions,
        "verdict": ("무회귀 — 적용 안전(수동 적용 필요)" if safe
                    else f"회귀 발생 — 적용 금지: {'; '.join(regressions)}"),
    }


# --------------------------------------------------------------------------- #
# L3 — 신호에서 '지식 변경' 제안 도출(사람 검토 큐). loop는 실행하지 않는다.
# --------------------------------------------------------------------------- #
def suggest(reco=None, records: list[dict] | None = None) -> list[dict]:
    """측정 신호 → actionable 지식 변경 제안 목록. 실행은 사람이 HITL 엔드포인트로.

    각 제안: {type, priority, target, rationale, evidence, action_hint}.
    """
    out = []

    # 1) 미승격 고장모드 군집 → Known-Issue 기사 승격 제안(중복 사례 정리)
    if reco is not None:
        try:
            import failure_modes
            for c in failure_modes.cluster_from_recommender(reco, threshold=0.80, min_size=3):
                if c.get("promoted") or c.get("avg_similarity", 0) < 0.80:
                    continue
                out.append({
                    "type": "promote_known_issue", "priority": "P2",
                    "target": c["representative"],
                    "rationale": f"유사도 {c['avg_similarity']}로 묶인 {c['size']}건 중복 사례 — 정규 기사로 승격 권장",
                    "evidence": {"members": c["members"], "size": c["size"],
                                 "avg_similarity": c["avg_similarity"], "chips": c.get("chips", [])},
                    "action_hint": "POST /knowledge/known-issue (members 승격)",
                })
        except Exception:
            pass

    # 2) 지식 공백(자주 묻지만 사례 없음) → RCA 작성/시드 제안
    try:
        import knowledge_gaps
        for g in knowledge_gaps.report(top=10).get("top_underserved_templates", []):
            if g.get("count", 0) < 2:
                continue
            out.append({
                "type": "author_rca", "priority": "P1",
                "target": g["template"],
                "rationale": f"'{g['template'][:40]}' 영역이 {g['count']}회 질의됐으나 유사 사례 없음(coverage 미통과)",
                "evidence": {"gap_count": g["count"]},
                "action_hint": "해당 고장군 해결 사례 시드/문서화",
            })
    except Exception:
        pass

    # 3) 반복적으로 '도움 안 됨'으로 평가된 사례 → 검토/폐기 제안
    try:
        import reco_feedback
        from collections import defaultdict
        net = defaultdict(int)
        for e in reco_feedback._load():
            net[e["match_key"]] += 1 if e.get("rating") == "helpful" else -1
        for k, v in net.items():
            if v <= -2:
                out.append({
                    "type": "review_unhelpful", "priority": "P2", "target": k,
                    "rationale": f"{k}가 추천에서 반복적으로 '도움 안 됨'(순효용 {v}) — 폐기/대체 검토",
                    "evidence": {"net_helpful": v},
                    "action_hint": "POST /knowledge/lifecycle (deprecated/superseded) 검토",
                })
    except Exception:
        pass

    # 4) 통제 어휘 밖 빈출 용어 → 온톨로지 정규화 검토(한 건으로 묶음, 노이즈 방지)
    if records is not None:
        try:
            import ontology
            rev = ontology.review(records, top=8)
            terms = [e["term"] for e in rev.get("uncontrolled_entities", []) if e["count"] >= 10]
            if len(terms) >= 3:
                out.append({
                    "type": "normalize_ontology", "priority": "P3", "target": "uncontrolled_terms",
                    "rationale": "통제 어휘 밖 빈출 용어가 다수 — 동의어 정규화로 검색성/집계 개선",
                    "evidence": {"top_terms": terms},
                    "action_hint": "GET /knowledge/ontology/review 후 POST /knowledge/ontology/synonym",
                })
        except Exception:
            pass
    return out


def _build_recommender(resolved):
    """cron/오프라인용 추천기 — 서버 없이 군집화(suggest)에 쓸 임베딩 보유. 리랭커 off(비용 0)."""
    import os as _os
    from recommender import Recommender
    kw = {kw_: float(_os.environ[env]) for kw_, env in
          (("gate_cos", "RVP_GATE_COS"), ("boost", "RVP_BOOST")) if _os.getenv(env)}
    return Recommender(resolved, method=_os.getenv("RVP_RECO_METHOD", "hybrid_embed"),
                       rerank=False, **kw)


def run_full(save: bool = True) -> dict:
    """cron 진입점 — 측정·진단(L1) + 지식 변경 제안(L3) 큐 병합. 서버 불필요, 지식 불변.

    L2(파라미터 적용)·L3 실행은 포함하지 않는다 — 사람이 검토 후 적용/실행한다.
    """
    import improve_queue
    resolved = _resolved_kb()
    out = run(save=save)                       # 측정·진단·리포트(L1)
    suggestions, synced = [], {"added": 0, "counts": improve_queue.counts()}
    try:
        reco = _build_recommender(resolved)
        suggestions = suggest(reco=reco, records=resolved)
        synced = improve_queue.sync(suggestions)
    except Exception as e:
        out["suggest_error"] = str(e)[:160]
    out["suggestions"] = {"generated": len(suggestions), **synced}
    return out


def main() -> int:
    out = run_full()
    m = out["metrics"]
    print(f"[self_improve] {m['ts']} — 유용성 {m['helpful_rate']} · 공백 {m['gap_events']} · "
          f"큐레이션 {m['curated']} · 기사 {m['known_issues']}")
    for r in out["recommendations"]:
        print(f"  진단 [{r['priority']}] {r['area']}: {r['action']}")
    s = out["suggestions"]
    print(f"[self_improve] 제안 생성 {s['generated']} · 신규 {s.get('added', 0)} · 큐 {s.get('counts')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
