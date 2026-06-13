"""앱 설정 저장소 — 온보딩에서 입력한 Hermes Gateway / Jira 설정을 JSON에 영속화하고
os.environ 에 주입한다 (단일 책임).

정책: 온보딩 저장값이 소스 오브 트루스. 서버 기동 시 load_into_env()로 적용하므로
.env 보다 저장값이 우선한다. 미설정이면 status()['ready']=False → 프론트가 온보딩 강제.

비밀(토큰/키)은 status()로 절대 반환하지 않고 존재 여부(has_*)만 노출한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "tmp_db" / "app_config.json"

# UI 필드 → 환경변수 매핑
JIRA_MAP = {
    "base_url": "JIRA_BASE_URL", "project_key": "JIRA_PROJECT_KEY",
    "email": "JIRA_EMAIL", "api_token": "JIRA_API_TOKEN", "pat": "JIRA_PAT",
}
HERMES_MAP = {
    "gateway_url": "OPENROUTER_BASE_URL", "api_key": "OPENROUTER_API_KEY",
    "model": "OPENROUTER_MODEL",
}


def _restrict_perms() -> None:
    """서비스 계정 비밀(Jira 토큰/PAT, OpenRouter 키)이 담길 수 있으므로 0600으로 제한."""
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def _load_json() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def load_into_env() -> None:
    """저장된 설정을 os.environ 에 주입(저장값 우선)."""
    for k, v in _load_json().items():
        if v:
            os.environ[k] = str(v)


def save(jira: dict | None, hermes: dict | None) -> dict:
    """온보딩 입력 저장 — 빈 문자열 필드는 기존값 유지(비밀 재입력 강제 방지)."""
    data = _load_json()
    for section, mapping in ((jira or {}, JIRA_MAP), (hermes or {}, HERMES_MAP)):
        for field, env_key in mapping.items():
            v = section.get(field)
            if v is None:
                continue
            v = str(v).strip()
            if v == "":
                continue
            data[env_key] = v
            os.environ[env_key] = v
    CONFIG_FILE.parent.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _restrict_perms()
    return status()


def set_env(key: str, value: str) -> None:
    """단일 환경변수를 영속화 + 즉시 주입 (예: HERMES_BIN)."""
    data = _load_json()
    data[key] = value
    os.environ[key] = value
    CONFIG_FILE.parent.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _restrict_perms()


def status() -> dict:
    g = os.environ
    jira_ok = bool(
        g.get("JIRA_BASE_URL") and g.get("JIRA_PROJECT_KEY")
        and (g.get("JIRA_PAT") or (g.get("JIRA_EMAIL") and g.get("JIRA_API_TOKEN"))))
    hermes_ok = bool(
        g.get("OPENROUTER_BASE_URL") and g.get("OPENROUTER_API_KEY") and g.get("OPENROUTER_MODEL"))
    return {
        "jira": {
            "configured": jira_ok,
            "base_url": g.get("JIRA_BASE_URL", ""),
            "project_key": g.get("JIRA_PROJECT_KEY", ""),
            "email": g.get("JIRA_EMAIL", ""),
            "auth_mode": "pat" if g.get("JIRA_PAT") else ("basic" if g.get("JIRA_API_TOKEN") else ""),
            "has_secret": bool(g.get("JIRA_PAT") or g.get("JIRA_API_TOKEN")),
        },
        "hermes": {
            "configured": hermes_ok,
            "gateway_url": g.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "model": g.get("OPENROUTER_MODEL", ""),
            "has_key": bool(g.get("OPENROUTER_API_KEY")),
        },
        "ready": jira_ok and hermes_ok,
    }
