# RCA Commenter 설계 — 해결 사례 기반 미해결 이슈 자동 근본원인 댓글

> 목표: Jira(LSI 프로젝트)의 **해야 할 일 / 진행 중** 이슈에 대해, **완료(해결)** 이슈
> 지식베이스를 근거로 예상 근본원인·해결책·우회책을 분석하고 **Jira 댓글로 게시**한다.
> 작성일: 2026-06-10

## 1. 현재 자산 vs 부족한 것

이 repo는 분석 파이프라인의 앞 단계(검색·추천·설명 생성)는 이미 갖추고 있고,
**"Jira에 댓글을 다는" 마지막 구간과 그 운영 안전장치**가 없다.

| 단계 | 상태 | 모듈 |
|---|---|---|
| Jira 이슈 적재 (api/3 검색 + api/2 상세, 상태 필터) | ✅ 있음 | `src/ingest.py` |
| 파싱 (chip/category/symptom + 해결 이슈의 root_cause/resolution/workaround 추출) | ✅ 있음 | `src/preprocess.py::parse_issue` |
| 유사 해결 사례 검색 (hybrid RRF, 합성 평가 P@1=1.0) + proposal/confidence | ✅ 있음 | `src/recommender.py` |
| LLM 종합 설명 (한국어, 근거 키 인용 프롬프트) | ✅ 있음 | `backend/server.py::_llm_explain` |
| LLM 엔진 (agno / OpenRouter HTTP 직접) | ✅ 있음 | `backend/server.py::_llm_stream` |
| 언어 규칙 검증/재작성 (CJK 한자 금지) | ✅ 있음 | `src/lang_validator.py` |
| **Jira 댓글 게시 (POST)** | ❌ 없음 | 신규 `src/jira_commenter.py` |
| **배치 오케스트레이션 CLI (dry-run/live)** | ❌ 없음 | 신규 `scripts/rca_comment.py` |
| **멱등성 (중복 댓글 방지·재분석 판단)** | ❌ 없음 | 봇 마커 + `tmp_db/rca_state.json` |
| **품질 게이트 (인용 검증·신뢰도 톤 조절·fallback)** | ❌ 없음 | CLI 내 gate 단계 |

## 2. 데이터 흐름

```
Jira ──ingest(status=all)──▶ raw ──parse_issue──▶ records
                                      │
        ┌─────────────────────────────┴──────────────────────┐
  status=='완료' (92건)                          status∈{해야 할 일, 진행 중} (108건)
        │                                                     │
   Recommender(hybrid) KB 빌드                          분석 대상(target)
        │                                                     │
        └────────── target별 recommend(k=3) ──────────────────┘
                          │  matches + proposal(confidence)
                   [게이트 1] coverage: matches 없으면 skip
                          │
                LLM 댓글 생성 (agno / OpenRouter)
                          │
                   [게이트 2] lang_validator (한자 검출 → 재작성)
                   [게이트 3] 인용 검증: 본문 속 LSI-키 ⊆ matches 키
                              실패 → 1회 재생성 → 또 실패 → 템플릿 fallback
                          │
                   [게이트 4] 멱등성: 기존 봇 댓글 + 입력 해시 비교
                              동일 → skip / 변경 → 갱신(또는 새 댓글)
                          │
              dry-run: 미리보기 파일 저장  |  --live: Jira POST
                          │
                tmp_db/rca_state.json 기록 + 실행 리포트
```

## 3. 신규 컴포넌트 설계

### 3.1 `src/jira_commenter.py` — Jira 댓글 I/O (단일 책임)

- `get_comments(key) -> list[dict]` — `GET /rest/api/2/issue/{key}/comment`
- `post_comment(key, body) -> dict` — `POST /rest/api/2/issue/{key}/comment`, `{"body": "<wiki markup 문자열>"}`
- `update_comment(key, comment_id, body)` — 갱신 모드용
- 인증: `ingest.jira_session()` 재사용 (.env의 JIRA_EMAIL+API_TOKEN 또는 JIRA_PAT)

**api/2 vs api/3 결정**: api/3 댓글은 ADF(JSON 문서 포맷)를 요구해 조립이 번거롭다.
api/2는 wiki markup 플레인 문자열을 받아 `h3.` `*bold*` 등이 그대로 렌더되므로 api/2 채택.
(ingest.py도 같은 이유로 api/2 상세를 쓰고 있음 — 컨벤션 일치. 추후 ADF 전환 시 이 모듈만 수정.)

### 3.2 `scripts/rca_comment.py` — 오케스트레이션 CLI

```
.venv/bin/python scripts/rca_comment.py                  # dry-run: 전체 대상 미리보기
.venv/bin/python scripts/rca_comment.py --keys LSI-7     # 특정 이슈만
.venv/bin/python scripts/rca_comment.py --status "진행 중"  # 상태 필터
.venv/bin/python scripts/rca_comment.py --live           # 실제 게시
.venv/bin/python scripts/rca_comment.py --live --update  # 입력 변경 시 기존 댓글 갱신
.venv/bin/python scripts/rca_comment.py --no-llm         # 템플릿 댓글만 (LLM 없이)
```

