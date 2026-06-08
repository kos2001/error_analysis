"""Robot Vision Platform — Customer Support Agent.

Stack:
    - Agno (agent framework)
    - OpenRouter (LLM gateway)
    - Graph retriever (entity → section bipartite graph) as the KB tool
    - SQLite (sessions + user memories)

The agent calls a single tool ``search_kb`` powered by the GraphRetriever
(winner of the retrieval ablation: composite 0.789).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.memory.manager import MemoryManager
from agno.models.openrouter import OpenRouter
from agno.tools import tool

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrievers import GraphRetriever  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "tmp_db"
DB_DIR.mkdir(exist_ok=True)

PRIMARY_MODEL = os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
JUDGE_MODEL = os.getenv("RVP_JUDGE_MODEL", PRIMARY_MODEL)
BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Single shared retriever — the graph is small and cheap to keep in memory.
_GRAPH = GraphRetriever()


@tool(name="search_kb", description="Search the RVP knowledge base (graph retriever) for product docs, error codes, billing, escalation policy.")
def search_kb(query: str) -> str:
    """Retrieve up to 3 most relevant knowledge-base sections for the query."""
    return _GRAPH.retrieve(query, k=3)


def build_agent(user_id: str = "demo-user", session_id: str = "demo-session") -> Agent:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY env var is required")

    db = SqliteDb(db_file=str(DB_DIR / "agent.sqlite"))
    model = OpenRouter(id=PRIMARY_MODEL, api_key=api_key, base_url=BASE_URL)

    memory_manager = MemoryManager(
        model=model,
        db=db,
        memory_capture_instructions=(
            "Capture durable facts about the customer: device model owned, "
            "subscription tier, region, recurring issues, language preference. "
            "Skip transient session details."
        ),
    )

    instructions = [
        "You are the Robot Vision Platform (RVP) customer support agent.",
        "Always call search_kb before answering product / billing / error questions.",
        "If search_kb does not return the answer, say so honestly and offer to escalate to a human (L2).",
        "Escalate to L2 specialists for: refund disputes >$200, RMA, security incidents.",
        "Be concise, cite the relevant section heading (e.g. 'per §3 Common Error Codes').",
        "Use the customer's stored memories (device model, plan, region) to personalize answers.",
        "Reply in the customer's language (Korean or English).",
        "Do NOT use Chinese, Japanese, or any CJK Hanja characters — only Korean Hangul, English, digits, punctuation, emoji.",
    ]

    return Agent(
        name="RVP Support",
        model=model,
        db=db,
        tools=[search_kb],
        user_id=user_id,
        session_id=session_id,
        memory_manager=memory_manager,
        enable_user_memories=True,
        add_memories_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        instructions=instructions,
    )


if __name__ == "__main__":
    agent = build_agent()
    print("Agent ready (graph retriever). Type 'exit' to quit.\n")
    while True:
        try:
            q = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"exit", "quit"}:
            break
        agent.print_response(q, stream=True)
