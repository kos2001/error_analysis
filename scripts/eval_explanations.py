"""심층 분석(설명) 품질 측정 — 이 제품의 주 산출물인데 한 번도 재본 적이 없다.

검색은 P@1 로 재 왔지만 **사용자가 실제로 읽는 것은 설명**이다. 근거를 벗어난
주장이나 "상황에 따라 다릅니다" 류의 무해한 헛소리는 P@1 로 잡히지 않는다.

두 층으로 잰다:

1. **결정적 검사**(주축) — LLM 없이, 재현 가능:
   · 인용 무결성: 본문의 `LSI-\\d+` 가 전부 제공된 근거 키 안에 있는가 (환각 인용)
   · 근거 사용률: 제공된 근거 중 실제로 인용된 비율 (안 쓸 거면 왜 넣었나)
   · 섹션 완결성: 프롬프트가 요구한 7개 섹션이 다 있는가
   · 구체성: 검증 방법·수치·명령이 있는가 vs 일반론만 있는가
   · 언어 규칙: 한자 혼입(lang_validator 와 동일 기준)
   · 분량: 너무 짧으면 내용이 없고, 너무 길면 읽히지 않는다

2. **판정자(agent-as-judge)** — 주관 축(근거 충실도·실행 가능성). 참고용이며
   모델이 바뀌면 점수도 흔들리므로 결정적 검사와 분리해 보고한다.

사용:
    set -a && source .env && set +a
    .venv/bin/python scripts/eval_explanations.py --n 8
    .venv/bin/python scripts/eval_explanations.py --n 8 --judge   # LLM 판정 포함
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

# 프롬프트(_explain_prompt_md)가 요구하는 섹션. 표기가 바뀌면 여기도 바꾼다.
REQUIRED_SECTIONS = [
    ("근본원인", "예상 근본원인"),
    ("인과분석", "증상→원인"),
    ("해결단계", "권장 해결 단계"),
    ("우회책", "임시 우회책"),
    ("검증방법", "근본원인 검증 방법"),
    ("사례종합", "사례 종합"),
    ("불확실성", "불확실성"),
]
# 구체성 신호 — 수치·버전·명령·측정 지시가 있으면 실행 가능한 문서로 본다.
CONCRETE_RE = re.compile(
    r"(\d+\s*(?:ms|us|ns|MHz|GHz|mV|V|A|°C|%|Mbps|Gbps|KB|MB|GB))"
    r"|(\bv?\d+\.\d+\.\d+\b)"
    r"|(레지스터|오실로스코프|로직 애널라이저|덤프|로그|재현 절차|측정)")
# 한자 판정은 lang_validator 의 것을 그대로 쓴다 — 직접 만들었더니 한글 음절까지
# 잡아 8/8 건이 "한자 혼입" 으로 오보됐다(계측이 거짓말한 사례).
from lang_validator import find_violations  # noqa: E402


def _stems(keys) -> set[str]:
    """LSI-7-rca → LSI-7 처럼 접미사를 떼어 본문 표기와 맞춘다.

    큐레이션 항목의 키는 "LSI-7-rca" 인데 본문은 자연스럽게 "LSI-7" 로 쓴다.
    이걸 구분 못 하면 정상 인용이 환각으로 잡힌다.
    """
    out = set()
    for k in keys:
        out.add(k)
        m = re.match(r"(LSI-\d+)", str(k))
        if m:
            out.add(m.group(1))
    return out


def analyse(md: str, evidence_keys: list[str], self_key: str = "") -> dict:
    """결정적 검사 — 같은 입력이면 항상 같은 결과."""
    # 서버(_llm_explain / explain_stream)와 **같은** 패턴이어야 한다. \w 를 쓰면
    # 한국어 조사까지 붙어("LSI-157은") 정상 인용이 환각으로 오보된다.
    cited = set(re.findall(r"LSI-\d+", md))
    # 허용: 제공된 근거(접미사 제거 포함) + **질의 이슈 자신**(분석 대상이므로
    # 본문이 자기 번호를 언급하는 것은 환각이 아니다).
    ev = _stems(evidence_keys) | _stems([self_key] if self_key else [])
    sections = {name: (marker in md) for name, marker in REQUIRED_SECTIONS}
    return {
        "chars": len(md),
        "cited_count": len(cited),
        # 환각 인용: 본문이 제공되지 않은 키를 언급 — 신뢰를 직접 깎는다
        "hallucinated_citations": sorted(cited - ev),
        # 근거 사용률: 넣어 준 사례 중 실제로 쓰인 비율
        "evidence_used_ratio": (round(len(cited & _stems(evidence_keys)) / len(evidence_keys), 3)
                                if evidence_keys else None),
        "sections_present": sum(sections.values()),
        "sections_total": len(REQUIRED_SECTIONS),
        "missing_sections": [n for n, ok in sections.items() if not ok],
        "concrete_hits": len(CONCRETE_RE.findall(md)),
        # 근거/배경/추정 구분 — 사례에 없는 내용을 (배경)/(추정)으로 표시하는가.
        # 표시가 없으면 엔지니어가 검증된 사례와 일반 지식을 구분할 수 없다.
        # 모델이 "(배경)" 과 "(배경: ...)" 를 섞어 쓰므로 접두만 센다 — 정확 일치로
        # 세면 실제로 지켜지는 규약을 0 으로 오보한다(실측).
        "labeled_background": len(re.findall(r"\(배경[):]", md)),
        "labeled_estimate": len(re.findall(r"\(추정[):]", md)),
        "han_chars": find_violations(md),
    }


def judge(md: str, ctx: str) -> dict | None:
    """주관 축 — 근거 충실도·실행 가능성. 실패하면 None(측정을 막지 않는다)."""
    import os
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        from agno.agent import Agent
        from agno.models.openrouter import OpenRouter
        from pydantic import BaseModel, Field
        from llm_headers import custom_headers

        class Verdict(BaseModel):
            grounding: int = Field(description="근거 사례로 뒷받침되는 정도 1-10")
            actionability: int = Field(description="바로 실행 가능한 정도 1-10")
            unsupported_claims: list[str] = Field(default_factory=list,
                                                  description="근거 없는 주장(있으면)")
            reason: str = ""

        model_id = os.getenv("RVP_JUDGE_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        agent = Agent(
            model=OpenRouter(id=model_id, api_key=api_key, base_url=base,
                             default_headers=custom_headers() or None),
            output_schema=Verdict, use_json_mode=True, markdown=False, telemetry=False,
            instructions=[
                "너는 LSI 칩/펌웨어 분석 리뷰어다. 관대하게 주지 마라.",
                # 제품 규약과 판정 기준을 맞춘다. 분석문은 사례에 없는 내용을
                # `(배경)`·`(추정)` 으로 **표시하도록** 요구받는다. 표시된 문장을
                # 위반으로 세면, 투명하게 표시할수록 점수가 깎이는 역설이 생긴다
                # (실측: 라벨 도입 후 판정 8.5 → 7.25, 지적 6 → 12건).
                "이 분석문은 규약을 따른다: 사례로 확인되는 주장에는 (LSI-49) 같은 "
                "인라인 인용을 달고, 사례에 없는 배경 설명은 문장 앞에 `(배경)`, "
                "이 이슈에 대한 추정은 `(추정)` 을 붙인다.",
                "grounding: **표시 없이** 사례에서 확인되지 않는 주장을 단정하면 감점한다. "
                "`(배경)`·`(추정)` 으로 올바르게 표시된 문장은 위반이 아니다 — 오히려 "
                "구분이 명확하므로 가점 요소다.",
                "actionability: '상황에 따라 다름' 류의 일반론은 감점, 구체적 절차·수치는 가점.",
                "unsupported_claims 에는 **표시 없이** 단정한, 근거에서 확인 안 되는 "
                "문장만 그대로 옮겨라. (배경)/(추정) 이 붙은 문장은 넣지 마라.",
            ])
        out = agent.run(input=f"## 근거 사례\n{ctx}\n\n## 검토할 분석\n{md}")
        v = out.content
        return {"grounding": v.grounding, "actionability": v.actionability,
                "unsupported": v.unsupported_claims[:3], "reason": v.reason[:200]}
    except Exception as e:
        return {"error": str(e)[:120]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="측정할 이슈 수")
    ap.add_argument("--judge", action="store_true", help="LLM 판정 포함(비용 발생)")
    ap.add_argument("--out", default=str(ROOT / "tmp_db" / "eval_explanations.json"))
    a = ap.parse_args()

    import server                       # 인프로세스로 실제 서빙 경로를 탄다
    st = server._reco_state()
    targets = st["unresolved"][: a.n]

    rows = []
    for i, rec in enumerate(targets, 1):
        res = server._recommend_cached(rec, k=4, exclude_key=rec["key"])
        if not res["matches"] or not res.get("coverage", True):
            print(f"  [{i}/{len(targets)}] {rec['key']:12} 게이트 미통과 — 건너뜀")
            continue
        match_recs = [st["by_key"].get(m["key"], m) for m in res["matches"]]
        hit = server._explain_md_cached(rec, match_recs)
        if hit is None:
            md = "".join(server._generate_explain_md(rec, match_recs))
        else:
            md = hit.get("markdown", "")
        ev = [m["key"] for m in res["matches"]]
        row = {"key": rec["key"], "evidence": ev, "cached": hit is not None,
               **analyse(md, ev, self_key=rec["key"])}
        if a.judge:
            ctx = "\n\n".join(server._case_block(r) for r in match_recs)
            row["judge"] = judge(md, ctx)
        rows.append(row)
        print(f"  [{i}/{len(targets)}] {rec['key']:12} "
              f"섹션 {row['sections_present']}/{row['sections_total']} · "
              f"근거사용 {row['evidence_used_ratio']} · 환각인용 {len(row['hallucinated_citations'])} · "
              f"구체성 {row['concrete_hits']} · {row['chars']}자"
              + (f" · 판정 g{row['judge'].get('grounding')}/a{row['judge'].get('actionability')}"
                 if a.judge and isinstance(row.get("judge"), dict) and "grounding" in row["judge"] else ""))

    if not rows:
        print("측정할 항목이 없습니다."); return 1
    n = len(rows)
    agg = {
        "n": n,
        "섹션_완결률": round(sum(r["sections_present"] / r["sections_total"] for r in rows) / n, 3),
        "근거_사용률": round(sum(r["evidence_used_ratio"] or 0 for r in rows) / n, 3),
        "환각_인용_건수": sum(len(r["hallucinated_citations"]) for r in rows),
        "한자_혼입_건수": sum(1 for r in rows if r["han_chars"]),
        "구체성_평균": round(sum(r["concrete_hits"] for r in rows) / n, 1),
        "배경_표시_평균": round(sum(r["labeled_background"] for r in rows) / n, 1),
        "추정_표시_평균": round(sum(r["labeled_estimate"] for r in rows) / n, 1),
        "분량_평균": round(sum(r["chars"] for r in rows) / n),
        "캐시_히트": sum(1 for r in rows if r["cached"]),
    }
    if a.judge:
        js = [r["judge"] for r in rows if isinstance(r.get("judge"), dict) and "grounding" in r["judge"]]
        if js:
            agg["판정_근거충실도"] = round(sum(j["grounding"] for j in js) / len(js), 2)
            agg["판정_실행가능성"] = round(sum(j["actionability"] for j in js) / len(js), 2)
            agg["판정_근거없는주장"] = sum(len(j.get("unsupported") or []) for j in js)

    print("\n" + "=" * 60)
    for k, v in agg.items():
        print(f"  {k:<18} {v}")
    missing = {}
    for r in rows:
        for m in r["missing_sections"]:
            missing[m] = missing.get(m, 0) + 1
    if missing:
        print(f"  빠진 섹션 빈도      {dict(sorted(missing.items(), key=lambda x: -x[1]))}")
    Path(a.out).write_text(json.dumps({"summary": agg, "rows": rows},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
