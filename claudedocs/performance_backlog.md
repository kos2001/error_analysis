# 성능 개선 백로그 — 추천 품질 · 응답 속도 · 운영

> 2026-06-10 코드 분석 기준. 우선순위: P1(품질에 직접 영향) > P2(체감 속도) > P3(운영).
> 2026-07-02 일괄 갱신: 재순위 기본 활성(paraphrase P@1 .898→1.0), 평가셋 4종 하네스,
> ingest 병렬화(39s→3.2s), 질의 임베딩 중복 제거(10ms→6ms). 상세는 각 항목 상태 참조.
> 2026-08-01 실측 프로파일링 후 D절(신규 항목) 추가. 항목 1·5는 구현 확인되어 상태 정정.

## 실측 기준선 (2026-08-01, 운영 설정 = openrouter bge-m3 임베딩 + cohere rerank)

| 구간 | 실측 | 비고 |
|---|---|---|
| `/recommend` 전체 (rerank ON) | **평균 728ms** (중앙 745 / 최대 858) | KB 137건, k=4, n=8 |
| └ 질의 임베딩 API 1회 | **429ms** | 지배적 비용 |
| └ rerank API 1회 | **+410ms** | rerank OFF 시 318ms |
| └ BM25 채점 + graph | 1.7ms | 무시 가능 |
| Recommender 빌드 (임베딩 캐시 히트) | 15ms | |
| Recommender 빌드 (**캐시 미스**) | **4,787ms** | KB 1건만 바뀌어도 137건 전량 재임베딩 |

측정 스크립트는 일회성(scratchpad)이었음 — D-13에서 상설 계측으로 승격 필요.

## A. 추천 품질 (정확도)

1. **[부분 완료 — 운영 설정에서 미동작(D-9 참조)]** 최소 점수 게이트
   rerank 게이트(0.20)·embed_cos 게이트(0.48) 모두 구현됨. 다만 운영 임베딩 백엔드
   (`RVP_EMBED_BACKEND=openrouter`)에서는 embed_cos 게이트가 실제로 꺼져 있다 — D-9.

2. **[완료 2026-07-02] 실전형 평가셋 — 4종 운용**
   `eval_recommender.py --sets hard,generated,real` + `--paraphrase`.
   paraphrase(사람 49) · generated(LLM 재서술 24) · hard(증상만 64) · real(실신호, 성장형).
   hard/LOO는 현 KB에서 포화(P@1 1.0) — 변별은 paraphrase/generated가 담당.

3. **[완료] 임베딩 랭커 기본화 + 재순위 기본 활성 (2026-07-02)**
   `hybrid_embed`가 서버 기본. KB 임베딩 `tmp_db/kb_emb_*.npz` 캐시.
   재순위(cohere/rerank-v3.5)도 기본 활성으로 승격 — 재검증: paraphrase P@1 .898→**1.0**,
   generated .958→**1.0**, 게이트 통과/무관 차단 모두 1.0. 평균 0.38s/질의.
   안전장치: 타임아웃 10s + 연속 3회 실패 시 자동 비활성(circuit breaker) → 미지원
   게이트웨이에서 질의당 0.01s로 1차 순위+embed_cos 게이트 폴백(실측). RVP_RERANK=0 으로 옵트아웃.

4. **[보류 — 측정 결과 무효] 한국어 형태소/토크나이저 고도화**
   paraphrase 실패 5건을 분석(2026-07-02): 조사 문제가 아니라 완전 재서술(어휘 갭)
   — "추운 곳에서 기기를 켜면" vs "저온 부팅 시 UFS link startup 실패". 형태소로 해결 불가,
   재순위(3번)가 해소. RRF 가중치 그리드(6조합)도 현행 2.0/1.5/0.5가 최적으로 확인.

5. **[완료 — 확인 2026-08-01] LLM 설명의 인용 검증 게이트**
   `_llm_explain`은 agno `output_schema`(RcaExplanation)의 `cited_keys`를 매치 키와
   대조해 환각 키를 `dropped`로 분리(server.py:497). 스트리밍 경로도 완료 이벤트에서
   `LSI-\d+` ∩ 매치 키로 검증(server.py:660). 미구현은 "위반 시 재생성 1회"뿐 —
   현재는 조용히 인용만 탈락시킨다(본문에는 남음). → D-13.

