"""사내 LLM 게이트웨이 식별 헤더 — OpenRouter 호환 호출에 매번 첨부.

mi-report와 동일 컨벤션:
  LLM_SERVICE_ID → x-service-id
  LLM_USER_ID    → x-user-id
값이 없으면 해당 헤더는 보내지 않는다(공개 OpenRouter엔 불필요). chat/embeddings/
rerank 등 모든 OpenRouter 호출(직접 HTTP·agno default_headers)에 공통 적용한다.
"""
from __future__ import annotations

import os

_MAP = {"LLM_SERVICE_ID": "x-service-id", "LLM_USER_ID": "x-user-id"}


def custom_headers() -> dict[str, str]:
    out: dict[str, str] = {}
    for env_name, header in _MAP.items():
        val = (os.getenv(env_name) or "").strip()
        if val:
            out[header] = val
    return out
