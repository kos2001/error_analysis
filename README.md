# LSI Error Analysis

LSI 칩/펌웨어 고객 고장 분석 어시스턴트. **과거에 해결된 이슈(Jira)를 지식베이스로,
진행 중이거나 시작 전인 미해결 이슈의 root-cause와 해결책을 자동 제안**한다.
시니어 엔지니어의 분석 경험을 그래프/검색 기반으로 재사용해 주니어 엔지니어를 지원하는 것이 목표.

## 무엇을 하나

1. **Jira 적재(ingest)** — LSI 프로젝트의 고장 이슈를 REST로 가져온다.
2. **전처리(preprocess)** — 칩/분류/증상/근본원인/해결책/엔티티를 추출하고,
   엔티티↔이슈 bipartite **지식 그래프**(networkx)를 만든다.
3. **탐색·추천(explorer / recommender)** — 미해결 이슈의 관찰 가능한 정보(요약·증상·칩·분류)로
   유사한 *해결된* 이슈를 검색하고, 그 근본원인/해결책을 제안한다. (옵션: LLM 종합 설명)
4. **프론트엔드** — 미해결 이슈를 고르면 과거 해결 사례 + 제안 근본원인/해결책/신뢰도를 보여준다.

## 검색 성능 (eval)

각 이슈의 잠재 '고장 템플릿'을 ground-truth로, 미해결 이슈에 대해 같은 템플릿의 해결 사례를
검색하는 정확도를 측정한다 (`src/eval_recommender.py`):

| method | P@1 | P@3 | MRR |
|--------|-----|-----|-----|
| graph (엔티티 중첩, baseline) | 0.96 | 1.0 | 0.973 |
| **bm25** | **1.0** | **1.0** | **1.0** |
| hybrid (bm25+graph+boost) | 1.0 | 1.0 | 1.0 |

> graph baseline 대비 bm25/hybrid로 P@1을 0.96 → 1.0 으로 끌어올림. (합성 데이터 기준)

## 파이프라인 실행

```sh
set -a && source .env && set +a            # Jira/OpenRouter 자격증명
.venv/bin/python scripts/run_pipeline.py    # ingest → preprocess → explorer
# 개별 단계
.venv/bin/python src/ingest.py
.venv/bin/python src/preprocess.py
.venv/bin/python src/explorer.py "PM9C3 thermal throttle link down"
.venv/bin/python src/explorer.py --viz       # tmp_db/lsi_solution_graph.html
# 성능 평가
.venv/bin/python src/eval_recommender.py --methods graph,bm25,hybrid
```

## 웹 앱 실행

```sh
bash scripts/dev.sh        # 백엔드(:8001) + 프론트(:5173)
```
- Jira 이슈 번호 입력(예: LSI-7) 또는 목록 선택 → 유사 해결 사례 + 제안 root-cause/해결책 + LLM 종합 분석

### 유사도 검색 (recommender)

- 랭킹: BM25 + 엔티티 그래프 (+ 다국어 임베딩) RRF 융합, 기본 `hybrid_embed`
  (`RVP_RECO_METHOD`로 변경). KB 문서는 **이슈 제기(요약/증상) + 문제 분석(디버깅
  접근/근본 원인)** 단계 내용으로 구성 — 해결 단계는 질의에 존재할 수 없어 제외.
- coverage 게이트: 임베딩 코사인 ≥0.5 또는 기술 엔티티 겹침 ≥1 미달 시
  "유사 사례 없음" 처리(LLM 설명도 생성 안 함). 매치별 강도 신호
  (`embed_cos`/`entity_overlap`/`bm25_raw`)를 API로 노출.
- 평가: `src/eval_recommender.py --paraphrase --doc-stages both` —
  264건 코퍼스(LSI+NFC) 기준 LOO·unresolved P@1 1.0, paraphrase 49문항
  P@1 .898 / P@3 .939 / 게이트 통과 .939 / 무관 질의 차단 .95 (hybrid_embed).

### LLM 설명 엔진

