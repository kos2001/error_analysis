"""브라우저 SSO — OIDC 인증 코드 플로우 + PKCE.

코드 교환을 **백엔드가** 한다. 프런트에서 교환하면 id_token/access_token 이 JS 가
읽을 수 있는 곳에 남아 XSS 한 번에 IdP 자격증명까지 넘어간다. 백엔드가 교환·검증하고
결과(이메일)만 서명 쿠키에 남긴다.

    브라우저 ──▶ /auth/login           state·code_verifier 를 서버에 보관
             ──▶ IdP 로그인 화면        code_challenge(S256) 전달
             ──▶ /auth/callback?code    state 소비(1회용) → 코드 교환
                                        id_token 서명·iss·aud·exp 검증 → 이메일
                                        → 인가 매핑 → Set-Cookie(HttpOnly)

신뢰의 근거는 **서명**이다. 네트워크 위치를 신뢰하는 프록시 헤더 방식과 달리,
백엔드에 직접 닿을 수 있어도 IdP 개인키 없이는 토큰을 만들 수 없다.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger("uvicorn.error")

# IdP 로그인 화면에 머무는 시간을 덮되, 길면 리플레이 창이 넓어진다.
STATE_TTL_SEC = 600
# 인증 전 엔드포인트(/auth/login)라 누구나 부를 수 있다 — 메모리 상한을 둔다.
MAX_PENDING = 256
# JWKS 캐시. IdP 가 키를 회전하므로 무한 캐시는 안 되고, 매 요청 조회는 IdP 를 때린다.
JWKS_TTL_SEC = 600


class SsoError(RuntimeError):
    """사용자에게 보여줄 수 있는 실패 사유."""


@dataclass(frozen=True)
class OidcSettings:
    discovery_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    email_claim: str = "email"
    audience: str = ""
    post_login_url: str = ""

    @property
    def enabled(self) -> bool:
        # 세 값이 다 있어야 로그인을 시작할 수 있다 — 하나라도 없으면 켜진 척하지 않는다.
        return bool(self.discovery_url and self.client_id and self.redirect_uri)


def settings_from_env() -> OidcSettings:
    def g(k: str, d: str = "") -> str:
        return os.getenv(k, d).strip()
    scopes = tuple(s for s in g("RVP_OIDC_SCOPES", "openid email profile").split() if s)
    return OidcSettings(
        discovery_url=g("RVP_OIDC_DISCOVERY_URL"),
        client_id=g("RVP_OIDC_CLIENT_ID"),
        client_secret=g("RVP_OIDC_CLIENT_SECRET"),
        redirect_uri=g("RVP_OIDC_REDIRECT_URI"),
        scopes=scopes or ("openid", "email", "profile"),
        email_claim=g("RVP_OIDC_EMAIL_CLAIM", "email") or "email",
        audience=g("RVP_OIDC_AUDIENCE") or g("RVP_OIDC_CLIENT_ID"),
        post_login_url=g("RVP_OIDC_POST_LOGIN_URL"),
    )


# ---------------------------------------------------------------------------
# discovery / JWKS 캐시
# ---------------------------------------------------------------------------
_META: dict[str, tuple[float, dict]] = {}
_JWKS: dict[str, tuple[float, Any]] = {}


def _http_get(url: str) -> dict:
    import httpx
    try:
        r = httpx.get(url, timeout=15.0, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise SsoError(f"IdP 문서를 가져오지 못했다({url}): {str(e)[:160]}") from e


def metadata(st: OidcSettings) -> dict:
    hit = _META.get(st.discovery_url)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    meta = _http_get(st.discovery_url)
    _META[st.discovery_url] = (time.time(), meta)
    return meta


def _jwks(uri: str, force: bool = False):
    from jwt import PyJWKSet
    hit = _JWKS.get(uri)
    if hit and not force and time.time() - hit[0] < JWKS_TTL_SEC:
        return hit[1]
    ks = PyJWKSet.from_dict(_http_get(uri))
    _JWKS[uri] = (time.time(), ks)
    return ks


def clear_caches() -> None:
    _META.clear()
    _JWKS.clear()
    _PENDING.clear()


# ---------------------------------------------------------------------------
# 인증 코드 플로우
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Pending:
    verifier: str
    next_url: str
    created: float


# state → 대기 중 로그인. 프로세스 메모리다 — 재기동하면 진행 중이던 로그인만
# 실패하고(다시 누르면 된다) 이미 발급된 쿠키는 영향받지 않는다.
_PENDING: dict[str, _Pending] = {}


def _prune(now: float) -> None:
    for s in [s for s, p in _PENDING.items() if now - p.created >= STATE_TTL_SEC]:
        _PENDING.pop(s, None)
    while len(_PENDING) >= MAX_PENDING:
        _PENDING.pop(next(iter(_PENDING)), None)   # dict 는 삽입 순서를 유지한다


def _challenge(verifier: str) -> str:
    d = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode("ascii")


def authorization_url(st: OidcSettings, next_url: str = "") -> str:
    meta = metadata(st)
    endpoint = meta.get("authorization_endpoint", "")
    if not endpoint:
        raise SsoError("IdP discovery 문서에 authorization_endpoint 가 없다")
    now = time.monotonic()
    _prune(now)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    _PENDING[state] = _Pending(verifier=verifier, next_url=next_url, created=now)
    q = urlencode({
        "response_type": "code", "client_id": st.client_id,
        "redirect_uri": st.redirect_uri, "scope": " ".join(st.scopes),
        "state": state,
        "code_challenge": _challenge(verifier), "code_challenge_method": "S256",
    })
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{q}"


def pop_pending(state: str) -> _Pending | None:
    p = _PENDING.pop(state, None)
    if p is None or time.monotonic() - p.created >= STATE_TTL_SEC:
        return None
    return p


def exchange_code(st: OidcSettings, code: str, verifier: str) -> dict:
    import httpx
    meta = metadata(st)
    endpoint = meta.get("token_endpoint", "")
    if not endpoint:
        raise SsoError("IdP discovery 문서에 token_endpoint 가 없다")
    form = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": st.redirect_uri, "client_id": st.client_id,
            "code_verifier": verifier}
    if st.client_secret:
        form["client_secret"] = st.client_secret
    try:
        r = httpx.post(endpoint, data=form, timeout=15.0,
                       headers={"Accept": "application/json",
                                "Content-Type": "application/x-www-form-urlencoded"})
    except Exception as e:
        raise SsoError(f"IdP 토큰 엔드포인트에 닿지 못했다: {str(e)[:160]}") from e
    if r.status_code != 200:
        # IdP 사유를 살려야 redirect_uri 불일치·secret 오류를 진단할 수 있다.
        # 이 응답에는 토큰이 담기지 않으므로 그대로 옮겨도 안전하다.
        try:
            d = r.json()
            detail = f"{d.get('error','')}: {d.get('error_description','')}".strip(": ")
        except Exception:
            detail = r.text[:200]
        raise SsoError(f"코드 교환 실패({r.status_code}) {detail}")
    tok = r.json()
    if not isinstance(tok, dict):
        raise SsoError("IdP 토큰 응답이 JSON 객체가 아니다")
    return tok


def verify_id_token(st: OidcSettings, id_token: str) -> dict:
    """id_token 서명·iss·aud·exp 검증 후 클레임 반환. 토큰 자체는 로그에 남기지 않는다."""
    import jwt
    meta = metadata(st)
    jwks_uri = meta.get("jwks_uri", "")
    issuer = meta.get("issuer", "")
    if not jwks_uri or not issuer:
        raise SsoError("IdP discovery 문서에 jwks_uri/issuer 가 없다")
    try:
        kid = jwt.get_unverified_header(id_token).get("kid", "")
    except Exception as e:
        raise SsoError(f"id_token 헤더를 읽지 못했다: {str(e)[:120]}") from e

    def _key(force: bool):
        ks = _jwks(jwks_uri, force=force)
        for k in ks.keys:
            if not kid or k.key_id == kid:
                return k
        return None

    key = _key(False) or _key(True)     # kid 가 없으면 키 회전으로 보고 한 번 다시 받는다
    if key is None:
        raise SsoError("id_token 의 서명 키를 JWKS 에서 찾지 못했다(kid 불일치)")
    try:
        claims = jwt.decode(
            id_token, key.key,
            algorithms=["RS256", "RS512", "ES256", "PS256"],
            audience=st.audience or st.client_id, issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except Exception as e:
        raise SsoError(f"id_token 검증 실패: {type(e).__name__}: {str(e)[:140]}") from e
    return claims


def email_from_claims(st: OidcSettings, claims: dict) -> str:
    """이메일 클레임 추출. IdP 마다 이름이 달라 대체 경로를 둔다."""
    for k in (st.email_claim, "email", "preferred_username", "upn", "sub"):
        v = str(claims.get(k) or "").strip()
        if "@" in v:
            return v.lower()
    return ""
