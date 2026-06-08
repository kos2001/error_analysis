"""Demonstrate that the agent remembers user facts across sessions."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent import build_agent  # noqa: E402


def main():
    user = "kos-2001"

    print("\n--- Session 1: customer introduces device + plan ---")
    a1 = build_agent(user_id=user, session_id="s1")
    a1.print_response(
        "안녕하세요. 저는 RVP-Cam-200을 Pro 플랜으로 쓰고 있습니다. 한국에 있어요. "
        "오늘 LED가 계속 주황색이에요."
    )

    print("\n--- Session 2 (new session, same user): no device repeated ---")
    a2 = build_agent(user_id=user, session_id="s2")
    a2.print_response("E033 떴는데, 제 디바이스 모델 기준으로 어떻게 해야 하나요?")

    print("\n--- Stored memories ---")
    mems = a2.get_user_memories(user_id=user) if hasattr(a2, "get_user_memories") else []
    for m in mems or []:
        print(" -", getattr(m, "memory", m))


if __name__ == "__main__":
    main()
