# 지식자산화 관점 보강 항목 — 갭 분석

> 2026-06-14 코드/데이터 분석 기준. 렌즈: **검색 정확도(이미 performance_backlog.md가 다룸)가 아니라,
> 조직의 엔지니어링 지식을 "지속·신뢰·재사용 가능한 자산"으로 축적·거버넌스하는 관점.**
> 우선순위: P1(자산의 존속·신뢰 자체를 위협) > P2(자산의 성숙·재사용성) > P3(운영·확산).

## 현재 축적되는 자산 (있는 것)

| 자산 | 위치 | 비고 |
|---|---|---|
| 해결 이슈 KB(구조화) | `tmp_db/resolved_issues.json` | chip/category/symptom/debug_approach/root_cause/resolution/workaround/verified/entities |
| 엔티티↔이슈 지식 그래프 | `tmp_db/lsi_graph.pkl/.graphml` | networkx bipartite |
| KB 임베딩 캐시 | `tmp_db/kb_emb_*.npz` | 다국어 MiniLM |
| 사람 검증·수정 RCA(환류) | `tmp_db/rca_feedback.json` | 승인 시 기록 → `kb_records()`로 추천기 KB에 주입(verified 가중) |
| 승인 대기 큐 | `tmp_db/rca_pending.json` | HITL |
| 검증 플래그 | KB record `verified` | "✅ 해결 및 검증" 마커 2종 존재 시 |

## 보강 필요 항목

### P1 — 자산의 존속·신뢰 자체를 위협 (최우선)

**1. 큐레이션 지식의 비영속성·비공유**
- 근거: `git check-ignore` → `tmp_db`, `data/raw_issues.json` 모두 gitignore. 코드(`src/rca_feedback.py`)만 추적되고 **데이터는 미추적**.
- 문제: 가장 가치 높은 "사람이 검증·수정한 RCA"(rca_feedback.json)가 **단일 개발 머신에만, 백업·버전·공유 없이** 존재. 머신 분실 = 자산 소실. 여러 엔지니어가 함께 축적할 수 없음.
- 보강: ① 영속 저장소(공유 DB/객체스토리지)로 이전 ② **Jira 환류** — 승인된 RCA를 원본 이슈 댓글뿐 아니라 전용 KB 스페이스(또는 별도 이슈 타입 "Known-Issue")에 정본으로 기록해 SoT로 승격 ③ 정기 백업·스냅숏 버전.

**2. 인입 지식 품질 게이트 부재 (무음 추출 실패)**
- 근거: `tmp_db/resolved_issues.json` 92건 전부 `category=''`·`root_cause=''`·`verified=0`로 파싱됨(마커 불일치 스냅숏이 조용히 존재).
- 문제: `parse_issue`의 마커(`근본 원인 (Root Cause)` 등)가 본문 형식과 어긋나면 **경보 없이 빈 레코드**가 KB에 적재됨. 자산 품질이 Jira 필드 위생에 무방비로 종속.
- 보강: ingest/preprocess에 **필드 충족률 검증 + 결측 리포트**(예: root_cause 결측률 > N% 시 차단/경고), 레코드 스키마 assertion, 추출 실패 키 목록 출력.

**3. 추천 "유용성/결과" 피드백 부재**
- 근거: `/rca/feedback`은 GET(통계)뿐. rca_feedback은 *초안 텍스트 수정*만 저장 — 정작 "추천된 과거 사례/제안이 실제로 맞았나·도움됐나"는 미수집.
- 문제: 자산이 **적합도를 학습할 신호가 없고, ROI(시간 절감·정답률)를 증명 못 함.** 추천 랭킹·게이트가 실사용 결과로 개선되지 않음.
- 보강: 매치/제안 카드에 `도움됨/아님 · 실제 근본원인=어느 사례` 라벨 수집 → ① 랭킹 학습(rerank 가중) ② 실전형 평가셋 자동 확장(performance_backlog 2번과 연결) ③ "절감 추정" 집계.

### P2 — 자산의 성숙·재사용성

**4. 사례 → 정규 "고장모드/Known-Issue" 기사 계층 부재**
- 동일 근본 고장의 중복 티켓이 평면 레코드로만 존재. 성숙한 지식자산은 *incident → 큐레이션 기사*로 승격됨.
- 보강: 임베딩 클러스터링으로 고장모드 후보 군집 → 사람이 정규 기사로 승격하고 사례들을 `instances-of`로 링크. 추천은 기사 단위로 묶어 노출.

