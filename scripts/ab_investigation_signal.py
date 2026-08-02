"""A/B: 진행 중 이슈의 '조사/재서술 누적' 신호가 검색을 개선하는가 (누수 없는 측정).

가설(M1): 이슈가 진행되며 쌓이는 조사·트리아지 코멘트(관찰 가능한 재서술)를 질의에
포함하면, 표현이 달라진(paraphrase) 질의의 검색 정확도가 오른다.

공정성(누수 차단):
  - KB는 해결 이슈(완료)에서 구성 — 질의와 독립.
  - 질의는 eval_paraphrase.json 의 '같은 템플릿' paraphrase 2개를 사용한다:
      · 기준선(baseline): paraphrase A(요약/증상)만으로 질의
      · M1: paraphrase A + paraphrase B(다른 lay 재서술)를 investigation 신호로 추가
    B는 또 다른 '일반어 재서술'일 뿐 확정 근본원인이 아니므로 정답 누수가 없다.
    (조사 코멘트가 root_cause를 인용하지 않도록 lsi_failure_data.py 도 수정함.)
  - 2개 paraphrase 보유 템플릿만 사용(각 paraphrase가 '상대' 1개를 investigation으로 가짐).

사용:
  set -a && source .env && set +a   # 임베딩 모델 다운로드용(최초 1회)
  .venv/bin/python scripts/ab_investigation_signal.py --methods hybrid,hybrid_embed
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sys as _sys
ROOT = Path(__file__).resolve().parent.parent
_sys.path.insert(0, str(ROOT / "src"))

from preprocess import parse_issue  # noqa: E402
from recommender import Recommender, env_embed_kwargs, template_key  # noqa: E402

ALL_RAW = ROOT / "data" / "all_raw_issues.json"
EVAL = ROOT / "data" / "eval_paraphrase.json"
RESOLVED_STATUS = "완료"


def _metrics(rec: Recommender, queries: list[dict], k: int = 3) -> dict:
    p1 = p3 = mrr = passed = 0.0
    for q in queries:
        res = rec.recommend({"summary": q["summary"], "symptom": q.get("symptom", ""),
                             "chip": "", "category": "",
                             "investigation": q.get("investigation", "")}, k=max(k, 3))
        if res["coverage"]:
            passed += 1
        hits = [i for i, m in enumerate(res["matches"])
                if template_key(m["summary"]) == q["template"]]
        if hits:
            first = hits[0]
            p1 += first == 0
            p3 += first < 3
            mrr += 1.0 / (first + 1)
    n = len(queries) or 1
    return {"n": len(queries), "P@1": round(p1 / n, 3), "P@3": round(p3 / n, 3),
            "MRR": round(mrr / n, 3), "gate_pass": round(passed / n, 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="hybrid,hybrid_embed")
    args = ap.parse_args()

    raw = json.load(ALL_RAW.open())
    resolved = [parse_issue(r) for r in raw if r.get("status") == RESOLVED_STATUS]
    ds = json.load(EVAL.open())

    by_t: dict[str, list[dict]] = defaultdict(list)
    for p in ds["positives"]:
        by_t[p["template"]].append(p)
    multi = {t: ps for t, ps in by_t.items() if len(ps) >= 2}

    # 각 paraphrase를 질의로, 같은 템플릿의 '다음' paraphrase를 investigation 으로.
    base_qs: list[dict] = []
    m1_qs: list[dict] = []
    for t, ps in multi.items():
        for i, p in enumerate(ps):
            other = ps[(i + 1) % len(ps)]  # 같은 템플릿의 다른 재서술
            base_qs.append({"template": t, "summary": p["summary"],
                            "symptom": p.get("symptom", "")})
            m1_qs.append({"template": t, "summary": p["summary"],
                          "symptom": p.get("symptom", ""),
                          "investigation": f"{other['summary']} {other.get('symptom','')}"})

    print(f"[ab] KB(해결) {len(resolved)} · 다중-paraphrase 템플릿 {len(multi)} · 질의 {len(base_qs)}")
    print(f"\n{'method':<14}{'condition':<26}{'n':>4}{'P@1':>7}{'P@3':>7}{'MRR':>7}{'gate':>7}")
    print("-" * 71)
    results = {}
    for m in [x.strip() for x in args.methods.split(",") if x.strip()]:
        rec = Recommender(resolved, method=m, **env_embed_kwargs())  # doc 표현은 기본 고정
        b = _metrics(rec, base_qs)
        a = _metrics(rec, m1_qs)
        results[m] = {"baseline": b, "m1_investigation": a}
        print(f"{m:<14}{'baseline(단일 재서술)':<26}{b['n']:>4}{b['P@1']:>7}{b['P@3']:>7}{b['MRR']:>7}{b['gate_pass']:>7}")
        print(f"{'':<14}{'M1(재서술 누적)':<26}{a['n']:>4}{a['P@1']:>7}{a['P@3']:>7}{a['MRR']:>7}{a['gate_pass']:>7}")
        d1, dm = round(a['P@1'] - b['P@1'], 3), round(a['MRR'] - b['MRR'], 3)
        print(f"{'':<14}{'Δ (M1 - baseline)':<26}{'':>4}{d1:>+7}{'':>7}{dm:>+7}")

    (ROOT / "tmp_db" / "ab_investigation_signal.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[ab] 결과 저장 → tmp_db/ab_investigation_signal.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
