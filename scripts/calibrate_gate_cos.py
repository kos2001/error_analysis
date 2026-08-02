"""embed_cos 폴백 게이트 임계 보정 — 리랭커가 죽었을 때의 안전망을 잰다.

**왜 필요한가.** 평소에는 rerank 게이트가 coverage 를 판정한다. 하지만 리랭커가
실패하면(게이트웨이 미지원·연속 실패로 circuit breaker 작동) embed_cos 폴백 게이트가
그 자리를 대신한다. 이 경로는 평소 지표에 안 잡혀서 오래 방치됐다 — 실제로
2026-08-01 에는 조건문 하나 때문에 **아예 동작하지 않고** 있었고, 평가셋을 바로잡은
2026-08-02 측정에서는 **정답 질의의 26.5% 를 막고** 있었다.

게이트 판정식(recommender.recommend 와 동일):

    coverage = (max_cos >= gate_cos) or (top_entity_overlap >= 1)

엔티티 겹침이 OR 로 들어가 있어 코사인만 봐서는 답이 안 나온다 — 둘을 같이 잰다.

측정 대상: 정답이 KB 안에 있는 질의(통과해야 함) vs 무관 질의(막아야 함).
LOO 는 쓰지 않는다 — 운영에서 들어오는 질의는 KB 에 자기 자신이 없는 미해결
이슈이거나 재서술된 질문이라, 평가셋의 positives/negatives 가 그 상황에 가깝다.

사용:
    set -a && source .env && set +a
    .venv/bin/python scripts/calibrate_gate_cos.py
    .venv/bin/python scripts/calibrate_gate_cos.py --sets confusable,paraphrase
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from preprocess import parse_issue                        # noqa: E402
from recommender import Recommender, env_embed_kwargs     # noqa: E402

RESOLVED = "완료"
SETS = {"confusable": "eval_confusable.json", "paraphrase": "eval_paraphrase.json",
        "generated": "eval_generated.json", "hard": "eval_hard.json"}


def load_kb() -> list[dict]:
    raw = json.loads((ROOT / "data" / "all_raw_issues.json").read_text(encoding="utf-8"))
    return [r for r in (parse_issue(x) for x in raw) if r["status"] == RESOLVED]


def probe(rec: Recommender, item: dict, *, with_meta: bool) -> dict:
    """게이트 입력 신호만 뽑는다 — 임계와 무관하게 max_cos·엔티티 겹침을 기록.

    with_meta 로 **두 가지 실제 경로**를 나눠 잰다. 하나로 뭉치면 답이 안 나온다:

      True  — 이슈를 골라 분석(`/recommend` with key). chip·category 가 채워져 있어
              엔티티 겹침이 자주 1 이상이고, 게이트가 코사인 없이도 통과한다.
      False — 검색창에 증상만 적어 넣는 자유 문장. 메타가 비어 겹침이 잘 안 생기니
              **코사인 임계가 그대로 노출된다.** 폴백 게이트의 진짜 시험대다.
    """
    q = {"summary": item.get("summary", ""), "symptom": item.get("symptom", "")}
    q["chip"] = item.get("chip", "") if with_meta else ""
    q["category"] = item.get("category", "") if with_meta else ""
    res = rec.recommend(q, k=3)
    g = res.get("gate") or {}
    return {"max_cos": g.get("max_cos"), "overlap": g.get("top_entity_overlap", 0),
            "signal": g.get("signal"), "n": len(res["matches"])}


def collect(rec: Recommender, names: list[str], *, with_meta: bool) -> tuple[list, list]:
    pos, neg = [], []
    for name in names:
        path = ROOT / "data" / SETS[name]
        if not path.exists():
            print(f"  ! {name}: 파일 없음 — 건너뜀")
            continue
        ds = json.loads(path.read_text(encoding="utf-8"))
        for p in ds.get("positives", []):
            r = probe(rec, p, with_meta=with_meta)
            if r["max_cos"] is None:
                continue
            pos.append({**r, "set": name, "id": p.get("id") or p.get("gold_key", "")})
        for n in ds.get("negatives", []):
            r = probe(rec, n, with_meta=with_meta)
            if r["max_cos"] is None:
                continue
            neg.append({**r, "set": name, "id": n.get("id", "")})
    return pos, neg


def passes(row: dict, thr: float) -> bool:
    return row["max_cos"] >= thr or row["overlap"] >= 1


def sweep(pos: list, neg: list, lo: float, hi: float, step: float) -> list[dict]:
    out = []
    t = lo
    while t <= hi + 1e-9:
        fn = [r for r in pos if not passes(r, t)]        # 정답인데 막힘
        fp = [r for r in neg if passes(r, t)]            # 무관인데 통과
        out.append({"thr": round(t, 3),
                    "recall": round(1 - len(fn) / max(len(pos), 1), 4),
                    "block": round(1 - len(fp) / max(len(neg), 1), 4),
                    "fn": len(fn), "fp": len(fp)})
        t += step
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="confusable,paraphrase,generated,hard")
    ap.add_argument("--lo", type=float, default=0.30)
    ap.add_argument("--hi", type=float, default=0.75)
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--out", default=str(ROOT / "tmp_db" / "gate_cos_calibration.json"))
    a = ap.parse_args()

    names = [s.strip() for s in a.sets.split(",") if s.strip() in SETS]
    kb = load_kb()
    # **리랭커 OFF** — 폴백 경로를 재는 것이 목적이다. 켜면 rerank 게이트가 판정해
    # embed_cos 는 계산조차 되지 않는다.
    rec = Recommender(kb, method="hybrid_embed", rerank=False, signals=True,
                      **env_embed_kwargs())
    print(f"[calib] KB(해결) {len(kb)} · 임베딩 {rec.embed_backend}/{rec._model_name()}")
    print(f"[calib] 현재 임계 gate_cos={rec.gate_cos}")
    if rec._kb_emb is None:
        raise SystemExit("임베딩이 없어 중단 — 폴백 게이트를 잴 수 없습니다.")

    scenarios = {}
    for label, with_meta in (("이슈 선택(chip·category 있음)", True),
                             ("자유 문장(메타 없음)", False)):
        pos, neg = collect(rec, names, with_meta=with_meta)
        if not pos or not neg:
            raise SystemExit("정답/무관 표본이 부족합니다.")
        pc = sorted(r["max_cos"] for r in pos)
        nc = sorted(r["max_cos"] for r in neg)
        rows = sweep(pos, neg, a.lo, a.hi, a.step)
        perfect = [r for r in rows if r["fn"] == 0 and r["fp"] == 0]
        cur = min(rows, key=lambda r: abs(r["thr"] - rec.gate_cos))
        scenarios[label] = {"pos": pos, "neg": neg, "sweep": rows,
                            "perfect": perfect, "cur": cur}

        print(f"\n{'='*66}\n[{label}]  정답 {len(pos)} · 무관 {len(neg)}")
        print(f"  정답 코사인  최소 {pc[0]:.3f} / p10 {pc[len(pc)//10]:.3f} / "
              f"중앙 {pc[len(pc)//2]:.3f}")
        print(f"  무관 코사인  중앙 {nc[len(nc)//2]:.3f} / 최대 {nc[-1]:.3f}")
        print(f"  엔티티 겹침으로 통과되는 정답 {sum(1 for r in pos if r['overlap'] >= 1)}/{len(pos)}"
              f" · 무관 {sum(1 for r in neg if r['overlap'] >= 1)}/{len(neg)}")
        print(f"  현재 임계 {cur['thr']:.2f} → 통과 {cur['recall']:.3f} / 차단 {cur['block']:.3f}"
              f"  (막힌 정답 {cur['fn']}건)")
        if perfect:
            print(f"  완전분리 구간 {perfect[0]['thr']:.2f}~{perfect[-1]['thr']:.2f}"
                  f" → 중앙 {(perfect[0]['thr']+perfect[-1]['thr'])/2:.3f}")
        else:
            print("  완전분리 구간 **없음** — 정답과 무관의 코사인이 겹친다")
            for r in rows:
                if r["fp"] == 0:
                    print(f"    무관을 다 막는 최저 임계 {r['thr']:.2f} → 정답 통과 {r['recall']:.3f}"
                          f" (정답 {r['fn']}건 손실)")
                    break
            best = max(rows, key=lambda r: r["recall"] + r["block"])
            print(f"    합계 최대 {best['thr']:.2f} → 통과 {best['recall']:.3f} / 차단 {best['block']:.3f}")
        worst = sorted(pos, key=lambda r: r["max_cos"])[:4]
        print("  가장 낮은 정답 코사인:", "  ".join(
            f"{r['max_cos']:.3f}(겹침{r['overlap']})" for r in worst))

    # 두 경로를 **동시에** 만족해야 한다 — 운영은 둘 다 들어온다.
    print(f"\n{'='*66}\n[종합] 두 경로를 함께 본 임계별 성적")
    print("  임계   이슈선택(통과/차단)   자유문장(통과/차단)")
    A = {r["thr"]: r for r in scenarios["이슈 선택(chip·category 있음)"]["sweep"]}
    B = {r["thr"]: r for r in scenarios["자유 문장(메타 없음)"]["sweep"]}
    cands = []
    for t in sorted(A):
        a_, b_ = A[t], B[t]
        cands.append((t, a_, b_))
    for t, a_, b_ in cands:
        if abs(t - rec.gate_cos) < 1e-9 or abs(t*100 - round(t*100/5)*5) < 1e-9:
            mark = "  ← 현재" if abs(t - rec.gate_cos) < 1e-9 else ""
            print(f"  {t:.2f}   {a_['recall']:.3f} / {a_['block']:.3f}"
                  f"          {b_['recall']:.3f} / {b_['block']:.3f}{mark}")
    # 무관을 100% 막으면서 자유문장 통과가 가장 높은 임계
    safe = [(t, a_, b_) for t, a_, b_ in cands if a_["fp"] == 0 and b_["fp"] == 0]
    if safe:
        best = max(safe, key=lambda x: (x[2]["recall"], x[1]["recall"]))
        print(f"\n[권고] 무관 차단 1.000 을 유지하는 최대 통과 임계 = **{best[0]:.2f}**")
        print(f"       이슈선택 통과 {best[1]['recall']:.3f} · 자유문장 통과 {best[2]['recall']:.3f}")
        print(f"       현재({rec.gate_cos}) 대비 자유문장 통과 "
              f"{B[min(B, key=lambda x: abs(x-rec.gate_cos))]['recall']:.3f} → {best[2]['recall']:.3f}")
    else:
        print("\n[권고] 무관을 전부 막는 임계가 없다 — 차단을 조금 포기해야 한다")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(
        {"model": rec._model_name(), "backend": rec.embed_backend,
         "current_gate_cos": rec.gate_cos, "sets": names,
         "scenarios": {k: {"positives": v["pos"], "negatives": v["neg"],
                           "sweep": v["sweep"]} for k, v in scenarios.items()}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[calib] 원자료 저장 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
