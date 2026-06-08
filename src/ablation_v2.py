"""Ablation v2: focus on top retrievers (graph, bm25, graph_bm25) and add a
language-compliance metric (Korean+English only, no CJK Hanja).

This intentionally trims the variant list to the winners from ablation v1 so
each combination can be measured with more statistical signal in less wall time.
"""
from __future__ import annotations

import json
import os
import sys
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
from lang_validator import validate
from retrievers import get_retriever

VARIANTS = ["bm25", "graph", "graph_bm25"]

SYSTEM = (
    "You are the Robot Vision Platform (RVP) customer support agent. "
    "Answer the customer using ONLY the context below. "
    "Match the customer's language: reply in Korean for Korean queries, English for English. "
    "Do NOT use Chinese, Japanese, or any CJK Hanja characters. "
    "Be concise; cite the relevant section heading when possible."
)


def make_runner():
    api_key = os.environ["OPENROUTER_API_KEY"]
    return Agent(
        name="rvp-runner",
        model=OpenRouter(id=PRIMARY_MODEL, api_key=api_key, base_url=BASE_URL),
        instructions=[SYSTEM],
        markdown=False,
        telemetry=False,
    )


def run() -> dict:
    api_key = os.environ["OPENROUTER_API_KEY"]
    runner = make_runner()

    print("Pre-building retrievers…")
    retrievers = {v: get_retriever(v) for v in VARIANTS}

    all_results: dict[str, list[dict]] = {}

    for variant in VARIANTS:
        print(f"\n=== Variant: {variant} ===")
        r = retrievers[variant]
        per_case = []
        for i, case in enumerate(DATASET, 1):
            ctx = r.retrieve(case["input"])
            try:
                resp = runner.run(
                    f"## Context\n{ctx}\n\n## Customer message\n{case['input']}\n\n## Your answer:"
                )
                ans = (resp.content if hasattr(resp, "content") else str(resp)) or ""
            except Exception as e:
                ans = f"[ERROR] {e}"

            recall, missing = fact_recall(ans, case["expected_facts"])
            lang = validate(ans)
            per_case.append({
                "id": case["id"],
                "category": case["category"],
                "input": case["input"],
                "answer": ans,
                "fact_recall": recall,
                "missing": missing,
                "language_ok": lang.ok,
                "language_violations": lang.violations,
            })
            flag = "✓" if lang.ok else f"✗{len(lang.violations)}"
            print(f"  [{i:2d}/{len(DATASET)}] {case['id']:25s} recall={recall:.2f}  lang={flag}")
        all_results[variant] = per_case

    print("\nRunning LLM-as-Judge…")
    judge = AgentAsJudgeEval(
        name="ablation-v2-judge",
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
        jc = [{"input": c["input"], "output": c["answer"]} for c in cases]
        res = judge.run(cases=jc, print_summary=False)
        passes = []
        for c, ev in zip(cases, res.results or []):
            c["judge_passed"] = bool(getattr(ev, "passed", False))
            passes.append(c["judge_passed"])

        recall_avg = mean(c["fact_recall"] for c in cases)
        judge_pass = sum(passes) / len(passes) if passes else 0.0
        lang_pass = sum(1 for c in cases if c["language_ok"]) / len(cases)
        composite = round((recall_avg + judge_pass + lang_pass) / 3, 3)
        leaderboard.append({
            "variant": variant,
            "fact_recall": round(recall_avg, 3),
            "judge_pass": round(judge_pass, 3),
            "language_ok_rate": round(lang_pass, 3),
            "composite": composite,
        })

    leaderboard.sort(key=lambda x: -x["composite"])

    print("\n========== LEADERBOARD (graph vs bm25 vs graph+bm25) ==========")
    print(f"{'rank':4s}  {'variant':12s}  {'recall':>7s}  {'judge':>7s}  {'lang_ok':>8s}  {'composite':>10s}")
    for i, row in enumerate(leaderboard, 1):
        print(f"{i:4d}  {row['variant']:12s}  {row['fact_recall']:7.3f}  "
              f"{row['judge_pass']:7.3f}  {row['language_ok_rate']:8.3f}  {row['composite']:10.3f}")

    # Surface every Hanja violation observed
    print("\n========== LANGUAGE VIOLATIONS DETECTED ==========")
    any_violation = False
    for variant, cases in all_results.items():
        for c in cases:
            if not c["language_ok"]:
                any_violation = True
                print(f"  [{variant}] {c['id']}: {c['language_violations']}")
                snippet = c["answer"][:200].replace("\n", " ")
                print(f"      → {snippet}…")
    if not any_violation:
        print("  (none — all variants produced Korean+English only)")

    out = {"leaderboard": leaderboard, "results": all_results}
    out_path = ROOT / "tmp_db" / "ablation_v2.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nFull results -> {out_path}")
    return out


if __name__ == "__main__":
    run()
