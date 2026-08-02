"""CJK 안전망 검증 — 멀쩡한 줄을 건드리지 않는가.

실제로 났던 사고: 한자 한 글자 때문에 문서 전체가 LLM 재작성으로 넘어가면서
무관한 문장의 "펌웨어" 가 "펌 - 만웨어" 로 깨졌다. 여기서 막는 것은 그 재발이다.

LLM 을 부르지 않는다 — 재작성기를 가짜로 갈아 끼워 "어떤 줄에 손대는가" 와
"검증에 실패하면 어떻게 되는가" 만 본다.

실행:
    .venv/bin/python tests/test_lang_validator.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import lang_validator as LV  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeAgent:
    """지정한 대로 답하는 가짜 재작성기."""
    def __init__(self, reply):
        self.reply = reply
        self.seen: list[str] = []

    def run(self, text: str):
        self.seen.append(text)
        out = self.reply(text) if callable(self.reply) else self.reply
        return type("R", (), {"content": out})()


def with_agent(agent):
    LV._REWRITER = agent
    LV._get_rewriter = lambda: agent


DOC = """### 예상 근본원인
tCKSRX에 guard band 추가로 펌웨어 타이밍 마진을 확보한다.
디바이스가 自动 으로 재시작됩니다.
### 권장 해결책
펌웨어 버전 DPHY.1.7.687 로 업데이트한다."""


def test_untouched_lines() -> None:
    print("\n[멀쩡한 줄은 건드리지 않는다]")
    # 재작성기가 무엇을 받든 한자 없는 한국어를 돌려준다고 가정
    agent = FakeAgent(lambda t: t.replace("自动", "자동"))
    with_agent(agent)
    r = LV.validate_and_fix(DOC)
    check("위반 감지", not r.ok and "自" in "".join(r.violations))
    out_lines = (r.rewritten or "").split("\n")
    src_lines = DOC.split("\n")
    check("한자 있던 줄만 LLM 에 전달", agent.seen == [src_lines[2]],
          f"전달된 줄: {agent.seen}")
    for i in (0, 1, 3, 4):
        check(f"{i}번 줄 원문 유지", out_lines[i] == src_lines[i],
              f"{src_lines[i]!r} → {out_lines[i]!r}")
    check("펌웨어 표기 보존", out_lines.count("펌웨어 버전 DPHY.1.7.687 로 업데이트한다.") == 1
          and "펌 - 만웨어" not in (r.rewritten or ""))
    check("한자 제거됨", not LV.find_violations(r.rewritten or ""))


def test_bad_rewrite_is_rejected() -> None:
    print("\n[나쁜 재작성은 버린다]")
    # (a) 한자가 그대로 남은 응답
    with_agent(FakeAgent("디바이스가 自动 으로 재시작됩니다."))
    r = LV.validate_and_fix(DOC)
    check("한자가 남으면 마스킹으로 대체", "⟦?⟧" in (r.rewritten or ""), r.rewritten or "")
    check("마스킹해도 나머지 줄은 원문", "guard band 추가로 펌웨어" in (r.rewritten or ""))

    # (b) 길이가 크게 변한 응답(요약·부연) — 내용이 바뀐 것으로 보고 거부
    with_agent(FakeAgent("자동."))
    r = LV.validate_and_fix(DOC)
    check("길이가 크게 줄면 거부", "⟦?⟧" in (r.rewritten or ""), r.rewritten or "")
    with_agent(FakeAgent("자동으로 재시작됩니다. " * 12))
    r = LV.validate_and_fix(DOC)
    check("길이가 크게 늘면 거부", "⟦?⟧" in (r.rewritten or ""), (r.rewritten or "")[:80])

    # (c) 재작성기 자체가 죽은 경우
    class Boom:
        def run(self, t): raise RuntimeError("no api key")
    with_agent(Boom())
    r = LV.validate_and_fix(DOC)
    check("재작성 실패 시 마스킹본", "⟦?⟧" in (r.rewritten or ""), (r.rewritten or "")[:80])
    check("실패해도 원문 문장은 남는다", "펌웨어 버전 DPHY.1.7.687" in (r.rewritten or ""))


def test_clean_text() -> None:
    print("\n[한자 없는 문서]")
    called = []
    with_agent(FakeAgent(lambda t: called.append(t) or t))
    r = LV.validate_and_fix("펌웨어 타이밍 마진을 확보한다. Error E033.")
    check("ok=True", r.ok)
    check("LLM 호출 안 함", not called)
    check("rewritten 없음", r.rewritten is None)


def test_notes() -> None:
    print("\n[무엇을 했는지 남긴다]")
    with_agent(FakeAgent(lambda t: t.replace("自动", "자동")))
    r = LV.validate_and_fix(DOC)
    check("notes 에 처리 내역", "재작성" in r.notes and "원문 유지" in r.notes, r.notes)


if __name__ == "__main__":
    test_untouched_lines()
    test_bad_rewrite_is_rejected()
    test_clean_text()
    test_notes()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