`/recommend?explain=true`·`/recommend/explain/stream` 의 종합 설명은 **agno(OpenRouter
HTTP 직접 호출)** 단일 엔진으로 생성한다. 모델·엔드포인트는 `.env`의 `OPENROUTER_*`로 설정
(`OPENROUTER_MODEL`/`OPENROUTER_BASE_URL`/`OPENROUTER_API_KEY`). 스트리밍은 SSE.

### Jira 변경 반영 (KB 최신 유지)

두 경로가 있고 **폴링이 기본**이다 — Jira Cloud가 로컬 서버에 도달할 수 없기 때문.

- **폴링(기본)**: 서버가 `RVP_JIRA_POLL_SEC`(기본 5초, 평균 반영 지연 2.5초) 주기로 "마지막 동기화 이후
  변경된 이슈"를 물어 해당 이슈만 재적재하고, **변경이 있을 때만** 추천 캐시를
  무효화한다(빈 폴은 재빌드 비용을 물지 않는다). 삭제는 `updated` JQL로 잡히지
  않으므로 10회마다 전체 키를 대조해 제거한다.
  `RVP_JIRA_POLL_SEC=0` 이면 끈다.
  - `GET /jira/sync/status` — 폴러 상태·마지막 결과
  - `POST /jira/sync[?full=true][&reconcile=true]` — 주기를 기다리지 않고 즉시 동기화
  - CLI: `.venv/bin/python src/jira_sync.py [--full|--reconcile|--watch 30]`
  - 종단 실측(2026-08-01, 주기 5초): Jira 제목 수정 → KB 반영 4.5초 → API 반영 +0.2초,
    변경 이슈 1건만 재조회(`upserted:1`). 원복도 4.9초에 자동 반영.
    무변경 폴 1회 = JQL 1건 240ms(중앙) — 주기를 줄여도 부담은 거의 없다.
- **웹훅(선택)**: 서버가 공개 https URL로 노출된 경우 초 단위 반영.
  수신부 `POST /webhook/jira`는 구현돼 있고, 등록은
  `scripts/jira_webhook_register.py {list|register <공개URL>|delete <id>}`.
  `JIRA_WEBHOOK_SECRET` 설정 시 쿼리로 대조한다. 폴링과 동시 사용해도 무해하다.

### 인증(SSO) · 권한(RBAC)

역할은 둘이다 — **관리자(admin)** / **사용자(user)**. 권한은 역할이 아니라 **기능
(capability)** 단위로 검사한다: 엔드포인트가 `require("rca.approve")` 처럼 필요한
기능을 선언하고, 역할→기능 표는 `src/auth.py` 한 곳에만 둔다.

| 기능 | user | admin | 예 |
|---|:--:|:--:|---|
| `issue.read` `reco.read` `knowledge.read` | ✓ | ✓ | 이슈·추천·심층 분석·지식 현황 조회 |
| `rca.draft` `rca.read` | ✓ | ✓ | RCA 초안 → **승인 대기 큐까지만** |
| `feedback.write` | ✓ | ✓ | 추천 피드백·VOC 제출 |
| `rca.approve` | | ✓ | **Jira 실제 게시**·거부 |
| `knowledge.write` | | ✓ | 고장모드 기사·수명주기·온톨로지·부정지식 편집 |
| `config.write` | | ✓ | LLM/Jira 접속 설정 |
| `ops.sync` `ops.cache` `ops.eval` | | ✓ | 동기화·재적재·캐시·예열·평가·자기점검 |
| `voc.manage` `improve.manage` | | ✓ | VOC 열람·상태, 개선 큐 처리 |

**로그인 경로 3가지** (설정된 것만 로그인 화면에 나타난다):

- **OIDC SSO** — 인증 코드 플로우 + PKCE. 코드 교환·`id_token` 검증을 **백엔드가**
  한다(프런트에서 하면 IdP 토큰이 JS 가 읽는 곳에 남는다). 결과는 이메일만
  HttpOnly 서명 쿠키에 남기고 IdP 토큰은 저장하지 않는다.
  `RVP_OIDC_DISCOVERY_URL` `RVP_OIDC_CLIENT_ID` `RVP_OIDC_REDIRECT_URI`
  (+ `_CLIENT_SECRET` `_SCOPES` `_EMAIL_CLAIM` `_AUDIENCE` `_POST_LOGIN_URL`)
