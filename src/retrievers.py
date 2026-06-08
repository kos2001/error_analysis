"""Retrieval variants for the ablation study.

Each retriever exposes ``retrieve(query: str) -> str`` returning the context
string that will be prepended to the LLM prompt.

Variants:
    - bm25      : BM25 over markdown sections (rank_bm25)
    - vector    : LanceDB vector search (FastEmbed embeddings)
    - hybrid    : LanceDB hybrid (vector + keyword)
    - graph     : Lightweight entity graph (networkx) — query keywords map to
                  section nodes via co-occurrence edges
    - sql       : Structured SQL lookup only (error codes, plans, escalation)
    - hybrid_sql: Hybrid retrieval + SQL structured facts (the "full stack")
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import networkx as nx
from rank_bm25 import BM25Okapi

from agno.knowledge.embedder.fastembed import FastEmbedEmbedder
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.lancedb import LanceDb, SearchType

ROOT = Path(__file__).resolve().parent.parent
KB_PATH = ROOT / "data" / "knowledge.md"
DB_DIR = ROOT / "tmp_db"


# ---------- Section chunker ----------
def load_sections() -> list[dict]:
    """Split knowledge.md by ## headings."""
    text = KB_PATH.read_text()
    parts = re.split(r"\n## ", text)
    sections = []
    for i, p in enumerate(parts):
        if i == 0 and not p.startswith("## "):
            continue
        if not p.strip():
            continue
        body = p if p.startswith("## ") else "## " + p
        title_line = body.splitlines()[0].lstrip("# ").strip()
        sections.append({"title": title_line, "text": body.strip()})
    return sections


SECTIONS = load_sections()


def _tokenize(s: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9가-힣]+", s.lower())


# ---------- BM25 ----------
class BM25Retriever:
    def __init__(self):
        self.corpus = [s["text"] for s in SECTIONS]
        self.titles = [s["title"] for s in SECTIONS]
        self.bm25 = BM25Okapi([_tokenize(c) for c in self.corpus])

    def retrieve(self, query: str, k: int = 3) -> str:
        scores = self.bm25.get_scores(_tokenize(query))
        top = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return "\n\n---\n\n".join(self.corpus[i] for i in top)


# ---------- LanceDB-backed Vector / Hybrid ----------
def _lance_kb(table_suffix: str, search_type: SearchType) -> Knowledge:
    vec = LanceDb(
        uri=str(DB_DIR / "lancedb_ablation"),
        table_name=f"rvp_{table_suffix}",
        search_type=search_type,
        embedder=FastEmbedEmbedder(),
    )
    kb = Knowledge(vector_db=vec)
    # Insert section-level chunks (one content per section so retrieval is granular)
    for s in SECTIONS:
        kb.add_content(text_content=s["text"], name=s["title"], skip_if_exists=True)
    return kb


class VectorRetriever:
    def __init__(self):
        self.kb = _lance_kb("vector", SearchType.vector)

    def retrieve(self, query: str, k: int = 3) -> str:
        docs = self.kb.search(query, max_results=k)
        return "\n\n---\n\n".join(getattr(d, "content", str(d)) for d in docs)


class HybridRetriever:
    def __init__(self):
        self.kb = _lance_kb("hybrid", SearchType.hybrid)

    def retrieve(self, query: str, k: int = 3) -> str:
        docs = self.kb.search(query, max_results=k)
        return "\n\n---\n\n".join(getattr(d, "content", str(d)) for d in docs)


# ---------- Graph RAG (lightweight) ----------
class GraphRetriever:
    """Build a bipartite graph of entities (error codes, key terms) and sections.

    Retrieval: extract entities from query, traverse to connected sections,
    rank by entity overlap count.
    """
    KEY_PATTERNS = [
        r"\bE\d{3}\b",                  # error codes
        r"\bRVP-[A-Za-z0-9-]+\b",       # product SKUs
        r"\b(Free|Pro|Enterprise)\b",
        r"\b(refund|RMA|webhook|escalation|thermal|illumination|pairing|calibration|telemetry|HMAC)\b",
        r"\b(L1|L2|L3)\b",
    ]

    def __init__(self):
        self.g = nx.Graph()
        for i, s in enumerate(SECTIONS):
            node = f"sec:{i}"
            self.g.add_node(node, text=s["text"], title=s["title"])
            for ent in self._extract_entities(s["text"]):
                self.g.add_edge(node, f"ent:{ent.lower()}")

    def _extract_entities(self, text: str) -> set[str]:
        ents = set()
        for pat in self.KEY_PATTERNS:
            ents.update(m.group(0) for m in re.finditer(pat, text, flags=re.IGNORECASE))
        return ents

    def retrieve(self, query: str, k: int = 3) -> str:
        q_ents = self._extract_entities(query)
        if not q_ents:
            # Fallback: token overlap with section titles
            qtoks = set(_tokenize(query))
            scored = [
                (sum(1 for t in _tokenize(s["title"]) if t in qtoks), s["text"])
                for s in SECTIONS
            ]
            scored.sort(reverse=True)
            return "\n\n---\n\n".join(t for _, t in scored[:k])

        scores: dict[str, int] = {}
        for ent in q_ents:
            ent_node = f"ent:{ent.lower()}"
            if ent_node in self.g:
                for sec_node in self.g.neighbors(ent_node):
                    scores[sec_node] = scores.get(sec_node, 0) + 1

        top = sorted(scores.items(), key=lambda x: -x[1])[:k]
        return "\n\n---\n\n".join(self.g.nodes[n]["text"] for n, _ in top)


