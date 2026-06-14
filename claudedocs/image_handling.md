# 이미지 처리 설계 (image handling)

> 2026-06-14 작성. 상태: **설계만(미구현)** — 실데이터에 이미지 첨부가 있는지 확인 후 착수.
> 현재 파이프라인은 100% 텍스트 전용(ingest가 첨부를 가져오지 않음, VLM/OCR 없음).

## 1. 동기

LSI 불량 분석에서 이미지는 종종 **텍스트에 없는 진단 신호**를 담는다:
오실로스코프 파형·eye diagram, SEM/현미경 die 사진, 디스플레이 아티팩트
(flicker/banding/ghosting) 스크린샷, 열화상, 레지스터 덤프·로그 스크린샷, 회로도.
현재는 이 신호를 전부 버리고 있다.

**현황(코드 확인):**
- `ingest.py`는 `summary,description,labels,priority,components,status,created` +
  댓글 본문(`comment.body`)만 가져온다. **첨부(attachment) 미수집.**
- 비전/VLM/OCR 코드 없음(`agent.py` 등의 "Robot Vision Platform"은 무관한 레거시 이름).
- 합성 시드에 이미지 없음 → 실데이터 전까지 검증 불가.

## 2. 핵심 원칙: 이미지를 "텍스트로 변환"해 기존 인프라 재사용

최대 레버리지는 **VLM 캡션 / OCR로 이미지를 텍스트화한 뒤, 그 텍스트를
`parse_issue`의 `context_text`에 합류시키는 것**이다. 그러면 임베딩·BM25·추천·RCA·
자기 개선 loop가 **리트리벌 재작업 없이** 이미지 신호를 활용한다.

```
Jira 첨부 ──(다운로드)──> 이미지
                          ├─ 일반 이미지(파형/사진/아티팩트) ──> VLM 캡션(한국어)
                          └─ 텍스트성(로그/레지스터 스크린샷) ──> OCR
                                                                   │
                          캡션/OCR 텍스트 ──> context_text ──> 기존 임베딩/BM25/추천/RCA
                          원본 이미지 ──> 참조(URL)만 저장, UI 썸네일/증거
```

## 3. 단계별 계획 (ROI 순)

### Phase 1 — 첨부 인입 + 증거/출처 (저비용·고확실성, ML 없음) ← 먼저
- `ingest.fetch_issues`에 `attachment` 필드 추가(`fields=...,attachment`), 댓글 첨부도.
- 레코드에 첨부 메타 저장: `{filename, url, mime, size, thumbnail, source(issue|comment)}`.
  **이미지 바이트는 저장 금지** — 참조(URL)만. 원본은 Jira/객체스토리지.
- UI 매치 카드·이슈 상세에 썸네일·링크 노출 → 엔지니어가 실제 파형/사진 확인.
- 검색엔 미사용(근거 보강만). 위험 0.

### Phase 2 — VLM 캡션 / OCR 텍스트화 (고레버리지)
- ingest(또는 별도 enrich 단계)에서 이미지당 1회 비전 모델 호출:
  - 일반 이미지 → OpenRouter 비전 모델로 한국어 설명 생성.
  - 로그·레지스터 스크린샷 → **OCR**(정확한 텍스트가 캡션보다 유용) — 분기.
  - **이미지 해시로 캐시**(재인입 시 재호출 방지, `kb_emb` 캐시와 동형).
- 캡션/OCR 텍스트를 `context_text`에 합류 → 검색·RCA 자동 활용.
- **HITL 필수**: 일반 VLM은 SEM·eye diagram 등 도메인 이미지에서 자신 있게 틀린다.
  캡션을 "AI 생성·미검증"으로 표시하고 사람이 정정 → P1-3 `rca_feedback`과 동일한
  환류. 품질 게이트(P1-2)에 "캡션 커버리지·신뢰도" 확장.

### Phase 3 — 선택(현시점 과투자)
- 이미지↔이미지 시각 유사도: CLIP류 임베딩 별도 인덱스("비슷한 파형의 과거 이슈").
  이미지가 충분히 쌓이기 전엔 ROI 낮음.
- 도메인 CV: eye 마진 측정, banding 검출 등 — 고비용·특화, overkill.

## 4. 정직한 함정

1. **VLM 환각**: 스코프/SEM은 범용 VLM이 가장 약한 영역. 텍스트 RCA보다 검증이 더
   중요 — HITL 없이 캡션을 KB에 넣으면 오염.
2. **고객 데이터 민감성**: 고객 이미지를 외부 API(OpenRouter)로 전송하는 것이 계약상
   금지될 수 있음 → **VLM 비활성 플래그 / 로컬 비전 모델** 옵션 필요
   (텍스트 엔진 `RVP_ENGINE=agno|hermes` 선택과 동형 패턴).
3. **저장 비용**: KB JSON에 이미지 바이트 금지 — 참조 + 캡션만.
4. **합성 데이터 한계**: 현재 시드엔 이미지 없음 → 실 Jira 첨부 유무 확인이 선행.

## 5. 기존 자산과의 연결

| 신규 | 재사용/연결 |
|---|---|
| 캡션/OCR 텍스트 | `parse_issue.context_text` → 임베딩·BM25·추천·RCA 그대로 |
| 캡션 정정 | `rca_feedback`류 환류 → 자기 개선 loop가 "캡션 품질" 신호로 측정 |
| VLM on/off · 모델 선택 | `app_config` 플래그(텍스트 엔진 선택과 동일 패턴) |
| 캡션 캐시 | 이미지 해시 기반(`tmp_db/`의 `kb_emb_*.npz` 캐시와 동형) |
| 품질 게이트 | P1-2에 캡션 커버리지/신뢰도 항목 추가 |

## 6. 착수 전 확인 사항 (blocker)

- [ ] 실 Jira(LSI 프로젝트) 이슈에 **이미지 첨부가 실제로 존재**하는가, 비율은?
  - 확인: `GET /rest/api/2/issue/{key}?fields=attachment` 로 표본 점검.
- [ ] 고객 이미지를 외부 비전 API로 보내도 되는 **데이터 정책**인가?
  (불가 시 Phase 2는 로컬 비전 모델 필요)
- [ ] 주된 이미지 유형 분포(파형 vs 사진 vs 로그 스크린샷) → 캡션 vs OCR 비중 결정.

## 7. 권고 순서

**(C) 본 문서 → 위 확인 → (A) Phase 1(첨부 인입+UI 증거) → (B) Phase 2(VLM+HITL).**
Phase 1은 ML 위험 0이고 Phase 2의 데이터 토대가 된다. Phase 3는 이미지 축적 후 재평가.
