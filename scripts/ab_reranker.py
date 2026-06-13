"""A/B: cross-encoder reranker 효과 — hybrid_embed 1차 검색 vs +rerank 재순위.

가설: bi-encoder(임베딩) top-N 후보를 cross-encoder로 다시 채점하면 paraphrase
질의의 1순위 정밀도(P@1)가 오른다. 또한 rerank 점수(강도)가 무관 질의를 잘
분리하면 coverage 게이트 신호로도 쓸 수 있다.

지표:
  P@1/P@3/MRR : 1차(hybrid_embed) vs 재순위(top-N rerank) 랭킹 품질.
  gate 분리   : positives 최상위 rerank 점수 vs negatives 최상위 rerank 점수 분포.

사용:
  set -a && source .env && set +a
  .venv/bin/python scripts/ab_reranker.py --n 20 --model cohere/rerank-v3.5
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import sys as _sys
ROOT = Path(__file__).resolve().parent.parent
_sys.path.insert(0, str(ROOT / "src"))

from preprocess import parse_issue  # noqa: E402
from recommender import Recommender, template_key, _doc_text  # noqa: E402
from reranker import rerank, DEFAULT_MODEL  # noqa: E402

ALL_RAW = ROOT / "data" / "all_raw_issues.json"
EVAL = ROOT / "data" / "eval_paraphrase.json"
RESOLVED_STATUS = "완료"


def _first_stage(rec: Recommender, q: dict, n: int) -> list[int]:
    """hybrid_embed 1차 top-N 후보의 KB 인덱스."""
    return [i for i, _ in rec.rank(q)[:n]]


def _ranks_to_metrics(template: str, ordered_kb_idx: list[int], kb: list[dict]) -> tuple[int, int, float]:
    hits = [pos for pos, i in enumerate(ordered_kb_idx)
            if template_key(kb[i]["summary"]) == template]
    if not hits:
        return 0, 0, 0.0
    f = hits[0]
    return int(f == 0), int(f < 3), 1.0 / (f + 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="1차 후보 수(재순위 입력)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    raw = json.load(ALL_RAW.open())
    resolved = [parse_issue(r) for r in raw if r.get("status") == RESOLVED_STATUS]
    ds = json.load(EVAL.open())
    rec = Recommender(resolved, method="hybrid_embed")  # 1차: 현재 채택 방식
    kb = rec.kb
    print(f"[ab-rerank] KB(해결) {len(resolved)} · paraphrase {len(ds['positives'])}pos/{len(ds['negatives'])}neg"
          f" · 1차 top-N={args.n} · rerank={args.model}")

    b_p1 = b_p3 = b_mrr = 0.0
    r_p1 = r_p3 = r_mrr = 0.0
    pos_top_scores = []
    t0 = time.monotonic()
    for it in ds["positives"]:
        q = {"summary": it["summary"], "symptom": it.get("symptom", ""), "chip": "", "category": ""}
        cand = _first_stage(rec, q, args.n)
        # baseline = 1차 순서
        p1, p3, mrr = _ranks_to_metrics(it["template"], cand, kb)
        b_p1 += p1; b_p3 += p3; b_mrr += mrr
        # rerank
        docs = [_doc_text(kb[i], analysis=True) for i in cand]
        qtext = f"{it['summary']} {it.get('symptom','')}".strip()
        order = rerank(qtext, docs, model=args.model)
        reranked = [cand[idx] for idx, _ in order]
        pos_top_scores.append(order[0][1] if order else 0.0)
        p1, p3, mrr = _ranks_to_metrics(it["template"], reranked, kb)
        r_p1 += p1; r_p3 += p3; r_mrr += mrr

    neg_top_scores = []
    for it in ds["negatives"]:
        q = {"summary": it["summary"], "symptom": it.get("symptom", ""), "chip": "", "category": ""}
        cand = _first_stage(rec, q, args.n)
        docs = [_doc_text(kb[i], analysis=True) for i in cand]
        qtext = f"{it['summary']} {it.get('symptom','')}".strip()
        order = rerank(qtext, docs, model=args.model)
        neg_top_scores.append(order[0][1] if order else 0.0)

    n = len(ds["positives"])
    dt = time.monotonic() - t0
    print(f"\n{'stage':<22}{'P@1':>7}{'P@3':>7}{'MRR':>7}")
    print("-" * 43)
    print(f"{'1차 hybrid_embed':<22}{b_p1/n:>7.3f}{b_p3/n:>7.3f}{b_mrr/n:>7.3f}")
    print(f"{'+rerank (top-N)':<22}{r_p1/n:>7.3f}{r_p3/n:>7.3f}{r_mrr/n:>7.3f}")
    print(f"{'Δ (rerank-1차)':<22}{(r_p1-b_p1)/n:>+7.3f}{(r_p3-b_p3)/n:>+7.3f}{(r_mrr-b_mrr)/n:>+7.3f}")

    import statistics as st
    print(f"\n[게이트 신호] rerank 최상위 점수 분포 (강도 기반 게이트 후보):")
    print(f"  positives(정답): 중앙값 {st.median(pos_top_scores):.3f} · 최소 {min(pos_top_scores):.3f}")
    print(f"  negatives(무관): 중앙값 {st.median(neg_top_scores):.3f} · 최대 {max(neg_top_scores):.3f}")
    sep = min(pos_top_scores) - max(neg_top_scores)
    print(f"  분리 마진(min정답 - max무관): {sep:+.3f}  → {'완전 분리(임계 1개로 게이트 가능)' if sep>0 else '겹침(임계 선택 필요)'}")

    out = {"n_pos": n, "first_stage": {"P@1": round(b_p1/n,3), "P@3": round(b_p3/n,3), "MRR": round(b_mrr/n,3)},
           "reranked": {"P@1": round(r_p1/n,3), "P@3": round(r_p3/n,3), "MRR": round(r_mrr/n,3)},
           "gate": {"pos_min": round(min(pos_top_scores),3), "neg_max": round(max(neg_top_scores),3),
                    "model": args.model, "top_n": args.n}, "sec": round(dt,1)}
    (ROOT / "tmp_db" / "ab_reranker.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ab-rerank] 결과 저장 → tmp_db/ab_reranker.json ({dt:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
