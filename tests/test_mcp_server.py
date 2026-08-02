"""MCP 서버 검증 — 도구 노출·인가·안전장치.

백엔드를 인프로세스(ASGITransport)로 띄워 실제 인증 경로를 그대로 탄다.
네트워크·LLM 호출이 필요한 도구는 검증에서 제외하고, 여기서는 다음을 본다:

  · 도구 목록이 의도한 집합인가(관리자·게시 도구가 새어 나가지 않았나)
  · 토큰 없음/위조/사용자/관리자별로 인가가 갈리는가
  · 심층 분석 도구가 **생성하지 않고** 캐시만 돌려주는가

실행:
    .venv/bin/python tests/test_mcp_server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

USERS = ROOT / "tests" / "_users_mcp_test.yaml"
os.environ.update({
    "RVP_JIRA_POLL_SEC": "0", "RVP_PREWARM": "0", "RVP_AUTH_DEV_LOGIN": "1",
    "RVP_SESSION_SECRET": "mcp-test-secret", "RVP_USERS_FILE": str(USERS),
    "RVP_MCP": "1",
})
USERS.write_text(
    "users:\n"
    "  - id: admin\n    name: 관리자\n    role: admin\n"
    "  - id: eng@example.com\n    name: 사용자\n    role: user\n",
    encoding="utf-8")

import mcp_server  # noqa: E402
import session  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# 노출하기로 한 도구 — 여기 없는 이름이 생기면 의도치 않은 확장이다.
EXPECTED_TOOLS = {
    "find_similar", "analyze_issue", "list_unresolved", "get_cached_analysis",
    "knowledge_overview", "find_duplicate_clusters", "find_contradictions",
    "draft_rca", "whoami",
}
# 절대 노출하면 안 되는 것 — Jira 게시·설정·사용자관리·운영
FORBIDDEN_HINTS = ("approve", "reject", "config", "users", "sync", "prewarm",
                   "clear", "selfcheck", "reload", "delete")


async def run() -> None:
    import server
    from mcp.server.mcpserver import MCPServer  # noqa: F401

    server._reload_users()
    # 도구의 백엔드 호출을 인프로세스로 — 실제 인증 의존성을 그대로 통과한다
    mcp_server.bind_asgi(server.app)
    srv = mcp_server.new_server()

    print("\n[도구 목록]")
    tools = await srv.list_tools()
    names = {t.name for t in tools}
    check("의도한 도구 집합과 일치", names == EXPECTED_TOOLS,
          f"차이: {names ^ EXPECTED_TOOLS}")
    leaked = [n for n in names if any(h in n.lower() for h in FORBIDDEN_HINTS)]
    check("게시·설정·운영 도구 미노출", not leaked, f"새어나감: {leaked}")
    for t in tools:
        check(f"{t.name} 설명 있음", bool((t.description or "").strip()))

    async def call(name: str, args: dict, token: str = "") -> dict:
        reset = mcp_server._ctx_token.set(token)
        try:
            res = await srv.call_tool(name, args)
        finally:
            mcp_server._ctx_token.reset(reset)
        # MCP 2.0 은 CallToolResult 를 준다(.content = 블록 리스트).
        # 1.x 의 (content, structured) 튜플 / 리스트 형태도 함께 받아 둔다.
        content = getattr(res, "content", None)
        if content is None:
            content = res[0] if isinstance(res, tuple) else res
        text = getattr(content[0], "text", None) if content else None
        try:
            return json.loads(text)
        except Exception:
            return {"_raw": text}

    admin_tok = session.issue({"sub": "admin", "via": "token"})
    user_tok = session.issue({"sub": "eng@example.com", "via": "token"})

    print("\n[인가]")
    r = await call("whoami", {}, "")
    check("토큰 없음 → 구조화된 401 안내", r.get("error", "").startswith("인증 실패"),
          str(r)[:80])
    check("401 안내에 발급 방법 포함", "auth/token" in str(r.get("hint", "")))

    r = await call("whoami", {}, "forged.token")
    check("위조 토큰 → 401", r.get("error", "").startswith("인증 실패"), str(r)[:80])

    r = await call("whoami", {}, admin_tok)
    check("관리자 토큰 → admin", r.get("role") == "admin", str(r)[:80])
    r = await call("whoami", {}, user_tok)
    check("사용자 토큰 → user", r.get("role") == "user", str(r)[:80])

    print("\n[도구 동작]")
    r = await call("list_unresolved", {"limit": 3}, user_tok)
    check("사용자도 미해결 목록 조회 가능", r.get("returned", 0) > 0, str(r)[:100])
    check("목록 필드 축약됨",
          all(set(i) <= {"key", "summary", "status", "chip", "category", "symptom"}
              for i in r.get("issues", [])))

    r = await call("list_unresolved", {"query": "UFS", "limit": 5}, user_tok)
    check("검색어 필터 동작", all("ufs" in json.dumps(i, ensure_ascii=False).lower()
                              for i in r.get("issues", [])), str(r)[:100])

    r = await call("knowledge_overview", {}, user_tok)
    check("지식 현황 — KB 수치 포함", isinstance((r.get("kb") or {}).get("resolved"), int),
          str(r)[:100])

    r = await call("find_duplicate_clusters", {"min_size": 2}, user_tok)
    check("중복 클러스터 조회", isinstance(r.get("count"), int), str(r)[:100])
    check("클러스터는 상위 20개로 제한", (r.get("shown") or 0) <= 20)

    print("\n[안전장치]")
    r = await call("get_cached_analysis", {"key": "LSI-99999"}, user_tok)
    check("없는 이슈 → 오류를 구조화해 전달", "error" in r, str(r)[:100])

    # 심층 분석은 생성하지 않는다 — 캐시가 없으면 없다고 답해야 한다
    r = await call("get_cached_analysis", {"key": "LSI-7"}, user_tok)
    ok = ("cached" in r) or ("error" in r)
    check("캐시 조회는 cached 플래그로 답한다", ok, str(r)[:120])
    if r.get("cached") is False:
        check("미캐시 시 대안 안내", "reason" in r, str(r)[:120])

    print("\n[전송 계층]")
    mounted = mcp_server.build_http_app()
    check("streamable-HTTP 앱 생성", mounted.app is not None)
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer abc123")]}
    check("Authorization 헤더에서 토큰 추출",
          mcp_server._token_from_scope(scope) == "abc123")
    scope2 = {"type": "http", "headers": [(b"x-rvp-token", b"xyz")]}
    check("X-RVP-Token 헤더에서 토큰 추출",
          mcp_server._token_from_scope(scope2) == "xyz")
    check("헤더 없으면 빈 토큰",
          mcp_server._token_from_scope({"type": "http", "headers": []}) == "")
    os.environ["LSI_MCP_ALLOWED_HOSTS"] = "lsi.example.com,lsi.example.com:443"
    check("허용 Host 를 환경변수로 지정 가능",
          mcp_server._allowed_hosts() == ["lsi.example.com", "lsi.example.com:443"])
    os.environ.pop("LSI_MCP_ALLOWED_HOSTS")
    check("기본 허용 Host 는 로컬만", all("localhost" in h or "127.0.0.1" in h
                                     for h in mcp_server._allowed_hosts()))

    mcp_server.unbind_asgi()


def test_mount_path() -> None:
    """슬래시 없는 /mcp 도 받는가 — 문서가 안내하는 URL 이 실제로 붙어야 한다.

    Mount 는 POST 를 자동 리다이렉트하지 않는다. 클라이언트가 "https://<서버>/mcp" 로
    설정하면 405 만 보고 이유를 알 수 없었다. 307 이어야 메서드와 본문이 보존된다.
    """
    print("\n[마운트 경로]")
    import server                                    # noqa: E402
    from fastapi.testclient import TestClient        # noqa: E402
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "probe", "version": "0"}}}
    hdr = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
    # base_url 을 localhost 로 — MCP 의 Host 검증(DNS rebinding 방어)이 TestClient 기본
    # 호스트 "testserver" 를 421 로 막는다. 그 방어는 의도된 것이라 우회가 아니라 준수한다.
    with TestClient(server.app, base_url="http://localhost") as c:
        r = c.post("/mcp", json=body, headers=hdr, follow_redirects=False)
        check("슬래시 없는 /mcp 는 307", r.status_code == 307, str(r.status_code))
        check("메서드 보존용 307 (301/302 아님)", r.status_code not in (301, 302),
              str(r.status_code))
        check("/mcp/ 로 보낸다", r.headers.get("location", "").startswith("/mcp/"),
              r.headers.get("location", ""))
        for path in ("/mcp", "/mcp/"):
            rr = c.post(path, json=body, headers=hdr, follow_redirects=True)
            check(f"POST {path} → 200", rr.status_code == 200, str(rr.status_code))
            check(f"POST {path} 에 serverInfo", "lsi-error-analysis" in rr.text,
                  rr.text[:100])


if __name__ == "__main__":
    try:
        asyncio.run(run())
        test_mount_path()
    finally:
        USERS.unlink(missing_ok=True)
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
