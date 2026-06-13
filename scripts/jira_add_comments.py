"""이미 생성된 Jira 이슈에 협업 스레드 코멘트를 추가(idempotent)하는 스크립트.

jira_seed.py 가 '이슈 생성'을 담당한다면, 이 스크립트는 '기존 이슈에 코멘트 보강'을
담당한다 (단일 책임). 이슈를 재생성하지 않는다.

키 매핑(robust): 라이브 Jira의 전체 이슈를 key 오름차순(=생성 순서)으로 조회하고,
시드 배치(lsi → nfc → nfc2)를 동일 순서로 재생성해 요약(summary)으로 정렬 정합한다.
Jira는 생성 시 key를 순차 부여하므로 배치 경계가 그대로 정렬된다. stale 한
result.json 에 의존하지 않고 프로젝트의 모든 이슈를 대상으로 한다.

멱등성: 게시 전 이슈의 기존 코멘트를 조회해, 동일 본문(헤더 시그니처 기준)이 이미
있으면 건너뛴다. 여러 번 실행해도 중복이 생기지 않고, Resolved 이슈에 이미 달려 있는
시니어 RCA 코멘트도 재게시하지 않는다.

사용:
  set -a && source .env && set +a
  .venv/bin/python scripts/jira_add_comments.py --dry-run        # 전체 미리보기
  .venv/bin/python scripts/jira_add_comments.py                  # 전체 게시
  .venv/bin/python scripts/jira_add_comments.py --only nfc2      # 특정 배치만
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))
from lsi_failure_data import (  # noqa: E402
    Issue, generate_issues, generate_nfc_issues, generate_nfc_v2_issues,
)

# 배치 정의: (이름, 생성 함수, 정합용 넉넉한 건수) — 순서 = 실제 push 순서
SET_BUILDERS = [
    ("lsi", lambda: generate_issues(target_count=400)),
    ("nfc", lambda: generate_nfc_issues(target_count=200)),
    ("nfc2", lambda: generate_nfc_v2_issues(target_count=200)),
]


def _signature(body: str) -> str:
    """코멘트 본문의 비교용 시그니처 — 첫 의미있는 줄(헤더)을 정규화."""
    for line in body.strip().splitlines():
        s = line.strip()
        if s:
            return re.sub(r"\s+", " ", re.sub(r"^h\d\.\s*|\*", "", s)).strip()
    return ""


def _fetch_live(session, base: str, project: str) -> list[tuple[str, str]]:
    """(key, summary) 전체를 key 오름차순(생성 순서)으로 반환."""
    rows: list[tuple[int, str, str]] = []
    token = None
    while True:
        body = {"jql": f"project={project} ORDER BY key ASC",
                "maxResults": 100, "fields": ["summary"]}
        if token:
            body["nextPageToken"] = token
        r = session.post(f"{base}/rest/api/3/search/jql", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        for i in data.get("issues", []):
            num = int(i["key"].split("-")[1])
            rows.append((num, i["key"], i["fields"].get("summary", "")))
        if data.get("isLast", True) or not data.get("nextPageToken"):
            break
        token = data["nextPageToken"]
    rows.sort()
    return [(k, summ) for _n, k, summ in rows]


def _align(live: list[tuple[str, str]],
           only: str | None) -> list[tuple[str, str, Issue]]:
    """라이브(key, summary)를 배치 재생성 결과와 요약으로 정렬 정합.

    반환: [(key, set_name, Issue)] — 정합된 이슈만.
    """
    mapping: list[tuple[str, str, Issue]] = []
    pos = 0
    for name, build in SET_BUILDERS:
        issues = build()
        gi = 0
        while (pos < len(live) and gi < len(issues)
               and live[pos][1] == issues[gi].summary):
            if only is None or only == name:
                mapping.append((live[pos][0], name, issues[gi]))
            pos += 1
            gi += 1
        if pos < len(live) and gi < len(issues):
            # 배치 경계 — 다음 배치로 진행
            continue
    if pos < len(live):
        print(f"  ! 경고: {len(live) - pos}건의 라이브 이슈가 어떤 배치와도 정합되지 "
              f"않았습니다 (다음: {live[pos][0]} {live[pos][1][:40]!r}).")
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["lsi", "nfc", "nfc2"], default=None,
                    help="특정 배치만 대상으로 (기본: 전체)")
    ap.add_argument("--dry-run", action="store_true",
                    help="API 게시 없이 추가될 코멘트만 집계")
    args = ap.parse_args()

    if not os.getenv("JIRA_BASE_URL"):
        print("[중단] .env 가 로드되지 않았습니다: set -a && source .env && set +a")
        return 2

    from ingest import jira_session  # noqa: E402
    from jira_commenter import get_comments, post_comment  # noqa: E402

    session, base = jira_session()
    project = os.getenv("JIRA_PROJECT_KEY", "LSI")
    live = _fetch_live(session, base, project)
    print(f"라이브 이슈 {len(live)}건 조회 ({live[0][0]} ~ {live[-1][0]})")

    mapping = _align(live, args.only)
    print(f"정합 완료: {len(mapping)}건 대상"
          + (f" (배치 {args.only}만)" if args.only else ""))

    total_post = total_skip = 0
    for key, set_name, issue in mapping:
        if not issue.comments:
            continue
        try:
            existing_sigs = {_signature(c["body"]) for c in get_comments(key)}
        except Exception as e:
            print(f"  ! {key} 기존 코멘트 조회 실패(전체 게시로 진행): {e}")
            existing_sigs = set()

        posted = skipped = 0
        for c in issue.comments:
            if _signature(c.body) in existing_sigs:
                skipped += 1
                continue
            if args.dry_run:
                posted += 1
                continue
            try:
                post_comment(key, c.body)
                posted += 1
                existing_sigs.add(_signature(c.body))
                time.sleep(0.15)  # rate-limit 완화
            except Exception as e:
                print(f"  ! {key} [{c.kind}] 게시 실패: {e}")
        total_post += posted
        total_skip += skipped
        tag = "[dry-run] " if args.dry_run else ""
        print(f"  {tag}{key:8} [{set_name}] {issue.summary[:42]:42} +{posted} / {skipped} 건너뜀")

    verb = "게시 예정" if args.dry_run else "게시 완료"
    print(f"\n완료: {total_post}건 {verb}, {total_skip}건 중복 건너뜀 ({len(mapping)} 이슈)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
