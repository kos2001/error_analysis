"""LSI 고장 분석 파이프라인 오케스트레이터: ingest → preprocess → explorer.

    set -a && source .env && set +a
    .venv/bin/python scripts/run_pipeline.py                 # 완료 이슈로 전체 실행
    .venv/bin/python scripts/run_pipeline.py --status all     # 전체 상태 적재
    .venv/bin/python scripts/run_pipeline.py --no-viz         # HTML 생성 생략

각 단계는 개별 실행도 가능:
    .venv/bin/python src/ingest.py
    .venv/bin/python src/preprocess.py
    .venv/bin/python src/explorer.py --stats
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ingest          # noqa: E402
import preprocess      # noqa: E402
from explorer import GraphExplorer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="ingest → preprocess → explorer 파이프라인")
    ap.add_argument("--status", default=ingest.DEFAULT_STATUS, help="적재할 상태 (기본: 완료, 'all' 가능)")
    ap.add_argument("--no-viz", action="store_true", help="HTML 시각화 생략")
    args = ap.parse_args()

    print("=" * 60)
    print("STAGE 1/3 · INGEST")
    issues = ingest.fetch_issues(args.status)
    ingest.save(issues)
    print(f"  적재 {len(issues)}건 → data/raw_issues.json")

    print("=" * 60)
    print("STAGE 2/3 · PREPROCESS")
    records, g = preprocess.run()
    n_ent = sum(1 for _, d in g.nodes(data=True) if d.get("kind") == "entity")
    print(f"  레코드 {len(records)} · 엔티티 {n_ent} · 엣지 {g.number_of_edges()}")

    print("=" * 60)
    print("STAGE 3/3 · EXPLORER")
    ex = GraphExplorer()
    s = ex.stats()
    print(f"  이슈 {s['issues']} · 분류별 {s['by_category']}")
    if not args.no_viz:
        p = ex.to_html()
        print(f"  그래프 시각화 → {p.relative_to(ROOT)}")
    # 데모 검색
    demo = "PM9C3 NVMe thermal throttle link down"
    print(f"\n  [데모 검색] '{demo}'")
    for r in ex.search(demo, k=2):
        print(f"   ■ {r['key']} ({r['score']}) {r['title'][:50]} → 해결: {r['resolution'][:80]}")

    print("=" * 60)
    print("파이프라인 완료 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
