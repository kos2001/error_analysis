"""인용 환각 A/B — 허용 키를 닫힌 목록으로 주면 줄어드는가.

**측정 대상.** 설명 본문이 제공되지 않은 사례 키를 인용하는 빈도. 실측
(2026-08-04, `eval_explanations --n 30`): 30건 중 2건(6.7%). 그런데 근거에 `-rca`
키가 섞인 6건만 보면 **2/6 = 33%** 이고 나머지 24건은 0% 였다. LSI-112 는 스스로
"LSI-70과 LSI-133은 LSI-7-rca에 포함된 것으로 간주됩니다" 라고 적었다 — 종합 RCA
문서가 다른 사례를 품고 있다고 **추측**한 것이다.

**확률적이다.** 같은 질의·같은 근거의 캐시 두 건 중 하나에만 환각이 있었다.
그래서 케이스당 여러 번 돌려 비율로 본다 — 1회 비교로는 판단할 수 없다.

**두 팔.**
  A(현행)  : 규칙만 — "제공된 키만, 창작 금지"
  B(닫힌목록): + 허용 키를 나열하고 "이 목록이 전부, 포함 관계를 추측하지 말 것"

캐시를 우회해 매번 새로 생성한다(비용 발생 — 케이스당 ~89초).

사용:
    set -a && source .env && set +a
    .venv/bin/python scripts/ab_citation_keylist.py --reps 2
    .venv/bin/python scripts/ab_citation_keylist.py --reps 3 --only-rca
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("RVP_MCP", "0")
os.environ.setdefault("RVP_JIRA_POLL_SEC", "0")
os.environ.setdefault("RVP_PREWARM", "0")


def unsupported(md: str, evidence: list[str], self_key: str) -> list[str]:
    """본문이 언급한 키 중 제공되지 않은 것. `-rca` 접미사는 원본과 같은 것으로 본다."""
    allow = {self_key}
    for k in evidence:
        allow.add(k)
        m = re.match(r"(LSI-\d+)", str(k))
        if m:
            allow.add(m.group(1))
    return sorted({m for m in re.findall(r"LSI-\d+", md) if m not in allow})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2, help="케이스당 팔별 반복 횟수")
    ap.add_argument("--n", type=int, default=40, help="훑을 미해결 이슈 수")
    ap.add_argument("--only-rca", action="store_true",
                    help="근거에 -rca 키가 섞인 위험군만 (기본: 위험군 우선 + 대조 일부)")
    ap.add_argument("--out", default=str(ROOT / "tmp_db" / "ab_citation_keylist.json"))
    a = ap.parse_args()

    import server
    st = server._reco_state()

    # 위험군(근거에 -rca 포함)과 대조군을 나눠 고른다 — 위험군만 보면 개선폭이
    # 과장되고, 섞어 보면 희석된다. 둘 다 보고한다.
    risky, control = [], []
    for rec in st["unresolved"][: a.n]:
        res = server._recommend_cached(rec, k=4, exclude_key=rec["key"])
        if not res["matches"] or not res.get("coverage", True):
            continue
        mr = [st["by_key"].get(m["key"], m) for m in res["matches"]]
        ev = [m["key"] for m in res["matches"]]
        (risky if any("-rca" in k for k in ev) else control).append((rec, mr, ev))
    control = [] if a.only_rca else control[: max(len(risky), 3)]
    cases = risky + control
    print(f"[ab] 위험군(-rca) {len(risky)}건 · 대조군 {len(control)}건 · "
          f"팔별 반복 {a.reps} → 총 생성 {len(cases) * 2 * a.reps}회")
    if not cases:
        raise SystemExit("대상 없음")

    rows = []
    t0 = time.monotonic()
    for arm, keylist in (("A_현행", "0"), ("B_닫힌목록", "1")):
        os.environ["RVP_EXPLAIN_KEYLIST"] = keylist
        for rec, mr, ev in cases:
            group = "위험군" if any("-rca" in k for k in ev) else "대조군"
            for r in range(a.reps):
                md = "".join(server._generate_explain_md(rec, mr))
                bad = unsupported(md, ev, rec["key"])
                rows.append({"arm": arm, "group": group, "key": rec["key"], "rep": r,
                             "evidence": ev, "unsupported": bad, "chars": len(md)})
                print(f"  [{len(rows):>3}] {arm:<10} {group} {rec['key']:<10} "
                      f"미제공 {len(bad)} {bad if bad else ''}")

    dt = time.monotonic() - t0
    print(f"\n[ab] 총 {len(rows)}회 생성 · {dt/60:.1f}분")
    print("\n" + "=" * 62)
    print(f"{'팔':<12}{'구분':<8}{'생성':<6}{'환각발생':<9}{'비율':<8}{'환각키 총수'}")
    summary = {}
    for arm in ("A_현행", "B_닫힌목록"):
        for group in ("위험군", "대조군"):
            g = [r for r in rows if r["arm"] == arm and r["group"] == group]
            if not g:
                continue
            n_bad = sum(1 for r in g if r["unsupported"])
            n_keys = sum(len(r["unsupported"]) for r in g)
            summary[f"{arm}/{group}"] = {"n": len(g), "bad": n_bad,
                                         "rate": n_bad / len(g), "keys": n_keys}
            print(f"{arm:<12}{group:<8}{len(g):<6}{n_bad:<9}{n_bad/len(g):<8.1%}{n_keys}")

    a_all = [r for r in rows if r["arm"] == "A_현행"]
    b_all = [r for r in rows if r["arm"] == "B_닫힌목록"]
    ra = sum(1 for r in a_all if r["unsupported"]) / max(len(a_all), 1)
    rb = sum(1 for r in b_all if r["unsupported"]) / max(len(b_all), 1)
    print(f"\n전체: A {ra:.1%} → B {rb:.1%}")
    if len(a_all) < 10:
        print("⚠ 표본이 작다 — 방향만 보고 판단은 유보할 것.")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"rows": rows, "summary": summary,
                                       "reps": a.reps, "minutes": round(dt / 60, 1)},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ab] 원자료 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
