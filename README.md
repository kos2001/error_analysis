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

### LLM 설명 엔진 선택 (RVP_ENGINE)

`/recommend?explain=true` 의 종합 설명 생성 엔진을 `.env`로 선택:

- `RVP_ENGINE=agno` (기본): OpenRouter API 직접 호출
- `RVP_ENGINE=hermes`: 로컬 Hermes Agent CLI 서브프로세스 (`hermes chat -q ... -Q --cli`).
  `HERMES_MODEL`/`HERMES_BIN`/`HERMES_TIMEOUT`/`HERMES_TOOLSETS` 로 오버라이드.

## 구조

```
src/
  ingest.py             1) Jira 적재
  preprocess.py         2) 전처리 + 엔티티/그래프 (엔티티 패턴 단일 소스)
  explorer.py           3) 탐색/검색/시각화
  recommender.py        해결책 추천기 (graph/bm25/hybrid/embed)
  eval_recommender.py   P@1/P@3/MRR 평가 하네스
  hermes_engine.py      Hermes Agent CLI 엔진 (LLM 설명 생성 대체 백엔드)
  jira_commenter.py     Jira 댓글 조회/게시 (사람 검토 승인 후 사용)
  agent.py, retrievers.py, lang_validator.py, ...  (평가/실험용 유틸)
scripts/
  run_pipeline.py       ingest→preprocess→explorer 오케스트레이션
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
OPENROUTER_API_KEY=...   OPENROUTER_MODEL=...
RVP_ENGINE=hermes        # 채팅 엔진: agno(기본) | hermes
```

## Stack

Python 3.11 · networkx · rank-bm25 · fastembed(옵션) · FastAPI · Agno · OpenRouter ·
React + Vite + TypeScript + Tailwind · vis-network
