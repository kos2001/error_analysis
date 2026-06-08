"""Self-validation: run the agent on the synthetic dataset and score with LLM-as-Judge.

Two complementary checks per case:
    1. Fact recall — string-level check that expected facts appear in the answer.
    2. LLM-as-Judge — Agno AgentAsJudgeEval grades the answer (1-10) on:
       accuracy, grounding in KB, helpfulness, escalation correctness.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agno.eval.agent_as_judge import AgentAsJudgeEval
from agno.models.openrouter import OpenRouter

from agent import BASE_URL, JUDGE_MODEL, build_agent  # noqa: E402
from eval_dataset import DATASET  # noqa: E402


def fact_recall(answer: str, expected_facts: list[str]) -> tuple[float, list[str]]:
    answer_l = answer.lower()
    hits = [f for f in expected_facts if f.lower() in answer_l]
    score = len(hits) / max(1, len(expected_facts))
    missing = [f for f in expected_facts if f not in hits]
    return score, missing


def run_eval(limit: int | None = None) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY env var required")

    # Each case gets a fresh session so memory from one case doesn't leak into another's KB lookup,
    # but they share the user_id so user memories accumulate.
    cases = DATASET[: limit] if limit else DATASET

    results = []
    judge_cases = []

    print(f"Running {len(cases)} validation cases against the agent…\n")
    for i, case in enumerate(cases, 1):
        agent = build_agent(user_id="eval-user", session_id=f"eval-{case['id']}")
        try:
            resp = agent.run(case["input"])
            answer = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            answer = f"[AGENT ERROR] {e}"

        recall, missing = fact_recall(answer, case["expected_facts"])
        print(f"[{i}/{len(cases)}] {case['id']}  fact_recall={recall:.2f}  missing={missing}")

        results.append({
            "id": case["id"],
            "category": case["category"],
            "input": case["input"],
            "answer": answer,
            "expected_facts": case["expected_facts"],
            "fact_recall": recall,
            "missing_facts": missing,
        })
        judge_cases.append({"input": case["input"], "output": answer})

    # LLM-as-Judge evaluation across all cases
    print("\nRunning LLM-as-Judge evaluation…")
    judge = AgentAsJudgeEval(
        name="RVP Support Judge",
        model=OpenRouter(id=JUDGE_MODEL, api_key=api_key, base_url=BASE_URL),
        criteria=(
            "Grade the customer-support answer on a 1-10 scale considering:\n"
            "  (a) Accuracy vs. the RVP knowledge base (error codes, billing, escalation policy).\n"
            "  (b) Helpfulness — does it give the customer a concrete next step?\n"
            "  (c) Escalation correctness — refunds >$200, RMA, security must escalate to L2.\n"
            "  (d) Language match — reply should match the customer's language.\n"
            "Penalize fabrication, missing key facts, or wrong escalation tier."
        ),
        threshold=7.0,
    )
    judge_result = judge.run(cases=judge_cases, print_summary=True, print_results=False)

    # Stitch per-case judge scores back in
    if judge_result and getattr(judge_result, "results", None):
        for r, ev in zip(results, judge_result.results):
            r["judge_score"] = getattr(ev, "score", None)
            r["judge_passed"] = getattr(ev, "passed", None)
            r["judge_reasoning"] = getattr(ev, "reasoning", None) or getattr(ev, "explanation", None)

    avg_recall = sum(r["fact_recall"] for r in results) / len(results)
    judge_scores = [r.get("judge_score") for r in results if r.get("judge_score") is not None]
    avg_judge = sum(judge_scores) / len(judge_scores) if judge_scores else None
    pass_judge = sum(1 for s in judge_scores if s >= 7) if judge_scores else 0

    summary = {
        "n": len(results),
        "avg_fact_recall": round(avg_recall, 3),
        "avg_judge_score": round(avg_judge, 3) if avg_judge is not None else None,
        "judge_pass_rate": round(pass_judge / len(judge_scores), 3) if judge_scores else None,
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    out_path = ROOT / "tmp_db" / "eval_results.json"
    out_path.write_text(json.dumps({"summary": summary, "cases": results}, indent=2, ensure_ascii=False))
    print(f"\nFull results -> {out_path}")
    return summary


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_eval(limit)
