"""[레거시 호환] LSIGraphRetriever → src/explorer.py::GraphExplorer 래퍼.

기존 import 경로(`from lsi_graph_retriever import LSIGraphRetriever`)를 유지하면서
실제 검색은 통합된 explorer 모듈에 위임한다.
"""

from __future__ import annotations

from pathlib import Path

from explorer import GraphExplorer, PICKLE


class LSIGraphRetriever:
    def __init__(self, graph_path: Path = PICKLE):
        self._ex = GraphExplorer(graph_path)
        self.g = self._ex.g

    def retrieve(self, query: str, k: int = 3) -> str:
        return self._ex.retrieve_context(query, k)

    def retrieve_keys(self, query: str, k: int = 3) -> list[tuple[str, int]]:
        return [(r["key"], r["score"]) for r in self._ex.search(query, k)]


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "PM9C3 NVMe thermal throttle link down"
    r = LSIGraphRetriever()
    print("matched:", r.retrieve_keys(q, 5))
