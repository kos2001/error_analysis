# LSI 고장 분석 이슈 — Jira 채우기 가이드 (Jira Cloud)

생성된 50건의 가짜 칩/펌웨어 고장 분석 이슈를 Jira Cloud에 채우는 두 가지 방법.
데이터는 이미 준비됨:

- `scripts/lsi_failure_data.py` — 데이터 생성기 (칩 10라인 × 24 고장 시나리오)
- `scripts/jira_seed.py` — REST API 푸시 스크립트
- `data/jira_seed_backup.json` — 50건 전체 데이터
- `data/jira_import.csv` — CSV 가져오기용 (50행)

**구성**: Open 15 / In Progress 10 / Resolved 25. 해결된 25건에는 시니어 엔지니어의
근본원인 분석 코멘트(디버깅 접근 + Root Cause + Resolution + Workaround)가 포함됨.

---

## 먼저: 내 Jira 사이트 주소 찾기

필요한 값은 `https://○○○.atlassian.net` 형태의 **사이트 주소**입니다.
(조직 관리 페이지 `home.atlassian.com/o/...` 나 `admin.atlassian.com` 은 사이트 주소가 아님)

찾는 법:
1. 아무 Atlassian 화면에서 **왼쪽 위 앱 스위처(점 9개 ⋮⋮⋮)** 클릭
2. **Jira** 선택
3. 주소창이 `https://○○○.atlassian.net/jira/...` 로 바뀜 → 앞부분 `https://○○○.atlassian.net` 이 사이트 주소

---

## 방법 A — REST API 스크립트 (자동, 토큰 필요)

### A-1. API 토큰 발급
1. https://id.atlassian.com/manage-profile/security/api-tokens 접속
2. **Create API token** → 이름 `lsi-seed` → **Create**
3. 토큰 문자열 복사 (창 닫으면 다시 못 봄)

### A-2. `.env` 채우기
```
JIRA_BASE_URL=https://○○○.atlassian.net
JIRA_PROJECT_KEY=LSI
JIRA_EMAIL=본인_로그인_이메일
JIRA_API_TOKEN=복사한_토큰
# JIRA_PAT 은 비워둠 (Cloud)
```
> `JIRA_PROJECT_KEY`: 이슈 번호 `ABC-123`의 `ABC`. 프로젝트가 없으면 Jira에서
> 새 프로젝트(예: "LSI Failure Analysis")를 만들고 그 키 사용.

### A-3. 실행
```sh
set -a && source .env && set +a
.venv/bin/python scripts/jira_seed.py            # 50건 생성
# 옵션:
.venv/bin/python scripts/jira_seed.py --count 30 # 건수 변경
.venv/bin/python scripts/jira_seed.py --dry-run  # API 없이 백업만
```
결과는 `data/jira_seed_result.json` 에 생성된 이슈 키 목록으로 저장됨.

**스크립트 동작**: 인증 확인 → 우선순위/컴포넌트 매핑 → 이슈 생성 →
해결 이슈에 분석 코멘트 부착 → 상태 전환(In Progress/Resolved) 시도.
프로젝트 워크플로우에 따라 상태 전환 이름이 다르면 일부 전환은 건너뛸 수 있음(이슈 생성 자체는 성공).

---

## 방법 B — CSV 가져오기 (토큰 불필요, Jira 관리자 권한 필요)

1. `https://○○○.atlassian.net` 접속
2. 우측 상단 **⚙️ 설정 → System**
   (직접 이동: `https://○○○.atlassian.net/secure/admin/ViewSystemInfo.jspa`)
3. 좌측 메뉴 **Import and export → External System Import**
4. **CSV** 선택
5. **파일 선택**: `data/jira_import.csv` 업로드
6. **대상 프로젝트** 선택 (없으면 먼저 생성)
7. **필드 매핑** 확인:
   | CSV 컬럼 | Jira 필드 |
   |----------|-----------|
   | Summary | Summary |
   | Issue Type | Issue Type |
   | Priority | Priority |
   | Component | Component/s |
   | Labels | Labels |
   | Status | Status |
   | Description | Description |
   | Comment | Comment |
8. **Import** 실행 → 50건 생성

> CSV의 Status(Open/In Progress/Resolved)는 대상 프로젝트의 워크플로우에 동일 이름
> 상태가 있어야 매핑됩니다. 없으면 기본 상태로 들어가며, 나중에 일괄 전환 가능.

---

## 데이터 커스터마이즈

`scripts/lsi_failure_data.py` 상단에서 수정:
- `CHIP_LINES`: 칩 제품 라인 추가/변경
- `FAILURE_TEMPLATES`: 고장 시나리오(증상·재현·근본원인·해결책) 추가/변경
- `generate_issues(target_count=...)`: 생성 건수, 상태 분포 조정

수정 후 `--dry-run` 으로 백업을 다시 생성해 확인 → 방법 A/B 중 하나로 업로드.
