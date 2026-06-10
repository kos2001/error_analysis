"""추천기 평가 — 과거 해결 이슈로 미해결 이슈의 근본원인/해결책을 얼마나 잘 맞히나.

Ground truth: 각 이슈의 잠재 '고장 템플릿'(= template_key(summary)). 같은 템플릿이면
근본원인/해결책이 동일하므로, 검색된 해결 이슈가 질의 이슈와 같은 템플릿이면 정답.

평가 셋:
  1) Resolved LOO : 해결 이슈끼리 leave-one-out (자기 자신 제외)
  2) Unresolved   : 미해결(진행중/시작전) 이슈 → 해결 이슈에서 검색  ← 실제 제품 목표

지표: P@1, P@3, MRR, coverage(해당 템플릿의 해결 사례 존재 비율)

데이터는 data/all_raw_issues.json 에 캐시(없으면 Jira에서 1회 적재).

사용:
    set -a && source .env && set +a
    .venv/bin/python src/eval_recommender.py
    .venv/bin/python src/eval_recommender.py --methods graph,bm25,hybrid,hybrid_embed
    .venv/bin/python src/eval_recommender.py --refresh   # Jira 재적재
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys as _sys
ROOT = Path(__file__).resolve().parent.parent
_sys.path.insert(0, str(ROOT / "src"))

import ingest          # noqa: E402
from preprocess import parse_issue  # noqa: E402
from recommender import Recommender, template_key  # noqa: E402

ALL_RAW = ROOT / "data" / "all_raw_issues.json"
RESOLVED_STATUS = "완료"


def load_all(refresh: bool = False) -> list[dict]:
    if ALL_RAW.exists() and not refresh:
        return json.load(ALL_RAW.open())
    print("[eval] Jira에서 전체 이슈 적재(캐시 생성)...")
    issues = ingest.fetch_issues("all")
    ALL_RAW.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")
    return issues


def evaluate(rec: Recommender, queries: list[dict], kb_keys: set[str],
             loo: bool, k: int = 3) -> dict:
    """queries 각각에 대해 정답(같은 템플릿) 검색 성능 측정."""
    kb_templates_by_key = {r["key"]: template_key(r["summary"]) for r in rec.kb}
    p1 = p3 = mrr = 0.0
    evaluated = 0
    covered = 0
    for q in queries:
        qt = template_key(q["summary"])
        exclude = q["key"] if loo else None
        # 이 질의에 대해 정답이 KB에 존재하는가(coverage)
        same = [kk for kk, t in kb_templates_by_key.items() if t == qt and kk != (exclude or "")]
        if not same:
            continue  # 정답 사례가 없으면 평가 불가(coverage 미달)
        covered += 1
        evaluated += 1
        ranked = rec.rank(q, exclude_key=exclude)[:max(k, 3)]
        hit_ranks = [pos for pos, (i, _) in enumerate(ranked)
                     if kb_templates_by_key[rec.kb[i]["key"]] == qt]
        if hit_ranks:
            first = hit_ranks[0]
            if first == 0:
                p1 += 1
            if first < 3:
                p3 += 1
            mrr += 1.0 / (first + 1)
    n = evaluated or 1
    return {"n": evaluated, "P@1": round(p1 / n, 3), "P@3": round(p3 / n, 3),
            "MRR": round(mrr / n, 3), "coverage": covered}


def evaluate_paraphrase(rec: Recommender, dataset: dict, k: int = 3) -> dict:
    """paraphrase 평가셋: 재서술 질의의 검색 성능 + coverage 게이트 정밀도.

    positives: 정답 템플릿 P@1/P@3/MRR + 게이트 통과율(미통과=false negative)
    negatives: 무관 질의 차단율(통과=false positive)
    """
    p1 = p3 = mrr = passed = 0.0
    pos = dataset["positives"]
    for item in pos:
        q = {"summary": item["summary"], "symptom": item.get("symptom", ""),
             "chip": "", "category": ""}
        res = rec.recommend(q, k=max(k, 3))
        if res["coverage"]:
            passed += 1
        hit_ranks = [pos_i for pos_i, m in enumerate(res["matches"])
                     if template_key(m["summary"]) == item["template"]]
        if hit_ranks:
            first = hit_ranks[0]
            p1 += first == 0
            p3 += first < 3
            mrr += 1.0 / (first + 1)
    neg = dataset["negatives"]
    blocked = sum(
        1 for item in neg
        if not rec.recommend({"summary": item["summary"], "symptom": item.get("symptom", ""),
                              "chip": "", "category": ""}, k=3)["coverage"])
    n = len(pos) or 1
    return {"n_pos": len(pos), "P@1": round(p1 / n, 3), "P@3": round(p3 / n, 3),
            "MRR": round(mrr / n, 3), "gate_pass": round(passed / n, 3),
            "n_neg": len(neg), "junk_blocked": round(blocked / (len(neg) or 1), 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="graph,bm25,hybrid")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--paraphrase", action="store_true",
                    help="data/eval_paraphrase.json 평가(검색+게이트) 포함")
    ap.add_argument("--doc-stages", default="report+analysis",
                    choices=["report", "report+analysis", "both"],
                    help="KB 문서 표현 단계 A/B (both=두 변형 모두 평가)")
    ap.add_argument("--boost", default=None, type=float,
                    help="동일 칩/분류 부스트 오버라이드 (기본: Recommender 기본값)")
    args = ap.parse_args()

    raw = load_all(args.refresh)
    records = [parse_issue(r) for r in raw]
    resolved = [r for r in records if r["status"] == RESOLVED_STATUS]
    unresolved = [r for r in records if r["status"] != RESOLVED_STATUS]
    kb_keys = {r["key"] for r in resolved}
    print(f"[eval] KB(해결) {len(resolved)} · 미해결 {len(unresolved)}")
    print(f"[eval] 해결 템플릿 종류 {len(set(template_key(r['summary']) for r in resolved))}")

    para_ds = None
    if args.paraphrase:
        para_ds = json.load((ROOT / "data" / "eval_paraphrase.json").open())

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    stages = ["report", "report+analysis"] if args.doc_stages == "both" else [args.doc_stages]
    print(f"\n{'method':<14}{'doc':<10}{'set':<12}{'n':>4}{'P@1':>7}{'P@3':>7}{'MRR':>7}{'gate':>7}{'junk':>7}")
    print("-" * 82)
    results = {}
    for m in methods:
        for st in stages:
            kw = {"doc_analysis": st == "report+analysis"}
            if args.boost is not None:
                kw["boost"] = args.boost
            rec = Recommender(resolved, method=m, **kw)
            tag = f"{m}|{st}" + (f"|b{args.boost}" if args.boost is not None else "")
            loo_r = evaluate(rec, resolved, kb_keys, loo=True)
            unr_r = evaluate(rec, unresolved, kb_keys, loo=False)
            results[tag] = {"resolved_loo": loo_r, "unresolved": unr_r}
            print(f"{m:<14}{st:<10}{'resolved-LOO':<12}{loo_r['n']:>4}{loo_r['P@1']:>7}{loo_r['P@3']:>7}{loo_r['MRR']:>7}")
            print(f"{'':<14}{'':<10}{'unresolved':<12}{unr_r['n']:>4}{unr_r['P@1']:>7}{unr_r['P@3']:>7}{unr_r['MRR']:>7}")
            if para_ds is not None:
                pp = evaluate_paraphrase(rec, para_ds)
                results[tag]["paraphrase"] = pp
                print(f"{'':<14}{'':<10}{'paraphrase':<12}{pp['n_pos']:>4}{pp['P@1']:>7}{pp['P@3']:>7}{pp['MRR']:>7}{pp['gate_pass']:>7}{pp['junk_blocked']:>7}")
    # 결과 저장
    (ROOT / "tmp_db" / "eval_recommender.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval] 결과 저장 → tmp_db/eval_recommender.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