6. **[P3] 설명 텍스트 품질 자동 평가 없음** — 검색(P@k)만 측정 중.
   → agent-as-judge(이미 agno에 있음)로 설명의 근거 충실도/실행 가능성 채점 하네스 추가.

## B. 응답 속도 (latency)

7. ~~[P2] `/chat`이 요청마다 `build_agent()` 재생성~~ — **무효(2026-07-02)**: 서버에
   `/chat` 엔드포인트 없음(agent.py는 데모 CLI). 해당 시 재검토.

7b. **[완료 2026-07-02] 질의 임베딩 중복 계산 제거** — `recommend()`가 rank()와
    신호 산출에서 같은 질의를 두 번 임베딩. 직전 질의 캐시로 제거 → 질의당 10ms→6ms
    (로컬 fastembed), openrouter 임베딩 백엔드는 질의당 API 1회 절감. 품질 불변.

8. **[완료] LLM 종합 분석(explain) SSE 스트리밍** — OpenRouter `stream:true` 직접 호출
   (`_llm_stream`)로 토큰 단위 중계 구현됨. 매치 즉시 + 설명 스트리밍.

9. ~~[P3] hermes 프로세스 기동 오버헤드~~ — **무효**: 엔진을 agno(OpenRouter HTTP)
   단일로 결정해 hermes CLI/proxy 미사용.

10. **[해소 확인 2026-07-02] GraphRetriever 중복 로드** — agent.py가 모듈 레벨
    `_GRAPH` 싱글톤을 이미 사용. 나머지 사용처는 오프라인 스크립트.

## C. 운영 (데이터 신선도·인입 속도)

11. **[완료 2026-07-02] ingest 요청 절반 + 병렬화** — `fields=...,comment`로 이슈당
    요청 2회→1회(잘림 시에만 보충), `ThreadPoolExecutor(8)`.
    실측: 완료 137건 직렬 19.5s(구방식 ≈39s) → **3.2s**, 전체 264건 5.4s.

12. **[부분 완료] KB 갱신** — Jira 웹훅 증분 재적재(커밋 e101ceb) + 병렬 ingest로
    전체 재적재도 수 초. 주기 cron은 self_improve에서 일일 수행.

## D. 신규 (2026-08-01 실측 기반)

9. **[완료 2026-08-01] 운영 임베딩 백엔드에서 embed_cos 게이트가 꺼져 있었음**
   수정 3건: (a) 신호 산출 조건 `_embedder is not None` → `_kb_emb is not None`,
   (b) 게이트 임계 모델별 보정(`_GATE_COS_BY_MODEL`, bge-m3 = 0.57),
   (c) 평가 하네스가 서버와 같은 `RVP_EMBED_*` 환경변수를 따르도록 — 하네스가 로컬
   fastembed만 평가해 이 결함을 놓쳤다. 부수: openrouter 임베딩 429/5xx 백오프 재시도.
   **재검증(운영 설정 openrouter+bge-m3)**: rerank OFF(폴백 경로) 무관 차단
   **0.0 → 1.0**, gate_pass 1.0 유지, paraphrase P@1 0.918 불변.
   rerank ON(기본 경로) 전 지표 1.0 — 회귀 없음.
   bge-m3 임계 마진은 ±0.007(정답 최소 0.576 / 무관 최대 0.563)로 좁다 — 모델이나
   KB가 바뀌면 재보정 필요.

   <details><summary>원래 증상</summary>
   `_init_embed`가 openrouter 백엔드에서 `self._embedder = None`을 두는데(임베딩은
   `_openrouter_embed`가 직접 수행), `recommend()`의 신호 산출 조건이
   `self.signals and self._embedder is not None`(recommender.py:320)이라 신호 자체가
   생략된다. 결과: `embed_cos`/`bm25_raw` 미표시, `gate=None`, **coverage 무조건 True**.
   재현(2026-08-01): 무관 질의("사내 카페테리아 점심 메뉴 장애") → rerank 미사용 시
   coverage=True, gate=None. rerank 서킷브레이커가 문서화한 "1차 순위 + embed_cos
   게이트 폴백"이 실제로는 존재하지 않는다.
   </details>

