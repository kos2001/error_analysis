"""[레거시 호환] build_graph_db.py → 새 파이프라인(ingest + preprocess)에 위임.

기존 명령 `python scripts/build_graph_db.py` 는 그대로 동작하되,
실제 로직은 src/ingest.py(적재) + src/preprocess.py(전처리/그래프)에 있다.
신규 코드는 scripts/run_pipeline.py 또는 각 src 모듈을 직접 사용하세요.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ingest          # noqa: E402
import preprocess      # noqa: E402


def main() -> int:
    print("[build_graph_db→pipeline] ingest + preprocess 실행")
    issues = ingest.fetch_issues(ingest.DEFAULT_STATUS)
    ingest.save(issues)
    print(f"  ingest: {len(issues)}건")
    records, g = preprocess.run()
    n_ent = sum(1 for _, d in g.nodes(data=True) if d.get("kind") == "entity")
    print(f"  preprocess: 이슈 {len(records)} · 엔티티 {n_ent} · 엣지 {g.number_of_edges()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
