"""Jira 댓글 I/O — RCA 자동 분석 댓글의 조회/게시/갱신 (단일 책임).

api/2 채택 이유: api/3 댓글은 ADF(JSON 문서)를 요구하지만 api/2는 wiki markup
플레인 문자열을 받아 h3./표 등이 그대로 렌더된다 (ingest.py 상세 조회와 동일 컨벤션).

인증은 ingest.jira_session() 재사용 (.env: JIRA_EMAIL+JIRA_API_TOKEN 또는 JIRA_PAT).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest import jira_session  # noqa: E402


def get_comments(key: str) -> list[dict]:
    """이슈의 전체 댓글 [{id, body, author, created}] 반환."""
    s, base = jira_session()
    r = s.get(f"{base}/rest/api/2/issue/{key}/comment",
              params={"maxResults": 100}, timeout=30)
    r.raise_for_status()
    out = []
    for c in r.json().get("comments", []):
        out.append({
            "id": c["id"],
            "body": c.get("body", ""),
            "author": (c.get("author") or {}).get("displayName", ""),
            "created": c.get("created", ""),
        })
    return out


def post_comment(key: str, body: str) -> dict:
    s, base = jira_session()
    r = s.post(f"{base}/rest/api/2/issue/{key}/comment",
               json={"body": body}, timeout=30)
    r.raise_for_status()
    return r.json()


def update_comment(key: str, comment_id: str, body: str) -> dict:
    s, base = jira_session()
    r = s.put(f"{base}/rest/api/2/issue/{key}/comment/{comment_id}",
              json={"body": body}, timeout=30)
    r.raise_for_status()
    return r.json()