10. **[완료 2026-08-01] LLM 생성물 캐시 + 예열, `/recommend` 재계산 제거**
    (a) 심층 분석 콘텐츠 주소 캐시(`src/llm_cache.py`) — 키 = 질의 이슈 내용 +
        근거 사례 내용 + 모델 + 프롬프트 버전. 전역 KB 버전을 쓰지 않으므로 무관한
        이슈 변경으로 캐시가 통째로 날아가지 않고, 근거의 근본원인이 수정되면 그
        항목만 자연히 재생성된다. 키 판정 8케이스 검증 통과.
    (b) `/recommend` 결과 캐시 — explain 스트리밍이 같은 검색을 다시 하지 않는다.
    (c) 예열(prewarm) — 기동 3초 후·KB 변경 후 백그라운드로 미해결 이슈의 분석을
        미리 생성. 이미 캐시에 있으면 건너뛴다. `RVP_PREWARM`/`_LIMIT`/`_GAP_SEC`.
    **실측**: 심층 분석 첫 토큰 9.9s → **0.01s**, 완료 33~59s → **0.01s**(캐시 히트).
    `/recommend` 0.65s → **0.00s**. UI에 "저장된 분석 재사용" 배지 + "다시 생성".

11. **[P1] KB 쓰기 1건마다 전체 재임베딩 4.8초 — 다음 질의가 그대로 뒤집어씀**
    `_RECO_STATE.clear()`가 RCA 승인·온톨로지 동의어·lifecycle·웹훅 등 9곳에서 호출되고
    (server.py:156/255/968/1075/1113/1174/1303/1315/1356/1369), 문서가 1건만 바뀌어도
    `kb_emb_*.npz` 다이제스트가 달라져 137건 전량을 API로 재임베딩한다(실측 4,787ms).
    빌드에 락도 없어 동시 요청 시 중복 빌드(thundering herd).
    → (a) 문서 단위 임베딩 캐시(내용 해시 → 벡터)로 변경분만 재계산 → ~35ms,
      (b) 무효화 시 즉시 폐기 대신 백그라운드 재빌드 + 구 상태 계속 서빙,
      (c) `_reco_state()`에 빌드 락, (d) 서버 기동 시 워밍업(첫 질의가 4.2s 임포트 부담).
    부수: `tmp_db/kb_emb_*.npz`가 19개 6.8MB 누적 — 세대 정리 필요.

12. **[P2] 질의 임베딩 429ms — 로컬 백엔드 A/B**
    `/recommend` 728ms 중 429ms가 질의 임베딩 API 1회. 같은 모델(bge-m3)을 로컬
    fastembed로 돌리면 네트워크 왕복이 사라진다(백로그 7b 실측 로컬 MiniLM 6ms).
    동일 모델이므로 품질 동일해야 하나, KB 임베딩도 같은 백엔드로 재생성 필요.
    → paraphrase/generated 셋으로 품질 동치 확인 후 채택. 기대 **-400ms/질의**.
    주의: 현 개발 환경의 fastembed MiniLM ONNX 캐시가 깨져 있어(로드 실패 → hybrid
    무언 폴백) 먼저 복구해야 측정 가능.

13. **[P2] 인용 위반 시 재생성 경로 없음** — 항목 5의 잔여.
    현재는 환각 키를 `dropped`로 떼어낼 뿐 본문의 잘못된 인용 문장은 남는다.
    → 검증 실패 시 1회 재생성, 그래도 실패면 템플릿 fallback.

14. **[P3] 서빙 지연 상설 계측 없음** — 이번 수치는 일회성 스크립트로 얻었다.
    측정→A/B→채택 컨벤션을 유지하려면 단계별(1차 랭킹/임베딩/rerank/LLM) 타이밍을
    응답 메타 또는 `/metrics`에 남겨야 회귀를 잡을 수 있다.

15. **[P3] `recommend()` 내 중복 연산(잔여)** — `_query_text`·`query_entities`·
    `tokenize`·BM25 `get_scores`가 `rank()`와 신호 산출에서 각각 2회 실행된다
    (7b가 임베딩만 캐시). 실측 합계 ~2ms로 **현 KB 규모에선 무의미** — KB가 수천 건이
    되거나 BM25가 프로파일에 뜰 때만 착수.

## 권장 착수 순서

D-9(완료) → D-10(완료) → **D-11**(증분 임베딩 + 백그라운드 재빌드 + 워밍업 —
KB 쓰기마다 4.8초, 폴러가 5초 주기로 도는 지금 우선도가 올라갔다)
→ **D-12**(로컬 임베딩 A/B, -400ms) → D-14(상설 계측) → D-13 → 6 → 15(보류)
