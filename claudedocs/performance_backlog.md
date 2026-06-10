# 성능 개선 백로그 — 추천 품질 · 응답 속도 · 운영

> 2026-06-10 코드 분석 기준. 우선순위: P1(품질에 직접 영향) > P2(체감 속도) > P3(운영).

## A. 추천 품질 (정확도)

1. **[P1] 무관 매치 차단용 최소 점수 게이트가 없음**
   BM25는 모든 KB 문서에 점수를 주므로 `coverage`가 사실상 항상 true.
   완전히 새로운 고장 유형을 질의해도 4건이 "유사 사례"로 표시된다.
   → RRF 점수 또는 BM25 원점수에 임계값을 두고, 미달 시 "유사 사례 없음 — 시니어 검토" 경로로 보낸다.
   (RCA 댓글 자동화의 coverage 게이트와 동일 로직 공유)

2. **[P1] P@1=1.0은 합성 데이터 착시 — 실전형 평가셋 필요**
   현재 평가(LOO)는 같은 템플릿 20종의 표현 변형이라 BM25만으로 만점.
   → ① 요약을 paraphrase한 hold-out 세트 ② 템플릿 자체를 빼고 평가(unseen failure class)
   ③ 신규 실이슈 유입 시 평가셋 자동 갱신. `eval_recommender.py` 확장.

3. **[P2] 표현이 다른 동일 고장(paraphrase)에 BM25 취약 → 임베딩 랭커 기본화 검토**
   `hybrid_embed`(fastembed 다국어 MiniLM)가 이미 구현돼 있으나 기본은 미사용.
   → 2번의 paraphrase 평가셋에서 `hybrid` vs `hybrid_embed` 비교 후 승자 채택.
   채택 시 KB 임베딩을 `tmp_db/`에 사전 계산·캐시(서버 기동 시 재계산 방지).

4. **[P2] 한국어 토크나이저가 단순 정규식** (`[A-Za-z0-9가-힣]+`)
   조사가 붙은 단어("전원차단을"≠"전원차단")가 다른 토큰이 된다.
   → kiwipiepy 등 형태소 분석 도입 또는 음절 bi-gram 보조 인덱스. 2번 평가셋으로 효과 측정 후 결정.

5. **[P1] LLM 설명의 인용 검증 게이트 부재 (UI `explain` 경로)**
   생성문이 매치에 없는 이슈 키를 인용(환각)해도 그대로 표시된다.
   → `LSI-\d+` 추출 ⊆ 매치 키 검증, 위반 시 재생성 1회 → 템플릿 fallback.
   (RCA 댓글 설계의 게이트 3을 `_llm_explain`에 공통 적용)

6. **[P3] 설명 텍스트 품질 자동 평가 없음** — 검색(P@k)만 측정 중.
   → agent-as-judge(이미 agno에 있음)로 설명의 근거 충실도/실행 가능성 채점 하네스 추가.

## B. 응답 속도 (latency)

7. **[P2] `/chat`이 요청마다 `build_agent()` 재생성** — agno Agent + SqliteDb + MemoryManager를
   매 요청 새로 만든다. → `(user_id, session_id)` 키 LRU 캐시.

8. **[P2] LLM 종합 분석(explain)이 동기 블로킹** — hermes 엔진 기준 수십 초.
   → ① SSE 스트리밍(OpenRouter는 `stream:true`, hermes는 stdout 라인 단위 중계)
   ② 또는 백그라운드 작업 + 프론트 폴링. 매치 결과는 즉시, 설명은 도착 시 갱신(현재 UI 구조가 이미 2단계라 백엔드만 바꾸면 됨).

9. **[P3] hermes 프로세스 기동 오버헤드** — 호출마다 CLI 부팅(~수 초).
   → `hermes proxy`(OpenAI 호환 로컬 서버)에 Nous Portal 로그인 후 HTTP로 전환하면
   프로세스 기동 제거 + 진짜 토큰 스트리밍 확보. (현재 proxy upstream 미로그인 상태)

10. **[P3] GraphRetriever가 agent.py와 hermes_engine.py에 각각 로드** — 중복 메모리/초기화.
    → 단일 모듈 레벨 싱글톤으로 공유.

## C. 운영 (데이터 신선도·인입 속도)

11. **[P2] ingest가 이슈당 GET 2회 직렬** (상세 + 댓글, 200건 ≈ 400 요청).
    → `fields=...,comment`로 댓글을 상세 응답에 포함시켜 요청 절반,
    `ThreadPoolExecutor(8)`로 병렬화. 200건 기준 수 분 → 수십 초.

12. **[P3] KB가 수동 갱신** (`data/all_raw_issues.json` 스냅숏).
    → 주기 ingest(cron) + `_reco_state` 무효화 엔드포인트(`POST /reco/reload`).

## 권장 착수 순서

1, 5 (게이트 2종 — RCA 댓글 자동화와 공유, 신뢰성 직결)
→ 2 (평가셋: 이후 모든 개선의 측정 기반)
→ 3, 4 (평가셋 위에서 A/B)
→ 7, 8 (체감 속도)
→ 11, 12 → 6, 9, 10
