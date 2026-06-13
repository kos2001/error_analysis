"""A/B: 임베딩 모델 비교 — 현재 MiniLM vs 로컬 e5-large vs OpenRouter bge-m3.

동일 paraphrase 평가셋(data/eval_paraphrase.json)으로 hybrid_embed 방식에서
임베딩 모델만 바꿔 검색 품질을 비교한다.

지표 해석:
  P@1/P@3/MRR : 랭킹 품질 — 게이트와 무관(모델 간 공정 비교의 핵심).
  gate/junk   : 게이트 임계(gate_cos=0.48)는 MiniLM 기준 보정값이므로, 다른 모델에선
                재보정 전까지 참고용일 뿐(코사인 분포가 모델마다 다름).

사용:
  set -a && source .env && set +a   # e5 최초 다운로드 + bge-m3(OpenRouter) 키
  .venv/bin/python scripts/ab_embedding_models.py
  .venv/bin/python scripts/ab_embedding_models.py --configs minilm,e5        # 로컬만
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
from recommender import Recommender  # noqa: E402
from eval_recommender import evaluate_paraphrase  # noqa: E402

ALL_RAW = ROOT / "data" / "all_raw_issues.json"
EVAL = ROOT / "data" / "eval_paraphrase.json"
RESOLVED_STATUS = "완료"

CONFIGS = {
    # name:        (embed_model, backend)
    "minilm": ("", "fastembed"),                                  # 현재 기본(384-dim)
    "e5":     ("intfloat/multilingual-e5-large", "fastembed"),    # 로컬 1024-dim
    "bge-m3": ("baai/bge-m3", "openrouter"),                      # OpenRouter 1024-dim
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="minilm,e5,bge-m3")
    args = ap.parse_args()

    raw = json.load(ALL_RAW.open())
    resolved = [parse_issue(r) for r in raw if r.get("status") == RESOLVED_STATUS]
    ds = json.load(EVAL.open())
    print(f"[ab-embed] KB(해결) {len(resolved)} · paraphrase {len(ds['positives'])}pos/{len(ds['negatives'])}neg")
    print(f"\n{'config':<10}{'model':<42}{'n':>4}{'P@1':>7}{'P@3':>7}{'MRR':>7}{'gate':>7}{'junk':>7}{'sec':>7}")
    print("-" * 93)

    results = {}
    for name in [c.strip() for c in args.configs.split(",") if c.strip()]:
        model, backend = CONFIGS[name]
        t0 = time.monotonic()
        try:
            rec = Recommender(resolved, method="hybrid_embed",
                              embed_model=model, embed_backend=backend)
            pp = evaluate_paraphrase(rec, ds)
        except Exception as e:
            print(f"{name:<10}{(model or 'MiniLM(기본)'):<42} 실패: {type(e).__name__}: {str(e)[:60]}")
            continue
        dt = time.monotonic() - t0
        results[name] = {"model": model or rec._EMBED_MODEL, "backend": backend, **pp}
        print(f"{name:<10}{(model or 'MiniLM(기본)'):<42}{pp['n_pos']:>4}"
              f"{pp['P@1']:>7}{pp['P@3']:>7}{pp['MRR']:>7}{pp['gate_pass']:>7}{pp['junk_blocked']:>7}{dt:>7.1f}")

    (ROOT / "tmp_db" / "ab_embedding_models.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[ab-embed] 결과 저장 → tmp_db/ab_embedding_models.json")
    print("주의: gate/junk 는 MiniLM 기준 임계(0.48) — 다른 모델은 재보정 필요(P@1/P@3/MRR만 공정 비교).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