- **dry-run이 기본** — `tmp_db/rca_preview/LSI-7.txt` 형태로 저장 + 콘솔 요약표 출력
- 게시 간 `sleep 0.5s` (Jira Cloud rate limit 여유), 5xx 1회 재시도
- 종료 시 리포트: 대상 N / 게시 P / 스킵 S(사유별) / 실패 F

### 3.3 댓글 포맷 (Jira wiki markup)

```
🤖 *자동 근본원인 분석* (RCA-bot v1 | 근거: LSI-49, LSI-70 | 신뢰도: 높음)

h3. 예상 근본 원인
...(LLM 또는 템플릿 본문, 근거 키 인용 필수)...

h3. 권장 해결 단계
# ...

h3. 임시 우회책
...

h3. 유사 해결 사례
|| 키 || 요약 || 점수 ||
| LSI-49 | ... | 0.041 |

_본 댓글은 과거 해결 이슈 기반 자동 분석입니다. 입력해시: a1b2c3d4_
```

- 첫 줄 `🤖 *자동 근본원인 분석*` + 마지막 줄 `입력해시:` 가 **봇 마커** — 멱등성 판별에 사용
- 신뢰도 라벨: `proposal.confidence ≥ 2/3` → "높음"(단정 톤), 미만 → "중간"(후보 병기 톤)
  — LLM 프롬프트에도 톤 지시로 반영

### 3.4 멱등성 (이중 안전망)

1. **원격(소스 오브 트루스)**: 게시 전 `get_comments(key)`에서 봇 마커 댓글 탐색.
   - 마커의 입력해시 == 현재 입력해시 → skip
   - 다름 → `--update`면 갱신, 아니면 skip + 리포트에 "stale" 표기
2. **로컬 캐시**: `tmp_db/rca_state.json` `{key: {comment_id, input_hash, posted_at}}`
   — 원격 조회 실패 대비·실행 이력 추적용 (원격이 항상 우선)

**입력해시** = sha256(대상 이슈 summary+symptom + top-k 매치 키 목록)[:8]
→ 이슈 내용이 바뀌거나 KB에 더 좋은 사례가 추가되면 해시가 변해 재분석 대상이 된다.

### 3.5 품질 게이트 상세

| 게이트 | 규칙 | 실패 시 |
|---|---|---|
| coverage | matches 0건 | skip (사유 기록). "유사 사례 없음" 댓글은 달지 않음 — 노이즈 방지 |
| 언어 | lang_validator 한자/CJK 검출 | validate_and_fix 재작성, 그래도 실패 시 템플릿 fallback |
| 인용 | 본문 정규식 `LSI-\d+` 추출 ⊆ matches 키 집합 | 1회 재생성 → 실패 시 템플릿 fallback |
| 길이 | 댓글 ≤ 30KB (Jira 32KB 한도 여유) | 유사 사례 표 축소 |

**템플릿 fallback**: LLM 없이 `proposal.root_cause/resolution/workaround` + matches 표를
그대로 조립한 댓글. LLM 장애·환각 시에도 파이프라인이 항상 완주하도록 보장 (`--no-llm`과 동일 경로).

## 4. 트레이드오프 / 결정 사항

- **댓글 vs 필드 수정**: 댓글이 비파괴적이고 사람이 검토·반박 가능 → 댓글 채택.
- **LLM 종합 vs 템플릿**: LLM은 복수 사례 합성·이슈 맞춤 표현에 유리하나 환각 위험
  → 인용 게이트 + fallback으로 상쇄. 둘 다 구현하고 LLM을 기본으로.
- **skip vs "사례 없음" 댓글**: 합성 데이터에선 coverage 100%지만 실데이터에선 미매치 발생
  → 미매치 댓글은 노이즈이므로 skip 후 리포트에만 남김.
- **재실행 정책**: 무조건 재댓글 금지. 입력해시 변경 시에만 갱신(opt-in `--update`).
- **평가 주의**: hybrid P@1=1.0은 합성 데이터(같은 템플릿 변형) 기준 —
  실데이터 전환 시 `eval_recommender.py`로 재측정 후 confidence 임계 재조정 필요.

## 5. 구현 단계 (PR 분할)

1. **PR1 — 게시 경로**: `jira_commenter.py` + `rca_comment.py` (dry-run, 템플릿 댓글, 리포트)
2. **PR2 — 품질/멱등**: LLM 생성 통합(agno), 4개 게이트, 입력해시 멱등성, `--live/--update`
3. **PR3 — 선택 확장**: `POST /rca/comment/{key}` 백엔드 엔드포인트 + 웹 UI "Jira에 게시" 버튼,
   cron 주기 실행(신규/변경 이슈만)

## 6. 리스크

- **권한**: API 토큰 계정에 해당 프로젝트 *Add Comments* 권한 필요 (시드 생성 계정이므로 충분할 것).
- **봇 댓글이 KB를 오염**: `parse_issue`가 댓글에서 root_cause를 추출하므로, 봇 댓글이 달린
  이슈가 나중에 '완료'되면 자동 분석 댓글이 시니어 분석으로 오인될 수 있음
  → `parse_issue`에서 봇 마커(🤖) 댓글은 추출 대상에서 제외하는 가드 1줄 필요. **(중요)**
- **api/2 deprecation**: Atlassian이 장기적으로 api/3 전환 예고 — `jira_commenter.py` 한 곳만 고치면 됨.
