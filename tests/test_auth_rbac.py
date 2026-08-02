"""인증(SSO 세션)·인가(RBAC) 검증.

외부 호출 없이 도는 것만 담는다 — IdP 왕복은 여기서 못 하므로, 검증 가능한
경계(세션 위조/만료, 역할별 허용·차단, 미인증 401 vs 권한부족 403, 프록시 헤더
신뢰 조건)를 전부 확인한다.

실행:
    .venv/bin/python tests/test_auth_rbac.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

# 서버 임포트 전에 환경을 고정한다 — 폴러·예열이 테스트 중 API를 때리지 않게.
os.environ.update({
    "RVP_JIRA_POLL_SEC": "0",
    "RVP_PREWARM": "0",
    "RVP_AUTH_DEV_LOGIN": "1",
    "RVP_SESSION_SECRET": "test-secret-fixed",
    "RVP_USERS_FILE": str(ROOT / "tests" / "_users_test.yaml"),
    # MCP 미마운트 — 세션 매니저는 인스턴스당 1회만 run() 할 수 있는데 이 테스트는
    # TestClient 를 여러 번 만든다. MCP 자체는 tests/test_mcp_server.py 가 본다.
    "RVP_MCP": "0",
})
(ROOT / "tests" / "_users_test.yaml").write_text(
    "users:\n"
    "  - id: admin\n    name: 관리자\n    role: admin\n"
    "  - email: boss@example.com\n    name: 관리자2\n    role: admin\n"   # 이전 email 키 하위호환
    "  - id: eng@example.com\n    name: 사용자\n    role: user\n"
    "  - id: gone@example.com\n    name: 폐기\n    role: admin\n    revoked: true\n"
    "  - id: typo@example.com\n    name: 오타역할\n    role: manger\n",
    encoding="utf-8")

import auth  # noqa: E402
import session  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------
def test_users_file() -> None:
    print("\n[인가 목록]")
    users = auth.load_users()
    check("아이디(admin) 계정 로드", users["admin"].role == "admin")
    check("아이디 계정은 email 이 비어 있다", users["admin"].email == "")
    check("이전 email 키 하위호환", users["boss@example.com"].role == "admin")
    check("사용자 로드", users["eng@example.com"].role == "user")
    check("revoked 항목 제외", "gone@example.com" not in users)
    check("알 수 없는 역할 항목 제외", "typo@example.com" not in users)
    check("식별자 대소문자·공백 정규화", auth.normalize_id(" Admin ") == "admin")


def test_capabilities() -> None:
    print("\n[역할별 기능]")
    admin = auth.User("a", "a", "admin")
    user = auth.User("u", "u", "user")
    check("관리자는 모든 기능", all(admin.can(c) for c in auth.ALL_CAPABILITIES))
    for cap in ("rca.approve", "knowledge.write", "config.write", "ops.sync",
                "ops.cache", "ops.eval", "voc.manage", "improve.manage"):
        check(f"사용자에게 {cap} 없음", not user.can(cap))
    for cap in ("issue.read", "reco.read", "knowledge.read", "rca.draft",
                "rca.read", "feedback.write"):
        check(f"사용자에게 {cap} 있음", user.can(cap))
    check("알 수 없는 기능은 거부", not admin.can("nonexistent.cap"))


def test_session() -> None:
    print("\n[세션 쿠키]")
    tok = session.issue({"email": "boss@example.com", "via": "oidc"})
    check("정상 토큰 검증", (session.verify(tok) or {}).get("email") == "boss@example.com")
    check("본문 위조 거부", session.verify("x" + tok) is None)
    head, sig = tok.rsplit(".", 1)
    check("서명 교체 거부", session.verify(f"{head}.{sig[:-4]}AAAA") is None)
    check("서명 없는 토큰 거부", session.verify(head) is None)
    expired = session.issue({"email": "boss@example.com"}, ttl=-1)
    check("만료 토큰 거부", session.verify(expired) is None)
    # 다른 비밀키로 서명된 토큰은 통과하지 못한다
    os.environ["RVP_SESSION_SECRET"] = "other-secret"
    check("다른 키 서명 거부", session.verify(tok) is None)
    os.environ["RVP_SESSION_SECRET"] = "test-secret-fixed"
    check("키 복구 후 재검증", session.verify(tok) is not None)


def test_domains() -> None:
    print("\n[자동 등록 도메인 제한]")
    os.environ["RVP_ALLOWED_EMAIL_DOMAINS"] = "samsung.com"
    check("사내 도메인 허용", auth.domain_allowed("hong@samsung.com"))
    check("사내 서브도메인 허용", auth.domain_allowed("a@sec.samsung.com"))
    check("유사 도메인 차단", not auth.domain_allowed("x@evil-samsung.com"))
    check("외부 도메인 차단", not auth.domain_allowed("x@gmail.com"))
    check("도메인 제한 시 아이디 자동등록 차단", not auth.domain_allowed("admin"))
    users = auth.load_users()
    check("목록에 있으면 도메인 제한과 무관",
          auth.resolve_email(users, "admin", "dev") is not None)
    check("목록 밖 외부 도메인은 자동 등록 거부",
          auth.resolve_email(users, "outsider@gmail.com", "oidc") is None)
    check("목록 밖 사내 도메인은 기본역할로 허용",
          (auth.resolve_email(users, "newbie@samsung.com", "oidc") or auth.User("", "", "x")).role == "user")
    os.environ.pop("RVP_ALLOWED_EMAIL_DOMAINS")
    check("제한 없으면 외부도 기본역할",
          auth.resolve_email(users, "outsider@gmail.com", "oidc") is not None)


def test_resolve() -> None:
    print("\n[식별자 → 신원]")
    users = auth.load_users()
    check("관리자 매핑", auth.resolve_email(users, "BOSS@example.com", "oidc").role == "admin")
    check("목록 밖은 기본역할(user)", auth.resolve_email(users, "new@example.com", "oidc").role == "user")
    os.environ["RVP_SSO_DEFAULT_ROLE"] = ""
    check("기본역할 없으면 목록 밖 거부", auth.resolve_email(users, "new@example.com", "oidc") is None)
    os.environ.pop("RVP_SSO_DEFAULT_ROLE")
    check("빈 이메일 거부", auth.resolve_email(users, "", "oidc") is None)
    check("목록이 None이면 전체 권한", auth.resolve_email(None, "", "x") is auth.ALL_ACCESS)


# ---------------------------------------------------------------------------
def test_endpoints() -> None:
    print("\n[엔드포인트 인가 — TestClient]")
    from fastapi.testclient import TestClient
    import server

    server._reload_users()
    with TestClient(server.app) as c:
        # 공개 엔드포인트
        check("GET /health 는 미인증 허용", c.get("/health").status_code == 200)
        check("GET /auth/config 는 미인증 허용", c.get("/auth/config").status_code == 200)
        check("GET /auth/me 는 미인증 401", c.get("/auth/me").status_code == 401)

        # 미인증 접근
        check("미인증 GET /issues/unresolved → 401",
              c.get("/issues/unresolved").status_code == 401)
        check("미인증 POST /rca/approve → 401",
              c.post("/rca/approve", json={"key": "LSI-1"}).status_code == 401)

        # 사용자 로그인
        r = c.post("/auth/dev-login", json={"email": "eng@example.com"})
        check("개발 로그인(사용자) 성공", r.status_code == 200 and r.json()["role"] == "user")
        check("사용자 GET /auth/me", c.get("/auth/me").json()["role"] == "user")
        check("사용자 GET /reco/stats 허용", c.get("/reco/stats").status_code == 200)
        check("사용자 GET /knowledge/quality 허용", c.get("/knowledge/quality").status_code == 200)
        check("사용자 GET /improve/queue 허용", c.get("/improve/queue").status_code == 200)
        # 관리자 전용은 403 (401 이 아니라 — 로그인은 되어 있다)
        for method, path, body in [
            ("post", "/rca/approve", {"key": "LSI-1"}),
            ("post", "/config", {}),
            ("post", "/jira/sync", None),
            ("post", "/explain/prewarm", None),
            ("delete", "/explain/cache", None),
            ("post", "/knowledge/lifecycle", {"key": "LSI-1", "state": "active"}),
            ("get", "/voc", None),
            ("post", "/improve/queue/state", {"id": "S-1", "state": "done"}),
            ("post", "/eval/build", None),
            ("get", "/selfcheck", None),
        ]:
            fn = getattr(c, method)
            r = fn(path, json=body) if body is not None else fn(path)
            check(f"사용자 {method.upper()} {path} → 403", r.status_code == 403,
                  f"실제 {r.status_code}")

        # 아이디 계정(admin) — 이메일이 없는 신원도 세션이 유지돼야 한다.
        # 예전에는 세션에 email 을 담아서, 이메일 없는 계정은 로그인 직후 401 이 났다.
        r = c.post("/auth/dev-login", json={"email": "admin"})
        check("아이디(admin) 로그인 성공", r.status_code == 200 and r.json()["role"] == "admin")
        me = c.get("/auth/me")
        check("아이디 계정 세션 유지", me.status_code == 200 and me.json()["subject"] == "admin",
              f"실제 {me.status_code}")
        check("아이디 계정으로 관리자 기능 접근", c.get("/voc").status_code == 200)
        check("아이디 계정 대문자 입력도 동일 계정",
              c.post("/auth/dev-login", json={"email": " ADMIN "}).status_code == 200)

        # 관리자 로그인
        r = c.post("/auth/dev-login", json={"email": "boss@example.com"})
        check("개발 로그인(관리자) 성공", r.status_code == 200 and r.json()["role"] == "admin")
        check("관리자 GET /voc 허용", c.get("/voc").status_code == 200)
        check("관리자 GET /explain/cache 허용", c.get("/explain/cache").status_code == 200)
        r = c.post("/improve/queue/state", json={"id": "__nope__", "state": "done"})
        check("관리자 POST /improve/queue/state 통과(권한)", r.status_code == 200,
              f"실제 {r.status_code}")

        # 인가 목록 밖 이메일로는 개발 로그인 불가
        check("목록 밖 개발 로그인 거부",
              c.post("/auth/dev-login", json={"email": "nobody@example.com"}).status_code == 403)

        # 로그아웃 → 다시 401
        c.post("/auth/logout")
        check("로그아웃 후 401", c.get("/auth/me").status_code == 401)

        # 위조 쿠키
        c.cookies.set(session.COOKIE_NAME, "forged.token")
        check("위조 쿠키 → 401", c.get("/auth/me").status_code == 401)
        c.cookies.clear()

        # 프록시 헤더는 헤더 이름이 설정돼야만 신뢰
        os.environ.pop("RVP_SSO_EMAIL_HEADER", None)
        check("헤더 미설정 시 프록시 헤더 무시",
              c.get("/auth/me", headers={"X-Forwarded-Email": "boss@example.com"}).status_code == 401)
        os.environ["RVP_SSO_EMAIL_HEADER"] = "X-Forwarded-Email"
        r = c.get("/auth/me", headers={"X-Forwarded-Email": "boss@example.com"})
        check("헤더 설정 시 프록시 신원 인정",
              r.status_code == 200 and r.json()["role"] == "admin", f"실제 {r.status_code}")
        os.environ.pop("RVP_SSO_EMAIL_HEADER", None)

        # 개발 로그인 비활성 시 404
        os.environ["RVP_AUTH_DEV_LOGIN"] = "0"
        check("개발 로그인 비활성 → 404",
              c.post("/auth/dev-login", json={"email": "boss@example.com"}).status_code == 404)
        os.environ["RVP_AUTH_DEV_LOGIN"] = "1"


def test_auth_disabled() -> None:
    print("\n[인가 목록 없음 → 인증 비활성]")
    from fastapi.testclient import TestClient
    import server
    os.environ["RVP_USERS_FILE"] = str(ROOT / "tests" / "_missing.yaml")
    server._reload_users()
    with TestClient(server.app) as c:
        check("비활성 시 관리자 엔드포인트도 통과", c.get("/voc").status_code == 200)
        check("비활성 상태가 /auth/config 에 드러남",
              c.get("/auth/config").json()["enabled"] is False)
        check("비활성 시 /auth/me 는 '인증 비활성' 신원",
              c.get("/auth/me").json()["via"] == "disabled")
    os.environ["RVP_USERS_FILE"] = str(ROOT / "tests" / "_users_test.yaml")
    server._reload_users()


if __name__ == "__main__":
    t0 = time.time()
    test_users_file()
    test_capabilities()
    test_session()
    test_resolve()
    test_domains()
    test_endpoints()
    test_auth_disabled()
    print(f"\n{'=' * 56}")
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print(f"전부 통과 ({time.time() - t0:.1f}s)")
