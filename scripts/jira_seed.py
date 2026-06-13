"""LSI 칩/펌웨어 고객 고장 이슈를 Jira에 시드(seed)하는 스크립트.

지원 인증 방식:
  - Jira Cloud:        JIRA_EMAIL + JIRA_API_TOKEN (HTTP Basic)
  - Jira Server/DC:    JIRA_PAT (Bearer 토큰)

필수 .env 변수:
  JIRA_BASE_URL   예) https://your-domain.atlassian.net  또는  https://jira.company.com
  JIRA_PROJECT_KEY 예) LSI
  (Cloud) JIRA_EMAIL, JIRA_API_TOKEN
  (DC)    JIRA_PAT

사용:
  set -a && source .env && set +a
  .venv/bin/python scripts/jira_seed.py            # 실제 생성
  .venv/bin/python scripts/jira_seed.py --dry-run  # 백업 파일만 생성, API 호출 없음
  .venv/bin/python scripts/jira_seed.py --count 30 # 건수 지정

항상 data/jira_seed_backup.json 및 data/jira_import.csv 를 생성한다.
CSV는 Jira의 External System Import(CSV)로도 업로드 가능.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

# requests는 venv에 설치되어 있음
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lsi_failure_data import generate_issues, Issue  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
BACKUP_JSON = DATA_DIR / "jira_seed_backup.json"
IMPORT_CSV = DATA_DIR / "jira_import.csv"
RESULT_JSON = DATA_DIR / "jira_seed_result.json"


# ---------------------------------------------------------------------------
# Jira 클라이언트
# ---------------------------------------------------------------------------


class JiraClient:
    def __init__(self, base_url: str, project_key: str,
                 email: str | None, api_token: str | None, pat: str | None):
        self.base = base_url.rstrip("/")
        self.project_key = project_key
        self.session = requests.Session()
        if pat:
            self.session.headers["Authorization"] = f"Bearer {pat}"
            self.auth_mode = "PAT (Bearer)"
        elif email and api_token:
            self.session.auth = (email, api_token)
            self.auth_mode = "Basic (email + API token)"
        else:
            raise ValueError("인증 정보 부족: JIRA_PAT 또는 (JIRA_EMAIL + JIRA_API_TOKEN) 필요")
        self.session.headers["Content-Type"] = "application/json"
        self.session.headers["Accept"] = "application/json"
        # 캐시
        self._priorities: dict[str, str] | None = None
        self._components: dict[str, str] | None = None

    # --- 사전 점검 ---
    def verify(self) -> dict:
        r = self.session.get(f"{self.base}/rest/api/2/myself", timeout=20)
        r.raise_for_status()
        me = r.json()
        r2 = self.session.get(f"{self.base}/rest/api/2/project/{self.project_key}", timeout=20)
        r2.raise_for_status()
        return {"user": me.get("displayName") or me.get("name"), "project": r2.json().get("name")}

    def _load_priorities(self) -> dict[str, str]:
        if self._priorities is None:
            try:
                r = self.session.get(f"{self.base}/rest/api/2/priority", timeout=20)
                r.raise_for_status()
                self._priorities = {p["name"].lower(): p["id"] for p in r.json()}
            except Exception:
                self._priorities = {}
        return self._priorities

    def _load_components(self) -> dict[str, str]:
        if self._components is None:
            self._components = {}
            try:
                r = self.session.get(
                    f"{self.base}/rest/api/2/project/{self.project_key}/components", timeout=20)
                r.raise_for_status()
                self._components = {c["name"]: c["id"] for c in r.json()}
            except Exception:
                self._components = {}
        return self._components

    def ensure_component(self, name: str) -> str | None:
        comps = self._load_components()
        if name in comps:
            return comps[name]
        # 컴포넌트 생성 시도 (권한 없으면 무시하고 component 생략)
        try:
            r = self.session.post(f"{self.base}/rest/api/2/component", json={
                "name": name, "project": self.project_key,
            }, timeout=20)
            if r.status_code in (200, 201):
                cid = r.json()["id"]
                comps[name] = cid
                return cid
        except Exception:
            pass
        return None

    def create_issue(self, issue: Issue, use_priority: bool, use_components: bool) -> dict:
        fields: dict = {
            "project": {"key": self.project_key},
            "summary": issue.summary,
            "description": issue.description,
            "issuetype": {"name": issue.issue_type},
            "labels": issue.labels,
        }
        if use_priority:
            pri = self._load_priorities().get(issue.priority.lower())
            if pri:
                fields["priority"] = {"id": pri}
        if use_components:
            cid = self.ensure_component(issue.component)
            if cid:
                fields["components"] = [{"id": cid}]

        r = self.session.post(f"{self.base}/rest/api/2/issue",
                              json={"fields": fields}, timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"이슈 생성 실패 {r.status_code}: {r.text[:400]}")
        return r.json()

    def add_comment(self, key: str, body: str) -> None:
        r = self.session.post(f"{self.base}/rest/api/2/issue/{key}/comment",
                              json={"body": body}, timeout=20)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"코멘트 실패 {r.status_code}: {r.text[:200]}")

    def transition_to(self, key: str, target_status: str) -> bool:
        """가능한 transition 중 target_status와 이름이 일치하는 것을 실행."""
        try:
            r = self.session.get(f"{self.base}/rest/api/2/issue/{key}/transitions", timeout=20)
            r.raise_for_status()
            transitions = r.json().get("transitions", [])
            # 직접 매칭 우선
            tid = None
            for tr in transitions:
                to_name = (tr.get("to") or {}).get("name", "").lower()
                if to_name == target_status.lower() or tr["name"].lower() == target_status.lower():
                    tid = tr["id"]
                    break
            if not tid:
                return False
            r2 = self.session.post(f"{self.base}/rest/api/2/issue/{key}/transitions",
                                   json={"transition": {"id": tid}}, timeout=20)
            return r2.status_code in (200, 204)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 백업 파일 생성
# ---------------------------------------------------------------------------


def write_backups(issues: list[Issue]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    # JSON 백업 (전체 필드)
    payload = [{
        "summary": it.summary,
        "description": it.description,
        "issue_type": it.issue_type,
        "priority": it.priority,
        "labels": it.labels,
        "component": it.component,
        "status": it.status,
        "reporter": it.reporter_label,
        "customer": it.customer,
        "chip": it.chip,
        "fw_version": it.fw_version,
        "severity": it.severity,
        "category": it.category,
        "analysis_comment": it.analysis_comment,
        "comments": [
            {"author": c.author, "kind": c.kind,
             "day_offset": c.day_offset, "body": c.body}
            for c in it.comments
        ],
        "meta": it.meta,
    } for it in issues]
    BACKUP_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Jira External System Import용 CSV
    with IMPORT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "Summary", "Issue Type", "Priority", "Component", "Labels",
            "Status", "Description", "Comment",
        ])
        # Jira CSV는 'Comment' 컬럼을 여러 개 두면 각각 별도 코멘트로 임포트된다.
        # 가장 코멘트가 많은 이슈 기준으로 컬럼 수를 맞춘다.
        max_comments = max((len(it.comments) for it in issues), default=0)
        if max_comments > 1:
            f.seek(0)
            f.truncate()
            w.writerow([
                "Summary", "Issue Type", "Priority", "Component", "Labels",
                "Status", "Description",
            ] + ["Comment"] * max_comments)
        for it in issues:
            bodies = [c.body for c in it.comments] or ([it.analysis_comment]
                                                       if it.analysis_comment else [])
            bodies += [""] * (max(max_comments, 1) - len(bodies))
            w.writerow([
                it.summary, it.issue_type, it.priority, it.component,
                " ".join(it.labels), it.status, it.description,
            ] + bodies)
    print(f"백업 생성: {BACKUP_JSON.relative_to(ROOT)} , {IMPORT_CSV.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--skip", type=int, default=0,
                    help="앞에서부터 N건은 건너뛰고 그 이후만 push (이미 생성된 이슈 중복 방지)")
    ap.add_argument("--set", default="lsi", choices=["lsi", "nfc", "nfc2"],
                    help="시드 배치: lsi(기본 24종) | nfc(NFC Forum 8종) | nfc2(NFC 2차 6종)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 백업 파일만 생성")
    ap.add_argument("--no-transition", action="store_true", help="상태 전환(transition) 생략")
    args = ap.parse_args()

    if args.set == "nfc":
        from lsi_failure_data import generate_nfc_issues
        issues = generate_nfc_issues(target_count=args.count)
    elif args.set == "nfc2":
        from lsi_failure_data import generate_nfc_v2_issues
        issues = generate_nfc_v2_issues(target_count=args.count)
    else:
        issues = generate_issues(target_count=args.count)
    if args.skip:
        issues = issues[args.skip:]
        print(f"앞 {args.skip}건 건너뜀 → {len(issues)}건 push 대상")

    # 프로젝트 스키마에 맞춘 이슈 타입 오버라이드 (예: 팀 관리 프로젝트는 'Bug'가 없고 '작업')
    issue_type_override = os.getenv("JIRA_ISSUE_TYPE")
    if issue_type_override:
        for it in issues:
            it.issue_type = issue_type_override
    print(f"이슈 {len(issues)}건 생성됨 (상태: "
          f"{sum(i.status=='Open' for i in issues)} Open / "
          f"{sum(i.status=='In Progress' for i in issues)} In Progress / "
          f"{sum(i.status=='Resolved' for i in issues)} Resolved)")
    write_backups(issues)

    if args.dry_run:
        print("[--dry-run] API 호출을 건너뜁니다. 백업 파일만 확인하세요.")
        return 0

    base_url = os.getenv("JIRA_BASE_URL")
    project_key = os.getenv("JIRA_PROJECT_KEY")
    email = os.getenv("JIRA_EMAIL")
    api_token = os.getenv("JIRA_API_TOKEN")
    pat = os.getenv("JIRA_PAT")

    missing = [k for k, v in {
        "JIRA_BASE_URL": base_url, "JIRA_PROJECT_KEY": project_key,
    }.items() if not v]
    if missing:
        print(f"\n[중단] .env에 다음 변수가 필요합니다: {', '.join(missing)}")
        print("백업 파일은 생성되었으니, 자격증명 설정 후 다시 실행하세요.")
        return 2

    try:
        client = JiraClient(base_url, project_key, email, api_token, pat)
    except ValueError as e:
        print(f"\n[중단] {e}")
        return 2

    print(f"\nJira 연결 시도: {base_url} (project={project_key}, auth={client.auth_mode})")
    try:
        info = client.verify()
        print(f"✓ 인증 성공: user={info['user']}, project={info['project']}")
    except Exception as e:
        print(f"✗ 연결/인증 실패: {e}")
        return 3

    results = []
    created = 0
    for i, it in enumerate(issues, 1):
        try:
            res = client.create_issue(it, use_priority=True, use_components=True)
            key = res["key"]
            created += 1
            # 해결 분석 코멘트
            if it.analysis_comment:
                try:
                    client.add_comment(key, it.analysis_comment)
                except Exception as e:
                    print(f"  ! {key} 코멘트 실패: {e}")
            # 상태 전환
            transitioned = True
            if it.status != "Open" and not args.no_transition:
                # 상태별 후보 이름(영문/한글 워크플로우 모두 대응)
                status_candidates = {
                    "In Progress": ["In Progress", "진행 중", "진행중"],
                    "Resolved": ["Resolved", "Done", "완료", "Closed", "해결됨"],
                }
                if it.status == "Resolved":
                    # In Progress 경유가 필요한 워크플로우 대응
                    for nm in status_candidates["In Progress"]:
                        if client.transition_to(key, nm):
                            break
                    transitioned = any(client.transition_to(key, nm)
                                       for nm in status_candidates["Resolved"])
                else:
                    transitioned = any(client.transition_to(key, nm)
                                       for nm in status_candidates.get(it.status, [it.status]))
            results.append({"key": key, "summary": it.summary,
                            "status_target": it.status, "transitioned": transitioned})
            print(f"  [{i}/{len(issues)}] ✓ {key}  {it.summary[:60]}")
            time.sleep(0.15)  # rate-limit 완화
        except Exception as e:
            print(f"  [{i}/{len(issues)}] ✗ 실패: {e}")
            results.append({"key": None, "summary": it.summary, "error": str(e)})

    RESULT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: {created}/{len(issues)} 건 생성. 결과: {RESULT_JSON.relative_to(ROOT)}")
    return 0 if created == len(issues) else 1


if __name__ == "__main__":
    raise SystemExit(main())
