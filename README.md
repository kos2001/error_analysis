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
- `고장 분석 추천` 탭: 미해결 이슈 → 유사 해결 사례 + 제안 root-cause/해결책 (+ LLM 종합)
- `지원 챗봇` 탭: Agno + OpenRouter + graph RAG 고객 지원 에이전트(원형)

## 구조

```
src/
  ingest.py             1) Jira 적재
  preprocess.py         2) 전처리 + 엔티티/그래프 (엔티티 패턴 단일 소스)
  explorer.py           3) 탐색/검색/시각화
  recommender.py        해결책 추천기 (graph/bm25/hybrid/embed)
  eval_recommender.py   P@1/P@3/MRR 평가 하네스
  agent.py, retrievers.py, sql_db.py, lang_validator.py, ...  (지원 챗봇 원형)
scripts/
  run_pipeline.py       ingest→preprocess→explorer 오케스트레이션
  jira_seed.py          가짜 고장 이슈 Jira 시드 생성기
  lsi_failure_data.py   칩 10라인 × 24 고장 시나리오 데이터
backend/server.py       FastAPI (/recommend, /issues/unresolved, /chat, ...)
web/                    Vite + React + TS + Tailwind
```

## 설정 (.env)

```
JIRA_BASE_URL=...        JIRA_PROJECT_KEY=LSI
JIRA_EMAIL=...           JIRA_API_TOKEN=...      # 또는 JIRA_PAT
OPENROUTER_API_KEY=...   OPENROUTER_MODEL=...
```

## Stack

Python 3.11 · networkx · rank-bm25 · fastembed(옵션) · FastAPI · Agno · OpenRouter ·
React + Vite + TypeScript + Tailwind · vis-network
