"""인가 목록(users.yaml) 쓰기 — 관리자가 화면에서 사용자를 등록·변경·회수한다.

읽기는 auth.load_users() 가 담당하고, 여기서는 파일을 고치는 일만 한다.
분리한 이유: 인증 판정 경로(auth)는 파일을 절대 쓰지 않아야 하고, 쓰기에는
잠금 방지·원자적 저장 같은 별개의 규칙이 붙는다.

지켜야 하는 것:

1. **활성 관리자를 0명으로 만들 수 없다.** 마지막 관리자를 회수하거나 사용자로
   내리면 아무도 설정을 못 고치는 상태가 된다 — 파일을 직접 편집하는 수밖에 없다.
   RVP_ADMIN_EMAILS 로 지정된 환경변수 관리자가 있으면 그것을 탈출구로 인정한다.
2. **회수는 삭제가 아니다.** revoked: true 로 남긴다 — 누가 있었는지가 보여야 한다.
3. **원자적 저장.** 임시 파일에 쓰고 교체한다. 반쯤 쓰인 목록을 읽으면 인증이
   무너진다.
4. 파일이 없으면 만든다 — 이 경로가 "인증 켜기"의 정상 수단이다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

VALID_ROLES = ("admin", "user")
# 식별자는 이메일 또는 아이디다. 이메일은 아주 느슨하게만 본다(사내 주소 형태를
# 우리가 단정할 수 없다), 아이디는 URL·YAML 에서 다루기 안전한 문자로 제한한다.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def valid_identifier(value: str) -> bool:
    v = normalize_email(value)
    return bool(_EMAIL_RE.match(v) or _NAME_RE.match(v))

HEADER = """# 인가 목록 — 화면(설정 → 사용자 관리)에서 관리한다.
# 손으로 고쳐도 되지만, 서버가 다시 쓰면 이 주석 아래 형식으로 정규화된다.
#
# id: 로그인 식별자. 이메일(SSO 계정) 또는 아이디(admin 같은 로컬 계정).
#     아이디 계정은 IdP 가 그 값을 주지 않으므로 OIDC 로는 들어오지 못한다 —
#     개발용 로그인·프록시 헤더 경로에서 쓰는 로컬 운영 계정이다.
# role: admin(운영·설정·Jira 게시) | user(조회·분석·초안 제출)
# revoked: true 는 권한 회수 — 이력을 남기려고 지우지 않고 표시만 한다.
"""


class UserStoreError(RuntimeError):
    """호출자에게 그대로 보여줄 수 있는 실패 사유."""


def path() -> Path:
    override = os.getenv("RVP_USERS_FILE", "").strip()
    return Path(override) if override else ROOT / "data" / "users.yaml"


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def env_admins() -> set[str]:
    raw = os.getenv("RVP_ADMIN_EMAILS", "")
    return {normalize_email(e) for e in raw.replace(";", ",").split(",") if e.strip()}


def read_raw() -> list[dict]:
    """파일의 users 배열을 그대로 읽는다(정규화·필터 없음)."""
    p = path()
    if not p.is_file():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise UserStoreError(f"users.yaml 을 읽지 못했습니다: {str(e)[:160]}") from e
    items = data.get("users")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _write_raw(items: list[dict]) -> None:
    p = path()
    body = yaml.safe_dump({"users": items}, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(HEADER + body, encoding="utf-8")
        tmp.replace(p)              # 원자적 교체 — 반쯤 쓰인 목록을 읽지 않게
    except OSError as e:
        raise UserStoreError(f"users.yaml 을 저장하지 못했습니다: {str(e)[:160]}") from e


def ident_of(item: dict) -> str:
    """항목의 식별자. id 가 정식 키이고 email 은 이전 형식이다."""
    return normalize_email(str(item.get("id") or item.get("email") or ""))


def _active_admins(items: list[dict]) -> set[str]:
    return {ident_of(i) for i in items
            if not i.get("revoked") and str(i.get("role") or "").lower() == "admin"
            and ident_of(i)}


def _guard_last_admin(items: list[dict]) -> None:
    if _active_admins(items) or env_admins():
        return
    raise UserStoreError(
        "활성 관리자가 0명이 됩니다 — 마지막 관리자는 회수하거나 사용자로 바꿀 수 없습니다. "
        "다른 관리자를 먼저 등록하세요.")


def listing() -> dict:
    """화면용 목록 + 환경변수 관리자(파일에서 수정 불가) 표시."""
    items = read_raw()
    envs = env_admins()
    out = []
    for i in items:
        email = ident_of(i)
        if not email:
            continue
        out.append({
            "email": email,          # 키 이름은 유지 — 화면에서 "ID" 로 표기한다
            "name": str(i.get("name") or email),
            "role": str(i.get("role") or "").lower(),
            "revoked": bool(i.get("revoked")),
            # 환경변수 관리자는 파일 값보다 우선하므로 화면에서 편집을 막는다.
            "locked": email in envs,
        })
    for email in sorted(envs - {u["email"] for u in out}):
        out.append({"email": email, "name": email, "role": "admin",
                    "revoked": False, "locked": True})
    out.sort(key=lambda u: (u["revoked"], u["role"] != "admin", u["email"]))
    return {
        "users": out,
        "file": str(path()),
        "file_present": path().is_file(),
        "env_admins": sorted(envs),
        "active_admins": len(_active_admins(items) | envs),
        "roles": list(VALID_ROLES),
    }


def upsert(email: str, name: str, role: str, actor: str = "") -> dict:
    """등록 또는 수정. 이미 있으면 이름·역할을 바꾸고 회수 상태를 해제한다."""
    e = normalize_email(email)
    if not valid_identifier(e):
        raise UserStoreError(
            f"ID 형식이 아닙니다: {email!r} — 이메일(user@company.com) 또는 "
            "아이디(영문 소문자·숫자·. _ - 2~64자)를 쓰세요.")
    r = (role or "").strip().lower()
    if r not in VALID_ROLES:
        raise UserStoreError(f"역할은 {' 또는 '.join(VALID_ROLES)} 여야 합니다: {role!r}")
    if e in env_admins() and r != "admin":
        raise UserStoreError(
            f"{e} 는 RVP_ADMIN_EMAILS 환경변수로 관리자로 지정돼 있어 화면에서 역할을 바꿀 수 없습니다.")

    items = read_raw()
    found = False
    for i in items:
        if ident_of(i) == e:
            i.pop("email", None)             # 이전 키는 정리하고 id 로 통일
            i["id"], i["name"], i["role"] = e, (name or "").strip() or e, r
            i.pop("revoked", None)
            found = True
            break
    if not found:
        items.append({"id": e, "name": (name or "").strip() or e, "role": r})
    _guard_last_admin(items)
    _write_raw(items)
    print(f"[users] {'수정' if found else '등록'} {e} → {r} (by {actor or '?'})")
    return listing()


def revoke(email: str, revoked: bool = True, actor: str = "") -> dict:
    """권한 회수 / 복구. 항목은 지우지 않는다."""
    e = normalize_email(email)
    if e in env_admins():
        raise UserStoreError(
            f"{e} 는 RVP_ADMIN_EMAILS 환경변수로 지정돼 있어 화면에서 회수할 수 없습니다 "
            "— 환경변수에서 제거하세요.")
    items = read_raw()
    hit = next((i for i in items if ident_of(i) == e), None)
    if hit is None:
        raise UserStoreError(f"목록에 없는 ID 입니다: {e}")
    if revoked:
        hit["revoked"] = True
    else:
        hit.pop("revoked", None)
    _guard_last_admin(items)
    _write_raw(items)
    print(f"[users] {'회수' if revoked else '복구'} {e} (by {actor or '?'})")
    return listing()
