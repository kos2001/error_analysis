"""[레거시 호환] graph_viz.py → src/explorer.py 의 to_html 에 위임.

기존 명령 `python scripts/graph_viz.py` 는 그대로 동작하되, 그래프 시각화 로직은
src/explorer.py::GraphExplorer.to_html 로 통합되었다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from explorer import GraphExplorer, OUT_HTML  # noqa: E402


def main() -> int:
    p = GraphExplorer().to_html(OUT_HTML)
    print(f"[graph_viz→explorer] 그래프 생성 → {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
