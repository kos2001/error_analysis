"""Re-run only the LLM-as-Judge phase using cached agent answers from eval_results.json."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agno.eval.agent_as_judge import AgentAsJudgeEval
from agno.models.openrouter import OpenRouter

from agent import BASE_URL, JUDGE_MODEL  # noqa: E402

api_key = os.environ["OPENROUTER_API_KEY"]
data = json.load(open(ROOT / "tmp_db" / "eval_results.json"))
cases = data["cases"]

judge_cases = [{"input": c["input"], "output": c["answer"]} for c in cases]

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
res = judge.run(cases=judge_cases, print_summary=True, print_results=False)

print("\nresult.results attrs:", [a for a in dir(res.results[0]) if not a.startswith('_')] if res and res.results else None)
for c, ev in zip(cases, res.results):
    c["judge_score"] = getattr(ev, "score", None)
    c["judge_passed"] = getattr(ev, "passed", None)
    c["judge_reasoning"] = getattr(ev, "reasoning", None) or getattr(ev, "explanation", None)

scores = [c["judge_score"] for c in cases if c.get("judge_score") is not None]
passed = [c["judge_passed"] for c in cases if c.get("judge_passed") is not None]
avg_recall = sum(c["fact_recall"] for c in cases) / len(cases)

summary = {
    "n": len(cases),
    "avg_fact_recall": round(avg_recall, 3),
    "avg_judge_score": round(sum(scores) / len(scores), 3) if scores else None,
    "min_judge_score": min(scores) if scores else None,
    "max_judge_score": max(scores) if scores else None,
    "judge_pass_rate": round(sum(passed) / len(passed), 3) if passed else None,
}
data["summary"] = summary
json.dump(data, open(ROOT / "tmp_db" / "eval_results.json", "w"), indent=2, ensure_ascii=False)

print("\n=== FINAL SUMMARY ===")
print(json.dumps(summary, indent=2, ensure_ascii=False))
print("\nPer-case:")
for c in cases:
    print(f"  {c['id']:25s} recall={c['fact_recall']:.2f}  judge={c.get('judge_score')}  passed={c.get('judge_passed')}")
