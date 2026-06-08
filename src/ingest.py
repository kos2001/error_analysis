"""파이프라인 1단계: INGEST — Jira에서 고장 이슈를 적재한다.

Jira REST(api/3 검색 + api/2 상세)로 LSI 프로젝트의 이슈를 가져와
원본(raw) 레코드를 data/raw_issues.json 으로 저장한다. 전처리/그래프 단계는
이 raw 파일만 입력으로 받으므로, Jira 접속 없이도 재현·재실행이 가능하다.

인증(.env): JIRA_BASE_URL, JIRA_PROJECT_KEY, (Cloud) JIRA_EMAIL+JIRA_API_TOKEN
            또는 (Server/DC) JIRA_PAT

사용:
    set -a && source .env && set +a
    .venv/bin/python src/ingest.py                 # 완료(Resolved) 이슈만
    .venv/bin/python src/ingest.py --status all     # 전체 상태
    .venv/bin/python src/ingest.py --status "진행 중"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RAW_JSON = DATA_DIR / "raw_issues.json"

DEFAULT_STATUS = "완료"  # Resolved (team-managed 한글 워크플로우)


def jira_session() -> tuple[requests.Session, str]:
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    s = requests.Session()
    pat = os.getenv("JIRA_PAT")
    if pat:
        s.headers["Authorization"] = f"Bearer {pat}"
    else:
        s.auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
    s.headers["Accept"] = "application/json"
    return s, base


def _all_keys(s: requests.Session, base: str, project: str) -> list[tuple[str, str]]:
    """(key, status_name) 전체 — api/3 search/jql 페이지네이션."""
    out: list[tuple[str, str]] = []
    token = None
    while True:
        body = {"jql": f"project={project} ORDER BY key ASC",
                "maxResults": 100, "fields": ["status"]}
        if token:
            body["nextPageToken"] = token
        r = s.post(f"{base}/rest/api/3/search/jql", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        for i in data.get("issues", []):
            out.append((i["key"], i["fields"]["status"]["name"]))
        if data.get("isLast", True) or not data.get("nextPageToken"):
            break
        token = data["nextPageToken"]
    return out


def fetch_issues(status: str | None = DEFAULT_STATUS) -> list[dict]:
    """status=None 또는 'all' 이면 전체. api/2 상세로 plain-text 본문/코멘트 취득."""
    s, base = jira_session()
    project = os.getenv("JIRA_PROJECT_KEY", "LSI")
    pairs = _all_keys(s, base, project)
    if status and status.lower() != "all":
        pairs = [(k, st) for k, st in pairs if st == status]

    issues: list[dict] = []
    for key, _st in pairs:
        rf = s.get(f"{base}/rest/api/2/issue/{key}",
                   params={"fields": "summary,description,labels,priority,components,status,created"},
                   timeout=30)
        rf.raise_for_status()
        f = rf.json()["fields"]
        rc = s.get(f"{base}/rest/api/2/issue/{key}/comment", timeout=30)
        rc.raise_for_status()
        issues.append({
            "key": key,
            "summary": f.get("summary", ""),
            "description": f.get("description") or "",
            "labels": f.get("labels", []),
            "priority": (f.get("priority") or {}).get("name", ""),
            "components": [c["name"] for c in (f.get("components") or [])],
            "status": f["status"]["name"],
            "created": f.get("created", ""),
            "comments": [c["body"] for c in rc.json().get("comments", [])],
        })
    return issues


def save(issues: list[dict], path: Path = RAW_JSON) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Jira 이슈 적재 (ingest)")
    ap.add_argument("--status", default=DEFAULT_STATUS,
                    help="필터할 상태명 (기본: 완료). 'all'이면 전체.")
    ap.add_argument("--out", default=str(RAW_JSON))
    args = ap.parse_args()

    if not os.getenv("JIRA_BASE_URL"):
        print("[중단] .env 를 source 하세요: set -a && source .env && set +a")
        return 2

    print(f"[ingest] Jira에서 이슈 적재 중 (status={args.status})...")
    issues = fetch_issues(args.status)
    save(issues, Path(args.out))
    print(f"[ingest] {len(issues)}건 → {Path(args.out).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
