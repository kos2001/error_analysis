"""사용자 관리(등록·역할변경·회수) + 잠금 방지 검증.

가장 중요한 것은 "관리자가 스스로 잠기지 않는가"다 — 마지막 관리자 회수/강등,
자기 자신 회수, 환경변수 관리자 편집을 전부 막아야 한다.

실행:
    .venv/bin/python tests/test_user_admin.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

USERS = ROOT / "tests" / "_users_admin_test.yaml"
os.environ.update({
    "RVP_JIRA_POLL_SEC": "0", "RVP_PREWARM": "0", "RVP_AUTH_DEV_LOGIN": "1",
    "RVP_SESSION_SECRET": "test-secret", "RVP_USERS_FILE": str(USERS),
    # MCP 미마운트 — 세션 매니저는 인스턴스당 1회만 run() 할 수 있는데 이 테스트는
    # TestClient 를 여러 번 만든다. MCP 자체는 tests/test_mcp_server.py 가 본다.
    "RVP_MCP": "0",
})
os.environ.pop("RVP_ADMIN_EMAILS", None)

import user_store  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def reset() -> None:
    USERS.write_text(
        "users:\n"
        "  - id: boss@example.com\n    name: 관리자\n    role: admin\n"
        "  - id: eng@example.com\n    name: 사용자\n    role: user\n",
        encoding="utf-8")


def test_store() -> None:
    print("\n[저장소 규칙]")
    reset()
    check("목록 읽기", {u["email"] for u in user_store.listing()["users"]}
          == {"boss@example.com", "eng@example.com"})

    user_store.upsert("New.Person@Example.com", "새 사람", "user", actor="t")
    rows = {u["email"]: u for u in user_store.listing()["users"]}
    check("등록 + 이메일 소문자 정규화", "new.person@example.com" in rows)
    check("등록된 역할", rows["new.person@example.com"]["role"] == "user")

    user_store.upsert("new.person@example.com", "새 사람", "admin", actor="t")
    check("역할 변경(user→admin)",
          user_store.listing()["users"] and
          {u["email"]: u["role"] for u in user_store.listing()["users"]}["new.person@example.com"] == "admin")

    user_store.revoke("new.person@example.com", True, actor="t")
    rows = {u["email"]: u for u in user_store.listing()["users"]}
    check("회수 — 항목은 남는다", rows["new.person@example.com"]["revoked"] is True)
    user_store.upsert("new.person@example.com", "새 사람", "user", actor="t")
    rows = {u["email"]: u for u in user_store.listing()["users"]}
    check("재등록 시 회수 해제", rows["new.person@example.com"]["revoked"] is False)
    user_store.revoke("new.person@example.com", False, actor="t")
    check("복구", not {u["email"]: u for u in user_store.listing()["users"]}["new.person@example.com"]["revoked"])

    # 아이디 계정도 등록된다
    user_store.upsert("admin", "관리자", "admin", actor="t")
    check("아이디(admin) 등록", any(u["email"] == "admin" and u["role"] == "admin"
                                  for u in user_store.listing()["users"]))
    for bad in ("a", "bad id", "@x.com", "", "UPPER CASE"):
        try:
            user_store.upsert(bad, "x", "user")
            check(f"잘못된 ID 거부 {bad!r}", False, "예외가 안 났다")
        except user_store.UserStoreError:
            check(f"잘못된 ID 거부 {bad!r}", True)
    try:
        user_store.upsert("ok@example.com", "x", "superuser")
        check("알 수 없는 역할 거부", False, "예외가 안 났다")
    except user_store.UserStoreError:
        check("알 수 없는 역할 거부", True)


def test_lockout_guard() -> None:
    print("\n[잠금 방지]")
    USERS.write_text("users:\n  - id: solo@example.com\n    name: 유일관리자\n    role: admin\n",
                     encoding="utf-8")
    for label, fn in [
        ("마지막 관리자 회수 거부", lambda: user_store.revoke("solo@example.com", True)),
        ("마지막 관리자 강등 거부", lambda: user_store.upsert("solo@example.com", "x", "user")),
    ]:
        try:
            fn()
            check(label, False, "예외가 안 났다")
        except user_store.UserStoreError as e:
            check(label, "활성 관리자가 0명" in str(e), str(e)[:60])

    # 다른 관리자를 먼저 등록하면 강등이 허용된다
    user_store.upsert("second@example.com", "둘째", "admin")
    try:
        user_store.upsert("solo@example.com", "x", "user")
        check("관리자 2명이면 강등 허용", True)
    except user_store.UserStoreError as e:
        check("관리자 2명이면 강등 허용", False, str(e)[:60])

    # 환경변수 관리자가 탈출구로 인정된다
    USERS.write_text("users:\n  - id: solo@example.com\n    name: 유일\n    role: admin\n",
                     encoding="utf-8")
    os.environ["RVP_ADMIN_EMAILS"] = "escape@example.com"
    try:
        user_store.revoke("solo@example.com", True)
        check("환경변수 관리자 있으면 마지막 파일관리자 회수 허용", True)
    except user_store.UserStoreError as e:
        check("환경변수 관리자 있으면 마지막 파일관리자 회수 허용", False, str(e)[:60])

    # 환경변수 관리자는 화면에서 못 고친다
    for label, fn in [
        ("환경변수 관리자 강등 거부", lambda: user_store.upsert("escape@example.com", "x", "user")),
        ("환경변수 관리자 회수 거부", lambda: user_store.revoke("escape@example.com", True)),
    ]:
        try:
            fn()
            check(label, False, "예외가 안 났다")
        except user_store.UserStoreError as e:
            check(label, "RVP_ADMIN_EMAILS" in str(e), str(e)[:60])
    check("환경변수 관리자는 locked 로 표시",
          any(u["locked"] for u in user_store.listing()["users"] if u["email"] == "escape@example.com"))
    os.environ.pop("RVP_ADMIN_EMAILS")


def test_api() -> None:
    print("\n[API 인가]")
    from fastapi.testclient import TestClient
    import server
    reset()
    server._reload_users()
    with TestClient(server.app) as c:
        check("미인증 GET /auth/users → 401", c.get("/auth/users").status_code == 401)

        c.post("/auth/dev-login", json={"email": "eng@example.com"})
        check("사용자 GET /auth/users → 403", c.get("/auth/users").status_code == 403)
        check("사용자 POST /auth/users → 403",
              c.post("/auth/users", json={"email": "x@example.com", "role": "user"}).status_code == 403)
        check("사용자 POST /auth/users/revoke → 403",
              c.post("/auth/users/revoke", json={"email": "eng@example.com"}).status_code == 403)

        c.post("/auth/dev-login", json={"email": "boss@example.com"})
        r = c.get("/auth/users")
        check("관리자 GET /auth/users → 200", r.status_code == 200)
        check("활성 관리자 수 보고", r.json()["active_admins"] == 1)

        r = c.post("/auth/users", json={"email": "fresh@example.com", "name": "신규", "role": "user"})
        check("관리자 등록 성공", r.status_code == 200 and
              any(u["email"] == "fresh@example.com" for u in r.json()["users"]))
        # 등록 직후 바로 로그인 가능해야 한다(재기동 없이 반영)
        check("등록 직후 로그인 가능",
              c.post("/auth/dev-login", json={"email": "fresh@example.com"}).status_code == 200)

        c.post("/auth/dev-login", json={"email": "boss@example.com"})
        r = c.post("/auth/users/revoke", json={"email": "boss@example.com", "revoked": True})
        check("자기 자신 회수 거부(400)", r.status_code == 400, f"실제 {r.status_code}")
        check("자기 회수 거부 사유 명시", "자기 자신" in r.json().get("detail", ""))

        r = c.post("/auth/users", json={"email": "bad id", "role": "user"})
        check("잘못된 ID → 400", r.status_code == 400)
        r = c.post("/auth/users/revoke", json={"email": "nobody@example.com"})
        check("목록 밖 회수 → 400", r.status_code == 400)

        # 승격 → 강등 왕복
        c.post("/auth/users", json={"email": "fresh@example.com", "name": "신규", "role": "admin"})
        check("승격 반영", c.get("/auth/users").json()["active_admins"] == 2)
        c.post("/auth/users", json={"email": "fresh@example.com", "name": "신규", "role": "user"})
        check("강등 반영", c.get("/auth/users").json()["active_admins"] == 1)

        # 회수된 사용자는 로그인 불가
        c.post("/auth/users/revoke", json={"email": "fresh@example.com", "revoked": True})
        c.post("/auth/logout")
        check("회수된 계정 로그인 거부",
              c.post("/auth/dev-login", json={"email": "fresh@example.com"}).status_code == 403)


def test_bootstrap() -> None:
    print("\n[파일 없는 상태에서 첫 등록 = 인증 켜기]")
    from fastapi.testclient import TestClient
    import server
    USERS.unlink(missing_ok=True)
    server._reload_users()
    with TestClient(server.app) as c:
        check("파일 없으면 인증 비활성", c.get("/auth/config").json()["enabled"] is False)
        check("비활성 상태에서도 관리 API 접근 가능(전체 권한)",
              c.get("/auth/users").status_code == 200)
        r = c.post("/auth/users", json={"email": "first@example.com", "name": "첫 관리자", "role": "admin"})
        check("첫 관리자 등록 성공", r.status_code == 200, f"실제 {r.status_code} {r.text[:80]}")
        check("등록 후 인증 활성화", c.get("/auth/config").json()["enabled"] is True)
        check("이후 미인증 접근은 401", c.get("/reco/stats").status_code == 401)


if __name__ == "__main__":
    try:
        test_store()
        test_lockout_guard()
        test_api()
        test_bootstrap()
    finally:
        USERS.unlink(missing_ok=True)
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