- **프록시 헤더** — 앞단 SSO 프록시가 검증한 이메일을 신뢰. `RVP_SSO_EMAIL_HEADER`
  를 **명시해야만** 켜진다(기본값을 두면 아무나 그 헤더를 보내 신원을 가로챈다).
- **개발용 로그인** — `RVP_AUTH_DEV_LOGIN=1`. IdP 없이 역할 분리를 확인하는 통로로,
  운영에서는 끈다.

**사용자 등록은 화면에서 한다** — 설정 → 사용자 관리 (관리자만). 등록·역할 변경·회수를
하면 서버가 `users.yaml` 을 원자적으로 다시 쓰고 즉시 재적용한다(재기동 불필요).
잠금 방지 규칙: 활성 관리자를 0명으로 만들 수 없고(마지막 관리자 회수·강등 거부),
자기 자신은 회수할 수 없고, `RVP_ADMIN_EMAILS` 로 지정된 관리자는 화면에서 못 고친다
(그쪽이 탈출구여야 하므로). 회수는 삭제가 아니라 `revoked: true` 로 남긴다.
목록 파일이 없는 상태에서 첫 관리자를 등록하면 그 시점에 **인증이 켜진다**.

인가 목록은 `data/users.yaml`(예시: `data/users.example.yaml`, git 미추적) 또는
`RVP_ADMIN_EMAILS`. **둘 다 없으면 인증 비활성 = 전체 권한**이고, 그 상태는 화면의
"인증 비활성" 배지와 `GET /auth/config` 로 드러난다.

**ID 는 이메일 또는 아이디**다. 사내 SSO 계정은 이메일(사내 형식 `xxx.samsung.com`,
서브도메인 포함)을 쓰고, `admin` 같은 아이디는 IdP 를 거치지 않는 로컬 운영 계정이다
— IdP 가 그 값을 이메일 클레임으로 주지 않으므로 OIDC 로는 로그인되지 않고,
개발용 로그인·프록시 헤더 경로에서 쓴다.

목록 밖 계정은 `RVP_SSO_DEFAULT_ROLE`(기본 `user`, 빈 값이면 거부)로 들어온다.
`RVP_ALLOWED_EMAIL_DOMAINS=samsung.com` 을 두면 **사내 도메인만 자동 등록**된다
(서브도메인 포함 — `sec.samsung.com` 통과, `evil-samsung.com` 차단). IdP 가 외부·게스트
계정을 인증해 주는 구성에서 필요하다.

세션: HttpOnly + SameSite=Lax 서명 쿠키. `RVP_SESSION_SECRET` 을 고정해야 재기동 후
세션이 유지된다. https 배포에서는 `RVP_COOKIE_SECURE=1`.

개발 서버는 Vite 프록시로 프런트와 API 를 **같은 오리진**으로 맞춘다
(`VITE_PROXY_TARGET`, 기본 `http://127.0.0.1:8011`) — 교차 사이트에서는 Lax 쿠키가
실리지 않기 때문이고, 프로덕션은 FastAPI 가 `web/dist` 를 같은 오리진에서 서빙한다.

검증:
- `.venv/bin/python tests/test_auth_rbac.py` (45개 — 역할별 허용·차단, 세션 위조·만료,
  401/403 구분, 프록시 헤더 신뢰 조건, 인증 비활성 폴백)
- `.venv/bin/python tests/test_user_admin.py` (34개 — 등록·역할변경·회수·복구,
  잠금 방지 4종, 회수된 계정 로그인 거부, 목록 없는 상태에서 인증 켜기)

### AI 심층 분석 캐시 · 예열

같은 이슈를 다시 열 때마다 LLM을 새로 돌리지 않는다.

