"""HTTP 스모크 — 실제 프로세스를 띄워 네트워크로 훑는다.

**왜 따로 있는가.** 다른 테스트는 전부 인프로세스(TestClient/ASGITransport)다. 그래서
`app.mount("/mcp")` 가 슬래시 없는 POST 를 405 로 떨구는 것을 놓쳤다 — 문서대로 설정한
클라이언트가 붙지 못하는데도 테스트는 전부 초록이었다. 마운트·정적 서빙·리다이렉트·
기동 자체는 **실제 소켓으로 찔러야** 보인다.

같은 이유로 프로세스를 `python backend/server.py` 로 띄운다(Dockerfile 의 CMD 와 동일).
그래야 기동 경로에 있던 결함 — 모듈이 두 번 적재되던 문제 — 도 여기서 잡힌다.

LLM·Jira 는 부르지 않는다(예열·폴링 끔). 확인하는 것:
  · 기동하고 /health 가 뜨는가
  · 모듈이 한 번만 적재되는가
  · 인증 없이는 막히고, 로그인하면 통과하는가
  · MCP 가 /mcp 와 /mcp/ 양쪽으로 붙는가
  · SPA 폴백이 API 라우트를 삼키지 않는가

실행:
    .venv/bin/python tests/test_http_smoke.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ci(headers) -> dict:
    """헤더를 소문자 키로 정규화."""
    return {str(k).lower(): v for k, v in dict(headers).items()}


class Client:
    """쿠키를 물고 다니는 최소 클라이언트 — 세션이 HttpOnly 쿠키라 필요하다."""

    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            _NoRedirect())

    def req(self, path: str, method: str = "GET", body: dict | None = None,
            headers: dict | None = None, follow: bool = False) -> tuple[int, str, dict]:
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.opener.handlers[0].cookiejar)
        ) if follow else self.opener
        # 헤더는 대소문자를 가리지 않는다 — HTTP/1.1 은 소문자로 오고 dict() 는 그대로
        # 담는다. h["Location"] 로 찾다 None 을 받고 잠깐 서버를 의심했다.
        try:
            with opener.open(r, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), _ci(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), _ci(e.headers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가지 않는다 — 307 자체를 검사해야 한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    http_error_301 = http_error_302 = http_error_303 = http_error_307 = \
        lambda self, req, fp, code, msg, hdrs: None


def boot(port: int, log: Path, users: Path) -> subprocess.Popen:
    users.write_text("users:\n  - id: admin\n    name: 관리자\n    role: admin\n"
                     "  - id: eng@example.com\n    name: 사용자\n    role: user\n",
                     encoding="utf-8")
    env = {**os.environ,
           "RVP_PORT": str(port), "RVP_HOST": "127.0.0.1",
           "RVP_JIRA_POLL_SEC": "0",      # 외부 호출 없음
           "RVP_PREWARM": "0",            # LLM 비용 없음
           "RVP_AUTH_DEV_LOGIN": "1",
           "RVP_SESSION_SECRET": "smoke-test",
           "RVP_USERS_FILE": str(users),
           "RVP_MCP": "1"}
    fh = log.open("w")
    return subprocess.Popen([sys.executable, "-u", "backend/server.py"],
                            cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT)


def wait_ready(base: str, proc: subprocess.Popen, log: Path, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"  프로세스가 죽었다 (exit {proc.returncode}):\n"
                  f"{log.read_text(errors='replace')[-1500:]}")
            return False
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    print(f"  기동 시간 초과:\n{log.read_text(errors='replace')[-1500:]}")
    return False


MCP_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "smoke", "version": "0"}}}


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    port, log = free_port(), tmp / "server.log"
    base = f"http://127.0.0.1:{port}"
    proc = boot(port, log, tmp / "users.yaml")
    try:
        print(f"\n[기동] {base}")
        if not wait_ready(base, proc, log):
            check("서버 기동", False, "로그는 위 참조")
            return 1
        check("서버 기동", True)

        text = log.read_text(errors="replace")
        # 모듈이 두 번 적재되면 모듈 수준 부작용이 조용히 두 번 실행된다
        check("모듈은 한 번만 적재된다", text.count("마운트됨") == 1,
              f"'마운트됨' {text.count('마운트됨')}회")

        c = Client(base)
        print("\n[인증]")
        st, _, _ = c.req("/metrics")
        check("미인증은 401", st == 401, str(st))
        st, _, _ = c.req("/auth/dev-login", "POST", {"email": "admin"})
        check("개발 로그인 200", st == 200, str(st))
        st, body, _ = c.req("/metrics")
        check("로그인 후 200", st == 200, str(st))
        check("응답은 JSON", body.lstrip().startswith("{"), body[:60])

        print("\n[API 라우트가 SPA 에 먹히지 않는다]")
        for path in ("/health", "/knowledge/stats", "/improve/queue"):
            st, body, _ = c.req(path)
            ok = st == 200 and not body.lstrip().lower().startswith("<!doctype")
            check(f"{path} 는 API 응답", ok, f"{st} {body[:60]}")

        print("\n[MCP 마운트 — 네트워크 경로]")
        hdr = {"Accept": "application/json, text/event-stream"}
        st, _, h = c.req("/mcp", "POST", MCP_INIT, hdr)
        check("슬래시 없는 /mcp 는 307", st == 307, str(st))
        check("Location 은 /mcp/", str(h.get("location", "")).startswith("/mcp/"),
              str(h.get("location")))
        st, body, _ = c.req("/mcp/", "POST", MCP_INIT, hdr)
        check("/mcp/ 초기화 200", st == 200, f"{st} {body[:80]}")
        check("serverInfo 응답", "lsi-error-analysis" in body, body[:120])

        print("\n[알 수 없는 경로]")
        st, body, _ = c.req("/no-such-endpoint")
        check("SPA 폴백 또는 404", st in (200, 404), str(st))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    rc = main()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    if rc:
        raise SystemExit(rc)
    print("전부 통과")
