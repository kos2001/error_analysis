"""Jira 폴링 증분 동기화 — 변경된 이슈만 재적재해 KB(all_raw_issues.json)를 최신으로 유지.

웹훅(`POST /webhook/jira`)과 같은 일을 하지만 공개 URL이 필요 없다. Jira에
"마지막 동기화 이후 변경된 이슈"를 물어보고, 해당 이슈만 상세 재조회해 upsert 한다.

시각 비교는 절대시각 대신 JQL 상대시각(`updated >= -12m`)을 쓴다 — 절대시각은
Jira 계정 타임존과 서버 로컬 타임존이 어긋나면 변경을 통째로 놓치기 때문이다.

삭제는 `updated` JQL로 탐지할 수 없으므로, N회마다 전체 키 목록을 대조해
사라진 키를 제거한다(reconcile).

사용:
    set -a && source .env && set +a
    .venv/bin/python src/jira_sync.py              # 1회 증분 동기화
    .venv/bin/python src/jira_sync.py --full       # 전체 재적재
    .venv/bin/python src/jira_sync.py --watch 30   # 30초 주기 폴링(포그라운드)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import sys as _sys
ROOT = Path(__file__).resolve().parent.parent
_sys.path.insert(0, str(ROOT / "src"))

import ingest  # noqa: E402

ALL_RAW = ROOT / "data" / "all_raw_issues.json"
STATE = ROOT / "data" / "jira_sync_state.json"

# 폴링 창에 얹는 여유분(초). 이슈가 검색 인덱스에 반영되는 지연과 폴 간격 흔들림을
# 흡수한다. 중복 upsert는 무해(같은 키를 덮어씀)하지만 누락은 복구되지 않는다.
OVERLAP_SEC = 120
# 삭제 탐지용 전체 키 대조 주기(폴 횟수). 매 폴마다 하기엔 요청이 아깝고,
# 삭제는 드문 이벤트라 느슨하게 맞춘다.
RECONCILE_EVERY = 10


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state() -> dict:
    st = _read_json(STATE, {})
    return st if isinstance(st, dict) else {}


def updated_keys(minutes: int) -> list[str]:
    """최근 `minutes`분 내 변경된 이슈 키 (JQL 상대시각)."""
    s, base = ingest.jira_session()
    project = os.getenv("JIRA_PROJECT_KEY", "LSI")
    jql = f"project={project} AND updated >= -{max(1, minutes)}m ORDER BY key ASC"
    keys, token = [], None
    while True:
        body = {"jql": jql, "maxResults": 100, "fields": ["status"]}
        if token:
            body["nextPageToken"] = token
        r = s.post(f"{base}/rest/api/3/search/jql", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        keys.extend(i["key"] for i in data.get("issues", []))
        if data.get("isLast", True) or not data.get("nextPageToken"):
            break
        token = data["nextPageToken"]
    return keys


def _fetch_many(keys: list[str], workers: int = 8) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    s, base = ingest.jira_session()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(keys) or 1))) as ex:
        return list(ex.map(lambda k: ingest._issue_raw(s, base, k), keys))


def _apply(raw: list[dict], fetched: list[dict], removed: set[str]) -> list[dict]:
    by_key = {r.get("key"): r for r in raw if isinstance(r, dict)}
    for issue in fetched:
        by_key[issue["key"]] = issue
    for k in removed:
        by_key.pop(k, None)
    return sorted(by_key.values(), key=lambda r: r.get("key", ""))


def sync(full: bool = False, reconcile: bool = False) -> dict:
    """1회 동기화. 반환: {changed, upserted, deleted, mode, checked_minutes, total}.

    `changed`가 False면 호출측은 KB 캐시를 무효화하지 않아야 한다 — 무효화는
    전체 재빌드(임베딩 캐시 미스 시 수 초)를 유발하므로 빈 폴에서 지불하면 손해다.
    """
    state = load_state()
    now = time.time()

    if full or not state.get("last_sync_ts"):
        issues = ingest.fetch_issues("all")
        _write_json_atomic(ALL_RAW, issues)
        _write_json_atomic(STATE, {"last_sync_ts": now, "polls": 0,
                                   "last_result": {"mode": "full", "total": len(issues)}})
        return {"changed": True, "upserted": len(issues), "deleted": 0,
                "mode": "full", "checked_minutes": None, "total": len(issues)}

    elapsed = now - float(state["last_sync_ts"])
    minutes = max(1, math.ceil((elapsed + OVERLAP_SEC) / 60))
    keys = updated_keys(minutes)

    polls = int(state.get("polls", 0)) + 1
    do_reconcile = reconcile or (polls % RECONCILE_EVERY == 0)

    raw = _read_json(ALL_RAW, [])
    if not isinstance(raw, list):
        raw = []
    known = {r.get("key") for r in raw if isinstance(r, dict)}

    removed: set[str] = set()
    if do_reconcile:
        s, base = ingest.jira_session()
        live = {k for k, _ in ingest._all_keys(s, base, os.getenv("JIRA_PROJECT_KEY", "LSI"))}
        removed = known - live

    fetched = _fetch_many(keys) if keys else []
    changed = bool(fetched or removed)
    if changed:
        _write_json_atomic(ALL_RAW, _apply(raw, fetched, removed))

    result = {"changed": changed, "upserted": len(fetched), "deleted": len(removed),
              "mode": "reconcile" if do_reconcile else "incremental",
              "checked_minutes": minutes, "total": len(known | set(keys)) - len(removed)}
    _write_json_atomic(STATE, {"last_sync_ts": now, "polls": polls, "last_result": result})
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Jira 폴링 증분 동기화")
    ap.add_argument("--full", action="store_true", help="전체 재적재")
    ap.add_argument("--reconcile", action="store_true", help="이번 회차에 삭제 대조 강제")
    ap.add_argument("--watch", type=int, default=0, metavar="SEC",
                    help="주어진 주기로 계속 폴링(포그라운드)")
    args = ap.parse_args()
    if not os.getenv("JIRA_BASE_URL"):
        print("[중단] .env 를 source 하세요: set -a && source .env && set +a")
        return 2
    while True:
        r = sync(full=args.full, reconcile=args.reconcile)
        print(f"[jira_sync] {r}")
        if not args.watch:
            return 0
        args.full = False
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