- **캐시 키는 내용 주소** — 질의 이슈 내용 + 근거 사례 내용 + 모델 + 프롬프트 버전.
  무관한 이슈가 바뀌어도 캐시가 유지되고, 근거 사례의 근본원인이 수정되면 그 항목만
  자연히 재생성된다. 저장 위치 `tmp_db/llm_cache/`(git 미추적).
- **예열** — 서버 기동 3초 후와 Jira 변경 감지 후 백그라운드로 미해결 이슈의 분석을
  미리 만들어 둔다. 이미 캐시에 있으면 건너뛴다.
- 실측: 심층 분석 캐시 히트 시 첫 토큰 9.9초 → **0.01초**, `/recommend` 0.65초 → 0.00초.
- 화면에 "저장된 분석 재사용" 배지와 "다시 생성"(캐시 무시) 버튼이 있다.
- `GET /explain/cache` 현황 · `POST /explain/prewarm` 수동 예열 · `DELETE /explain/cache` 비우기.
- 프롬프트 문구를 바꾸면 `src/llm_cache.py` 의 `PROMPT_VERSION` 을 올린다(안 올리면 옛 형식이 계속 나간다).

환경변수: `RVP_PREWARM`(0=끔) · `RVP_PREWARM_LIMIT`(기본 20) ·
`RVP_PREWARM_GAP_SEC`(기본 1.0) · `RVP_LLM_CACHE_TTL`(0=무기한)

## 구조

```
src/
  ingest.py             1) Jira 적재
  jira_sync.py          Jira 폴링 증분 동기화 (변경분만 재적재 + 삭제 대조)
  auth.py               역할→기능 표 + 인가 목록 (관리자/사용자)
  oidc_sso.py           OIDC 인증 코드 + PKCE (백엔드 코드 교환·id_token 검증)
  session.py            HttpOnly 서명 세션 쿠키
  user_store.py         인가 목록 쓰기 (등록·역할변경·회수 + 잠금 방지)
  llm_cache.py          LLM 생성물 콘텐츠 주소 캐시
  preprocess.py         2) 전처리 + 엔티티/그래프 (엔티티 패턴 단일 소스)
  explorer.py           3) 탐색/검색/시각화
  recommender.py        해결책 추천기 (graph/bm25/hybrid/embed)
  eval_recommender.py   P@1/P@3/MRR 평가 하네스
  jira_commenter.py     Jira 댓글 조회/게시 (사람 검토 승인 후 사용)
  agent.py, retrievers.py, lang_validator.py, ...  (평가/실험용 유틸)
scripts/
  run_pipeline.py       ingest→preprocess→explorer 오케스트레이션
  jira_webhook_register.py  Jira 웹훅 등록/목록/해제 (공개 URL 필요, 폴링이 기본)
  jira_seed.py          가짜 고장 이슈 Jira 시드 생성기 (--set lsi|nfc|nfc2)
  lsi_failure_data.py   칩 11라인 × (LSI 24종 + NFC Forum 프로토콜 14종) 고장 시나리오
                        NFC 배치: NCI 2.3/Digital 2.4/LLCP 1.4/SNEP/Type 2·3·4·5 Tag/
                        TNEP/WLC 2.0/Smart Poster RTD/NFC Auth Protocol/Connection Handover
                        (https://nfc-forum.org/build/specifications 참조)
backend/server.py       FastAPI (/recommend, /issues/unresolved, /chat, ...)
web/                    Vite + React + TS + Tailwind
```

## 설정 (.env)

```
JIRA_BASE_URL=...        JIRA_PROJECT_KEY=LSI
JIRA_EMAIL=...           JIRA_API_TOKEN=...      # 또는 JIRA_PAT
RVP_SESSION_SECRET=...   RVP_ADMIN_EMAILS=...    # 인증·권한 (위 절 참조)
OPENROUTER_API_KEY=...   OPENROUTER_MODEL=...    # LLM 엔진=agno(OpenRouter)
```

## Stack

Python 3.11 · networkx · rank-bm25 · fastembed(옵션) · FastAPI · Agno · OpenRouter ·
React + Vite + TypeScript + Tailwind · vis-network