# ---------- SQL-only ----------
class SQLRetriever:
    """Look up structured facts from SQLite. Returns a textual summary."""

    def retrieve(self, query: str, k: int = 3) -> str:
        from sql_db import check_escalation, lookup_error, lookup_plan

        out: list[str] = []
        # Error codes
        for code in re.findall(r"\bE\d{3}\b", query, flags=re.IGNORECASE):
            row = lookup_error(code)
            if row:
                out.append(
                    f"[ERROR_CODE {row['code']}] {row['name']} — cause: {row['cause']} | "
                    f"fix: {row['fix']} | threshold: {row['threshold']}"
                )
        # Plans
        for plan_name in re.findall(r"\b(Free|Pro|Enterprise)\b", query, flags=re.IGNORECASE):
            row = lookup_plan(plan_name)
            if row:
                out.append(
                    f"[PLAN {row['name']}] price=${row['monthly_price_usd']}/mo, "
                    f"daily_limit={row['daily_inference_limit']}, "
                    f"rate_limit={row['rate_limit_per_sec']}/s, "
                    f"refund_window={row['refund_window_days']}d, "
                    f"refund_usage_cap={row['refund_usage_cap']}"
                )
        # Escalation
        amount_match = re.search(r"\$?(\d{2,5})", query)
        amount = float(amount_match.group(1)) if amount_match else None
        for rule in check_escalation(query, amount):
            out.append(
                f"[ESCALATION] trigger='{rule['trigger_keyword']}', "
                f"min_amount=${rule['min_amount_usd']}, target={rule['target_tier']}, "
                f"note={rule['note']}"
            )
        return "\n".join(out) if out else "[no structured data matched]"


class HybridSQLRetriever:
    def __init__(self):
        self.text = HybridRetriever()
        self.sql = SQLRetriever()

    def retrieve(self, query: str, k: int = 3) -> str:
        sql_ctx = self.sql.retrieve(query)
        kb_ctx = self.text.retrieve(query, k)
        return f"== STRUCTURED FACTS ==\n{sql_ctx}\n\n== KNOWLEDGE BASE ==\n{kb_ctx}"


class GraphBM25Retriever:
    """Combine graph entity matches (high-precision) with BM25 (high-recall).

    Strategy: take graph-matched sections first (entity hits are deterministic
    signals like error codes), then top up with BM25 results to fill k.
    Reciprocal-rank-fusion when sections overlap.
    """

    def __init__(self, alpha: float = 0.6):
        self.graph = GraphRetriever()
        self.bm25 = BM25Retriever()
        self.alpha = alpha  # weight for graph signal

    def _rank_graph(self, query: str) -> list[tuple[int, float]]:
        q_ents = self.graph._extract_entities(query)
        scores: dict[int, int] = {}
        if q_ents:
            for ent in q_ents:
                en = f"ent:{ent.lower()}"
                if en in self.graph.g:
                    for sec_node in self.graph.g.neighbors(en):
                        idx = int(sec_node.split(":")[1])
                        scores[idx] = scores.get(idx, 0) + 1
        return sorted(scores.items(), key=lambda x: -x[1])

    def _rank_bm25(self, query: str) -> list[tuple[int, float]]:
        scores = self.bm25.bm25.get_scores(_tokenize(query))
        return sorted(((i, float(s)) for i, s in enumerate(scores)), key=lambda x: -x[1])

    def retrieve(self, query: str, k: int = 3) -> str:
        gr = self._rank_graph(query)
        br = self._rank_bm25(query)

        # Reciprocal Rank Fusion
        rrf: dict[int, float] = {}
        for rank, (idx, _) in enumerate(gr):
            rrf[idx] = rrf.get(idx, 0) + self.alpha / (60 + rank)
        for rank, (idx, _) in enumerate(br):
            rrf[idx] = rrf.get(idx, 0) + (1 - self.alpha) / (60 + rank)

        top = sorted(rrf.items(), key=lambda x: -x[1])[:k]
        return "\n\n---\n\n".join(SECTIONS[i]["text"] for i, _ in top)


def get_retriever(name: str):
    return {
        "bm25": BM25Retriever,
        "vector": VectorRetriever,
        "hybrid": HybridRetriever,
        "graph": GraphRetriever,
        "graph_bm25": GraphBM25Retriever,
        "sql": SQLRetriever,
        "hybrid_sql": HybridSQLRetriever,
    }[name]()


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "E033 thermal"
    for name in ["bm25", "vector", "hybrid", "graph", "sql", "hybrid_sql"]:
        print(f"\n==================== {name} ====================")
        try:
            ctx = get_retriever(name).retrieve(q)
            print(ctx[:600])
        except Exception as e:
            print("ERROR:", e)
