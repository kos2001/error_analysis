"""Retrieval ablation study.

For each variant in {bm25, vector, hybrid, graph, sql, hybrid_sql}:
    1. retrieve context for each of the 10 eval cases
    2. ask the same LLM (OpenRouter) to answer using ONLY that context
    3. score with fact_recall + LLM-as-Judge

Outputs:
    tmp_db/ablation_results.json — per-variant, per-case answers + scores
    Printed leaderboard sorted by composite score
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from agno.agent import Agent
from agno.eval.agent_as_judge import AgentAsJudgeEval
from agno.models.openrouter import OpenRouter

from agent import BASE_URL, JUDGE_MODEL, PRIMARY_MODEL
from eval_dataset import DATASET
from evaluate import fact_recall
from retrievers import get_retriever

VARIANTS = ["bm25", "vector", "hybrid", "graph", "sql", "hybrid_sql"]

SYSTEM = (
    "You are the Robot Vision Platform (RVP) customer support agent. "
    "Answer the customer using ONLY the context below. "
    "If the context does not contain the answer, say so honestly and offer to escalate. "
    "Be concise, cite the relevant section heading when possible. "
    "Respond in the customer's language (Korean or English)."
)


def make_runner_agent():
    api_key = os.environ["OPENROUTER_API_KEY"]
    return Agent(
        name="rvp-runner",
        model=OpenRouter(id=PRIMARY_MODEL, api_key=api_key, base_url=BASE_URL),
        instructions=[SYSTEM],
        markdown=False,
        telemetry=False,
    )


def answer(agent: Agent, context: str, question: str) -> str:
    prompt = f"## Context\n{context}\n\n## Customer message\n{question}\n\n## Your answer:"
    resp = agent.run(prompt)
    return resp.content if hasattr(resp, "content") else str(resp)


def run() -> dict:
    api_key = os.environ["OPENROUTER_API_KEY"]
    runner = make_runner_agent()

    print("Pre-building retrievers (one-time index)…")
    retrievers = {}
    for name in VARIANTS:
        t0 = time.time()
        retrievers[name] = get_retriever(name)
        print(f"  {name:12s} built in {time.time()-t0:.1f}s")

    all_results: dict[str, list[dict]] = {}

    for variant in VARIANTS:
        print(f"\n=== Variant: {variant} ===")
        r = retrievers[variant]
        per_case = []
        for i, case in enumerate(DATASET, 1):
            ctx = r.retrieve(case["input"])
            try:
                ans = answer(runner, ctx, case["input"])
            except Exception as e:
                ans = f"[ERROR] {e}"
            recall, missing = fact_recall(ans, case["expected_facts"])
            per_case.append({
                "id": case["id"],
                "category": case["category"],
                "input": case["input"],
                "context": ctx,
                "answer": ans,
                "expected_facts": case["expected_facts"],
                "fact_recall": recall,
                "missing": missing,
            })
            print(f"  [{i:2d}/{len(DATASET)}] {case['id']:25s} recall={recall:.2f}")
        all_results[variant] = per_case

    # LLM-as-Judge per variant
    print("\nRunning LLM-as-Judge for each variant…")
    judge = AgentAsJudgeEval(
        name="ablation-judge",
        model=OpenRouter(id=JUDGE_MODEL, api_key=api_key, base_url=BASE_URL),
        criteria=(
            "Grade the answer 1-10 on: (a) factual accuracy vs RVP knowledge, "
            "(b) helpfulness/concrete next step, (c) correct escalation tier for "
            "refunds>$200/RMA/security to L2, (d) language match. Pass threshold 7."
        ),
        threshold=7.0,
        telemetry=False,
    )

    leaderboard = []
    for variant, cases in all_results.items():
        judge_cases = [{"input": c["input"], "output": c["answer"]} for c in cases]
        res = judge.run(cases=judge_cases, print_summary=False)
        passes = []
        for c, ev in zip(cases, res.results or []):
            c["judge_passed"] = bool(getattr(ev, "passed", False))
            c["judge_reason"] = getattr(ev, "reason", None)
            passes.append(c["judge_passed"])

        recall_avg = mean(c["fact_recall"] for c in cases)
        pass_rate = sum(passes) / len(passes) if passes else 0.0
        composite = 0.5 * recall_avg + 0.5 * pass_rate
        leaderboard.append({
            "variant": variant,
            "avg_fact_recall": round(recall_avg, 3),
            "judge_pass_rate": round(pass_rate, 3),
            "composite": round(composite, 3),
        })
        print(f"  {variant:12s} recall={recall_avg:.3f}  judge_pass={pass_rate:.3f}  composite={composite:.3f}")

    leaderboard.sort(key=lambda x: -x["composite"])

    print("\n========== LEADERBOARD ==========")
    print(f"{'rank':4s}  {'variant':12s}  {'recall':>7s}  {'judge':>7s}  {'composite':>10s}")
    for i, row in enumerate(leaderboard, 1):
        print(f"{i:4d}  {row['variant']:12s}  {row['avg_fact_recall']:7.3f}  {row['judge_pass_rate']:7.3f}  {row['composite']:10.3f}")

    out = {"leaderboard": leaderboard, "results": all_results}
    out_path = ROOT / "tmp_db" / "ablation_results.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nFull results -> {out_path}")
    return out


if __name__ == "__main__":
    run()
