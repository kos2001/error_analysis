"""Hermes Agent CLI engine — agno/OpenRouter 에이전트의 대체 엔진.

`hermes chat -q <prompt> -Q --cli` 를 서브프로세스로 호출해 답변을 생성한다.
    - 세션 연속성: hermes가 출력하는 session_id를 (user_id, session_id) 키로
      tmp_db/hermes_sessions.json 에 저장하고, 이후 턴은 --resume 으로 이어간다.
    - KB 검색: agno 에이전트의 search_kb 툴 대신, GraphRetriever로 미리 검색해
      프롬프트에 컨텍스트로 주입한다 (RAG 방식).
    - hermes 자체 툴은 -t none 으로 비활성화 — 순수 생성 엔진으로만 사용.

환경 변수:
    RVP_ENGINE=hermes      backend/server.py 가 이 엔진을 사용
    HERMES_BIN             hermes 실행 파일 경로 (기본: PATH의 hermes)
    HERMES_MODEL           hermes -m 모델 오버라이드 (기본: hermes 기본 모델)
    HERMES_TIMEOUT         호출 타임아웃 초 (기본: 300)
    HERMES_TOOLSETS        hermes 도구 활성화 (예: "web", "web,file"; 기본: "" = 비활성)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from retrievers import GraphRetriever

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "tmp_db"
DB_DIR.mkdir(exist_ok=True)
SESSIONS_FILE = DB_DIR / "hermes_sessions.json"

def _resolve_bin() -> str:
    """HERMES_BIN을 호출 시점에 해석 — 온보딩에서 프로필 설정 시 재시작 없이 반영."""
    return os.path.expanduser(os.getenv("HERMES_BIN") or shutil.which("hermes") or str(
        Path.home() / ".local" / "bin" / "hermes"))


HERMES_BIN = _resolve_bin()  # 호환용 기본값(현재 env 기준)
HERMES_MODEL = os.getenv("HERMES_MODEL", "")
HERMES_TIMEOUT = int(os.getenv("HERMES_TIMEOUT", "300"))
# hermes toolset 활성화 (예: "web", "web,file", "debugging"). 빈 값 = 도구 비활성화(순수 생성).
# headless(-Q --cli)에서도 도구가 실행됨 — 서버 무인 동작이므로 terminal/file 부여는 신중히.
HERMES_TOOLSETS = os.getenv("HERMES_TOOLSETS", "")

# agent.py 의 instructions 와 동일한 페르소나/정책 (첫 턴 프롬프트에 주입)
INSTRUCTIONS = (
    "You are the Robot Vision Platform (RVP) customer support agent.\n"
    "Answer using the [KB] context provided with each question.\n"
    "If the KB context does not contain the answer, say so honestly and offer to escalate to a human (L2).\n"
    "Escalate to L2 specialists for: refund disputes >$200, RMA, security incidents.\n"
    "Be concise, cite the relevant section heading (e.g. 'per §3 Common Error Codes').\n"
    "Reply in the customer's language (Korean or English).\n"
    "Do NOT use Chinese, Japanese, or any CJK Hanja characters — only Korean Hangul, English, digits, punctuation, emoji."
)

_GRAPH = GraphRetriever()


def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_sessions(sessions: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def _invoke(prompt: str, resume: str | None = None) -> tuple[str, str]:
    """hermes를 1회 호출. (answer, hermes_session_id) 반환.

    답변은 stdout, ``session_id: <id>`` 라인은 stderr로 출력된다.
    """
    cmd = [_resolve_bin(), "chat", "-q", prompt, "-Q", "--cli", "-t", HERMES_TOOLSETS]
    if HERMES_MODEL:
        cmd += ["-m", HERMES_MODEL]
    if resume:
        cmd += ["--resume", resume]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=HERMES_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes 호출 실패 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    sid = resume or ""
    for line in proc.stderr.splitlines():
        if line.startswith("session_id:"):
            sid = line.split(":", 1)[1].strip()
    answer = "\n".join(
        line for line in proc.stdout.splitlines()
        if not line.startswith(("session_id:", "Warning:"))
    ).strip()
    return answer, sid


class HermesEngine:
    """backend/server.py 가 사용하는 채팅/단발 생성 인터페이스."""

    def run(self, message: str, user_id: str = "web-user",
            session_id: str = "web-session") -> str:
        """KB 컨텍스트를 주입한 멀티턴 채팅. 세션은 hermes --resume 으로 유지."""
        kb = _GRAPH.retrieve(message, k=3)
        key = f"{user_id}:{session_id}"
        sessions = _load_sessions()
        hermes_sid = sessions.get(key)
        if hermes_sid:
            prompt = f"[KB]\n{kb}\n\n고객 질문: {message}"
        else:
            prompt = f"{INSTRUCTIONS}\n\n[KB]\n{kb}\n\n고객 질문: {message}"
        answer, sid = _invoke(prompt, resume=hermes_sid)
        if sid and sid != hermes_sid:
            sessions[key] = sid
            _save_sessions(sessions)
        return answer

    def complete(self, prompt: str) -> str:
        """세션 없는 단발 생성 (예: /recommend 의 LLM 종합 설명)."""
        answer, _ = _invoke(prompt)
        return answer


if __name__ == "__main__":
    engine = HermesEngine()
    print("Hermes engine ready. Type 'exit' to quit.\n")
    while True:
        try:
            q = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"exit", "quit"}:
            break
        print(engine.run(q))
