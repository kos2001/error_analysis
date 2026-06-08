"""Iterative prompt optimization.

Loop:
    1. Run the agent (graph retriever fixed) with current system prompt on the
       20-case eval dataset.
    2. Score with fact_recall + LLM-as-Judge + language compliance.
    3. If composite improved → keep new prompt as best.
    4. If no improvement for 2 consecutive iterations → stop.
    5. Otherwise: ask a "refiner" LLM to rewrite the prompt based on the failures.

Outputs:
    tmp_db/prompt_optimization.json   — every iteration: prompt + scores + failures
    tmp_db/best_prompt.txt            — the best prompt found
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
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
from eval_dataset_large import DATASET_LARGE
from evaluate import fact_recall
from lang_validator import validate
from retrievers import GraphRetriever

GRAPH = GraphRetriever()

INITIAL_PROMPT = (
    "You are the Robot Vision Platform (RVP) customer support agent. "
    "Answer the customer using ONLY the context below. "
    "Match the customer's language: reply in Korean for Korean queries, English for English. "
    "Do NOT use Chinese, Japanese, or any CJK Hanja characters. "
    "Be concise; cite the relevant section heading when possible."
)

MAX_ITERS = 6
PATIENCE = 2  # stop after this many consecutive non-improvements


@dataclass
class IterResult:
    iter: int
    prompt: str
    fact_recall: float
    judge_pass: float
    language_ok: float
    composite: float
    failures: list[dict]


def make_runner(prompt: str) -> Agent:
    return Agent(
        name="rvp-runner",
        model=OpenRouter(id=PRIMARY_MODEL, api_key=os.environ["OPENROUTER_API_KEY"], base_url=BASE_URL),
        instructions=[prompt],
        markdown=False,
        telemetry=False,
    )


def make_judge() -> AgentAsJudgeEval:
    return AgentAsJudgeEval(
        name="prompt-opt-judge",
        model=OpenRouter(id=JUDGE_MODEL, api_key=os.environ["OPENROUTER_API_KEY"], base_url=BASE_URL),
        criteria=(
            "Grade the answer 1-10 on: (a) factual accuracy vs RVP knowledge, "
            "(b) helpfulness/concrete next step, (c) correct escalation tier "
            "(refunds>$200 / RMA / security → L2), (d) language match, "
            "(e) no Chinese characters. Pass threshold 7."
        ),
        threshold=7.0,
        telemetry=False,
    )


def make_refiner() -> Agent:
    return Agent(
        name="prompt-refiner",
        model=OpenRouter(id=PRIMARY_MODEL, api_key=os.environ["OPENROUTER_API_KEY"], base_url=BASE_URL),
        instructions=[
            "You are a senior prompt engineer for customer support LLM agents.",
            "Given the CURRENT system prompt and a list of FAILURE cases (question + agent's answer + expected facts + judge's reasoning), "
            "rewrite the system prompt to fix the failure patterns.",
            "Constraints on the new prompt:",
            "  • Keep it under 300 words.",
            "  • Keep it in English.",
            "  • Stay general — do not hardcode answers, just steer behavior.",
            "  • Preserve hard rules: language match, no CJK Hanja, escalation tiers.",
            "  • Add specific instructions only where failures show a pattern (e.g. 'always include the exact numeric threshold').",
            "Output ONLY the new system prompt, no preamble.",
        ],
        markdown=False,
        telemetry=False,
    )


def evaluate_prompt(prompt: str, iter_idx: int) -> IterResult:
    print(f"\n--- Iteration {iter_idx} ---")
    print(f"Prompt (head): {prompt[:120]}…")
    runner = make_runner(prompt)
    per_case = []
    for i, case in enumerate(DATASET_LARGE, 1):
        ctx = GRAPH.retrieve(case["input"], k=3)
        try:
            resp = runner.run(f"## Context\n{ctx}\n\n## Customer message\n{case['input']}\n\n## Your answer:")
            ans = (resp.content if hasattr(resp, "content") else str(resp)) or ""
        except Exception as e:
            ans = f"[ERROR] {e}"
        recall, missing = fact_recall(ans, case["expected_facts"])
        lang = validate(ans)
        per_case.append({
            "id": case["id"],
            "input": case["input"],
            "answer": ans,
            "expected_facts": case["expected_facts"],
            "fact_recall": recall,
            "missing": missing,
            "language_ok": lang.ok,
            "language_violations": lang.violations,
        })
        print(f"  [{i:2d}/{len(DATASET_LARGE)}] {case['id']:25s} recall={recall:.2f}  lang={'✓' if lang.ok else '✗'}")

    # Judge in batch
    judge = make_judge()
    jc = [{"input": c["input"], "output": c["answer"]} for c in per_case]
    res = judge.run(cases=jc, print_summary=False)
    for c in per_case:
        c["judge_passed"] = False
        c["judge_reason"] = ""
    for c, ev in zip(per_case, res.results or []):
        c["judge_passed"] = bool(getattr(ev, "passed", False))
        c["judge_reason"] = (getattr(ev, "reason", None) or "")[:300]

    recall_avg = mean(c["fact_recall"] for c in per_case)
    judge_avg = sum(c.get("judge_passed", False) for c in per_case) / len(per_case)
    lang_avg = sum(c["language_ok"] for c in per_case) / len(per_case)
    composite = round((recall_avg + judge_avg + lang_avg) / 3, 4)

    print(f"  → recall={recall_avg:.3f}  judge={judge_avg:.3f}  lang={lang_avg:.3f}  composite={composite:.4f}")

    failures = [
        c for c in per_case
        if c["fact_recall"] < 1.0 or not c.get("judge_passed", True) or not c["language_ok"]
    ]
    return IterResult(
        iter=iter_idx,
        prompt=prompt,
        fact_recall=round(recall_avg, 4),
        judge_pass=round(judge_avg, 4),
        language_ok=round(lang_avg, 4),
        composite=composite,
        failures=failures,
    )


def refine_prompt(current_prompt: str, failures: list[dict]) -> str:
    refiner = make_refiner()
    # Show up to 8 failure examples to the refiner
    sample = failures[:8]
    bullet_list = "\n\n".join(
        f"CASE {c['id']}\n"
        f"  question: {c['input']}\n"
        f"  agent answer: {c['answer'][:400]}\n"
        f"  expected facts: {c['expected_facts']}\n"
        f"  missing facts: {c.get('missing')}\n"
        f"  judge reason: {c.get('judge_reason', '(judge n/a)')[:300]}"
        for c in sample
    )
    user_msg = (
        f"## CURRENT SYSTEM PROMPT\n{current_prompt}\n\n"
        f"## FAILURE CASES ({len(failures)} total, showing {len(sample)})\n{bullet_list}\n\n"
        f"## TASK\nRewrite the system prompt to address these failure patterns. Output the prompt only."
    )
    resp = refiner.run(user_msg)
    return (resp.content if hasattr(resp, "content") else str(resp)).strip()


def run() -> dict:
    history: list[dict] = []
    best = None
    no_improve = 0
    prompt = INITIAL_PROMPT

    for it in range(MAX_ITERS):
        result = evaluate_prompt(prompt, it)
        history.append({
            "iter": result.iter,
            "prompt": result.prompt,
            "fact_recall": result.fact_recall,
            "judge_pass": result.judge_pass,
            "language_ok": result.language_ok,
            "composite": result.composite,
            "n_failures": len(result.failures),
        })

        if best is None or result.composite > best.composite:
            improvement = (result.composite - best.composite) if best else float("inf")
            best = result
            no_improve = 0
            print(f"  ✓ new best: composite={result.composite:.4f}  (Δ={improvement:+.4f})")
        else:
            no_improve += 1
            print(f"  ✗ no improvement ({no_improve}/{PATIENCE}). best stays at {best.composite:.4f}")

        if no_improve >= PATIENCE:
            print(f"\nEarly stop: no improvement for {PATIENCE} iterations.")
            break

        if not result.failures:
            print("\nNo failures left — perfect score. Stopping.")
            break

        print("  → refining prompt based on failures…")
        prompt = refine_prompt(prompt, result.failures)

    # Persist
    out = {
        "history": history,
        "best_iter": best.iter if best else None,
        "best_composite": best.composite if best else None,
        "best_prompt": best.prompt if best else None,
    }
    (ROOT / "tmp_db" / "prompt_optimization.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )
    (ROOT / "tmp_db" / "best_prompt.txt").write_text(best.prompt if best else "")

    print("\n========== OPTIMIZATION HISTORY ==========")
    print(f"{'iter':4s}  {'recall':>7s}  {'judge':>7s}  {'lang':>7s}  {'composite':>10s}  {'fails':>6s}")
    for h in history:
        marker = " *" if best and h["iter"] == best.iter else ""
        print(f"{h['iter']:4d}  {h['fact_recall']:7.3f}  {h['judge_pass']:7.3f}  "
              f"{h['language_ok']:7.3f}  {h['composite']:10.4f}  {h['n_failures']:6d}{marker}")
    print(f"\nBest prompt (iter {best.iter}):\n{best.prompt}")
    return out


if __name__ == "__main__":
    run()
