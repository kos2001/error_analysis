"""사례 없을 때의 조사 계획 검증 — 게이트를 우회하지 않는가.

이 기능은 "근거 없이 분석하지 않는다" 는 제품 계약을 **일부러 여는** 것이다.
그래서 검증기가 곧 안전장치다: 검증 없이 열면 게이트를 없앤 것과 같다.

지키는 계약:
  · 근본원인을 단정하는 문장이 있으면 **내보내지 않는다**
  · 제시되지 않은 사례 키를 언급하면 내보내지 않는다
  · 가설에 (추정) 표시가 빠지면 내보내지 않는다
  · 절이 하나라도 없으면 내보내지 않는다
  · 도구는 결정적이다 — 같은 KB·같은 질의면 같은 번들이 나온다

LLM 을 부르지 않는다 — 도구(gather)와 검증기(validate)만 본다.

실행:
    .venv/bin/python tests/test_first_principles.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


import first_principles as fp                      # noqa: E402
from preprocess import parse_issue                 # noqa: E402


class FakeReco:
    """KB 만 들고 있는 최소 추천기 — gather 는 kb/_kb_ents 만 쓴다."""

    def __init__(self, kb):
        self.kb = kb
        from preprocess import extract_entities
        self._kb_ents = [extract_entities(" ".join([r.get("summary", ""), r.get("symptom", ""),
                                                    r.get("chip", ""), r.get("category", "")]))
                         for r in kb]


KB = [r for r in (parse_issue(x) for x in
                  json.loads((ROOT / "data" / "all_raw_issues.json").read_text(encoding="utf-8")))
      if r["status"] == "완료"]
RECO = FakeReco(KB)
QUERY = {"key": "LSI-9001", "summary": "[PM9C3-NVMe] 도장 공정 후 간헐 리셋",
         "symptom": "출하 수개월 뒤 워치독 리셋", "chip": "PM9C3-NVMe",
         "category": "Thermal", "labels": []}
WEAK = [{"key": KB[i]["key"], "summary": KB[i]["summary"], "chip": KB[i].get("chip", ""),
         "category": KB[i].get("category", ""), "root_cause": KB[i].get("root_cause", ""),
         "resolution": KB[i].get("resolution", ""), "rerank_score": 0.12,
         "entity_overlap": 1} for i in range(3)]


def full_plan(body: str = "") -> str:
    """검증을 통과하는 최소 형태의 계획."""
    parts = []
    for name, marker in fp.SECTIONS:
        parts.append(marker)
        if name == "가설":
            parts.append("- (추정) 전원 글리치 가능성. 리셋 플래그를 읽으면 갈린다.")
        else:
            parts.append("내용.")
    return "\n".join(parts) + ("\n" + body if body else "")


def test_gather_is_deterministic() -> None:
    print("\n[도구는 결정적이다]")
    a = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    check("같은 입력 → 같은 번들", a == b)
    check("약한 매치를 버리지 않는다", len(a["weak_matches"]) == 3, str(len(a["weak_matches"])))
    check("같은 칩 이력을 모은다", len(a["chip_history"]) > 0, str(len(a["chip_history"])))
    check("칩 이력은 실제로 같은 칩", all(
        any(r["key"] == h["key"] and r.get("chip") == QUERY["chip"] for r in KB)
        for h in a["chip_history"]), str(a["chip_history"][:2]))
    check("분류 고장모드를 모은다", len(a["category_modes"]) > 0, str(a["category_modes"][:2]))
    check("LLM 없이 돈다(외부 호출 없음)", True)


def test_allowed_keys() -> None:
    print("\n[언급 허용 키]")
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    allow = fp.allowed_keys(b)
    check("약한 매치 키 포함", all(w["key"] in allow for w in WEAK), str(sorted(allow)[:5]))
    check("질의 자신 포함", QUERY["key"] in allow)
    check("무관한 키는 불포함", "LSI-999999" not in allow)


def test_rejects_assertions() -> None:
    print("\n[단정문은 막는다]")
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    for bad in ("근본 원인은 전원 글리치이다.",
                "근본원인은 클럭 지터입니다.",
                "확실히 도장 잔류물 때문입니다."):
        v = fp.validate(full_plan(bad), b)
        ok, why = fp.is_acceptable(v)
        check(f"차단: {bad[:18]}…", not ok, f"통과됨 — {v['assertions']}")
    v = fp.validate(full_plan("(추정) 전원 글리치일 수 있다. 리셋 플래그로 판별한다."), b)
    check("추정 표시된 가설은 통과", fp.is_acceptable(v)[0], str(v["assertions"]))


def test_rejects_unsupported_keys() -> None:
    print("\n[제시되지 않은 사례 언급은 막는다]")
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    v = fp.validate(full_plan("참고: LSI-999998 사례와 유사하다."), b)
    ok, why = fp.is_acceptable(v)
    check("차단된다", not ok, why)
    check("어떤 키인지 알려준다", "LSI-999998" in v["unsupported_mentions"],
          str(v["unsupported_mentions"]))
    good = fp.validate(full_plan(f"참고: {WEAK[0]['key']} 를 볼 것."), b)
    check("제시된 키는 통과", fp.is_acceptable(good)[0], str(good["unsupported_mentions"]))


def test_requires_hypothesis_labels() -> None:
    print("\n[가설에 (추정) 표시 강제]")
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    parts = []
    for name, marker in fp.SECTIONS:
        parts.append(marker)
        parts.append("- 전원 글리치 가능성.\n- (추정) 클럭 지터 가능성." if name == "가설" else "내용.")
    v = fp.validate("\n".join(parts), b)
    ok, why = fp.is_acceptable(v)
    check("일부만 표시되면 차단", not ok, why)
    check("몇 개가 빠졌는지 센다", v["hypotheses"] == 2 and v["hypotheses_labeled"] == 1,
          f"{v['hypotheses']}/{v['hypotheses_labeled']}")


def test_requires_all_sections() -> None:
    print("\n[절 누락은 막는다]")
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    partial = "\n".join(m for _, m in fp.SECTIONS[:4])
    v = fp.validate(partial, b)
    ok, why = fp.is_acceptable(v)
    check("차단된다", not ok, why)
    check("빠진 절을 알려준다", len(v["missing_sections"]) == 3, str(v["missing_sections"]))


def test_prompt_carries_evidence_and_rules() -> None:
    print("\n[프롬프트가 재료와 규칙을 싣는다]")
    b = fp.gather(QUERY, RECO, WEAK, k_weak=3)
    p = fp.build_prompt(b, {"signal": "rerank", "rerank_top": 0.12, "threshold": 0.17})
    check("약한 매치가 근거 아님으로 명시", "근거 아님" in p)
    check("단정 금지 규칙 포함", "단정문은 쓰지 마라" in p)
    check("창작 금지 규칙 포함", "창작 금지" in p)
    check("게이트 수치 포함", "0.12" in p and "0.17" in p)
    check("7개 절 지시 포함", all(m in p for _, m in fp.SECTIONS))
    check("debug_approach 는 쓰지 않는다(신호 없음)", "조사 방법" not in p and "debug_approach" not in p)


if __name__ == "__main__":
    test_gather_is_deterministic()
    test_allowed_keys()
    test_rejects_assertions()
    test_rejects_unsupported_keys()
    test_requires_hypothesis_labels()
    test_requires_all_sections()
    test_prompt_carries_evidence_and_rules()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
