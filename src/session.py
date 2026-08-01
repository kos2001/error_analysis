"""서명 세션 쿠키 — 로그인 상태를 HttpOnly 쿠키 하나로 유지한다.

서버에 세션 저장소를 두지 않는다: 담을 것이 신원(이메일·역할·만료)뿐이고,
서명으로 위조를 막을 수 있다. 재기동해도 세션이 유지되려면 RVP_SESSION_SECRET 이
고정돼 있어야 한다 — 없으면 프로세스마다 새로 만들고 경고한다(재기동 시 재로그인).

**IdP 토큰은 쿠키에 담지 않는다.** id_token/access_token 을 브라우저로 내보내면
XSS 한 번에 IdP 자격증명까지 넘어간다. 백엔드가 검증하고 결과(식별자)만 남긴다.

담는 키는 `sub`(식별자)다. 예전에는 `email` 을 담았는데, 아이디 계정(`admin`)은
이메일이 없어 그 방식으로는 세션이 성립하지 않는다.

쿠키는 HttpOnly + SameSite=Lax. Lax 인 이유: IdP 콜백이 크로스사이트
리다이렉트(GET)로 돌아오므로 Strict 면 그 순간 쿠키가 실리지 않는다.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import secrets
import time
from hashlib import sha256

log = logging.getLogger("uvicorn.error")

COOKIE_NAME = "rvp_session"
DEFAULT_TTL_SEC = 12 * 3600

_FALLBACK_SECRET = ""


def _secret() -> str:
    global _FALLBACK_SECRET
    s = os.getenv("RVP_SESSION_SECRET", "").strip()
    if s:
        return s
    if not _FALLBACK_SECRET:
        _FALLBACK_SECRET = secrets.token_urlsafe(48)
        log.warning("RVP_SESSION_SECRET 미설정 — 프로세스 임시 키를 쓴다. "
                    "재기동하면 모든 세션이 만료된다(운영에서는 반드시 설정).")
    return _FALLBACK_SECRET


def ttl_sec() -> int:
    try:
        return max(60, int(os.getenv("RVP_SESSION_TTL_SEC", str(DEFAULT_TTL_SEC))))
    except ValueError:
        return DEFAULT_TTL_SEC


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(payload: dict, ttl: int | None = None) -> str:
    """payload + 만료를 담아 서명한 토큰 문자열."""
    body = {**payload, "exp": int(time.time()) + (ttl or ttl_sec())}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_secret().encode("utf-8"), raw, sha256).digest()
    return f"{_b64e(raw)}.{_b64e(sig)}"


def verify(token: str) -> dict | None:
    """서명·만료 검증. 실패하면 None (사유는 남기지 않는다 — 오라클이 된다)."""
    if not token or "." not in token:
        return None
    enc, sig = token.rsplit(".", 1)
    try:
        raw = _b64d(enc)
        want = hmac.new(_secret().encode("utf-8"), raw, sha256).digest()
        if not hmac.compare_digest(want, _b64d(sig)):   # 타이밍 비교
            return None
        body = json.loads(raw)
    except Exception:
        return None
    if not isinstance(body, dict) or int(body.get("exp", 0)) < time.time():
        return None
    return body


def cookie_kwargs() -> dict:
    """Set-Cookie 옵션. https 배포에서는 RVP_COOKIE_SECURE=1 로 Secure 를 켠다."""
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": os.getenv("RVP_COOKIE_SECURE", "0") == "1",
        "path": "/",
        "max_age": ttl_sec(),
    }
