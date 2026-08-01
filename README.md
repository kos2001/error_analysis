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

- **폴링(기본)**: 서버가 `RVP_JIRA_POLL_SEC`(기본 10초, 평균 반영 지연 5초) 주기로 "마지막 동기화 이후
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

## 구조

```
src/
  ingest.py             1) Jira 적재
  jira_sync.py          Jira 폴링 증분 동기화 (변경분만 재적재 + 삭제 대조)
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
OPENROUTER_API_KEY=...   OPENROUTER_MODEL=...    # LLM 엔진=agno(OpenRouter)
```

## Stack

Python 3.11 · networkx · rank-bm25 · fastembed(옵션) · FastAPI · Agno · OpenRouter ·
React + Vite + TypeScript + Tailwind · vis-network