**5. 지식 신선도·폐기 수명주기 부재**
- FW/칩이 진화하는데 사례에 유효기간·대체(`superseded-by`)·신뢰 감쇠가 없음 → 오래되어 무효일 수 있는 근본원인이 최신과 동급 추천.
- 보강: `fw_version`/`created` 기반 신선도 가중, 폐기·대체 링크, UI에 "이 사례는 FW X·YYYY 기준" 경고.

**6. 분류·엔티티 거버넌스(온톨로지) 부재**
- `category`는 자유 Jira 필드(폴백 `기타`), 엔티티는 정규식·정규화 없음(`PM9C3` vs `PM9C3-NVMe` 동의어 미통합).
- 보강: 통제 어휘/동의어 사전, 엔티티 정규화 단계, 신규 라벨 검토 큐. (performance_backlog 4번 토크나이저와 시너지)

**7. 디버깅 서사·부정 지식(실패한 가설) 미포착**
- 자산이 Jira 필드 위생에 종속, 시니어의 추론 경로와 "시도했지만 아니었던 것"이 얇음. 고가치 부정지식 누락.
- 보강: 초안 생성/승인 폼에 `검토한 가설 · 기각 사유` 구조 필드 추가 → KB record에 적재.

### P3 — 운영·확산

**8. 지식 공백 관측성 부재**
- coverage 게이트 미달("유사 사례 없음") 이벤트가 로깅·집계되지 않음 → 어느 고장군이 얇은지 안 보여 문서화 우선순위 근거 없음.
- 보강: 게이트 미스·저신뢰 질의 로깅 → "지식 공백 대시보드"(가장 자주 질의되나 사례 없는 영역).

**9. 전문가·저자·소유권 메타데이터 부재**
- 누가 작성·검증했는지 없음 → find-the-expert, 저자 신뢰 가중, 책임성 불가.
- 보강: record에 author/validator 메타 + 신뢰 가중.

**10. 상호운용성·내보내기 부재**
- 지식이 본 도구에 갇힘(Confluence/위키/외부 API 없음).
- 보강: 구조화 기사 export(Markdown/JSON) + 읽기 API.

## 구현 현황 (2026-06-14 갱신)

| 항목 | 상태 | 핵심 산출 |
|---|---|---|
| P1-1 영속화·Jira 환류 | ✅ | `knowledge_store.py`, `data/knowledge_store.json`, rebuild-from-jira |
| P1-2 인입 품질 게이트 | ✅ | `quality_gate.py`, `/knowledge/quality`, strict 차단 |
| P1-3 추천 유용성 피드백 | ✅ | `reco_feedback.py`, 매치 카드 👍/👎, eval_pairs·ROI |
| P2-4 고장모드 기사 | ✅ | `failure_modes.py`, 클러스터링·승격·매치 주석 |
| P2-5 신선도·폐기 | ✅ | `lifecycle.py`, freshness·deprecate·강등 |
| P2-6 온톨로지 거버넌스 | ✅ | `ontology.py`, 동의어 정규화·검토 큐 |
| P2-7 부정지식 | ✅ | `negative_knowledge.py`, 기각 가설 프롬프트 주입 |
| P3-8 지식공백 관측성 | ✅ | `knowledge_gaps.py`, `/knowledge/gaps` |
| P3-9 저자·소유권 | ✅ | `ownership.py`, find-the-expert |
| P3-10 export·상호운용 | ✅ | `knowledge_export.py`, json/markdown |
| **자기 개선 loop L1** | ✅ | `self_improve.py`, `/selfcheck` (측정·진단·제안, 무변경) |
| 자기 개선 loop L2/L3 | ⏳ | 파라미터 자동튜닝+회귀게이트 / 지식변경 HITL 제안 |

## 권장 착수 순서

1. **P1-1 영속화·Jira 환류** — 자산 소실 리스크 제거가 가장 시급(다른 모든 항목의 토대).
2. **P1-2 인입 품질 게이트** — 적은 코드로 무음 오염 차단, 즉시 신뢰도 향상.
3. **P1-3 유용성 피드백** — 학습·평가·ROI의 입력. UI 한 줄 + 저장 엔드포인트로 시작.
4. 이후 P2-4(고장모드 기사) → P2-5(신선도) → 나머지.
