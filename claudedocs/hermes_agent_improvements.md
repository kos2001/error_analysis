# Hermes Agent 기반 개선점

> 2026-06-14. 출처: github.com/nousresearch/hermes-agent ("self-improving AI agent").
> 현 상태: LSI는 hermes_engine.py로 `hermes chat --cli`를 **순수 생성 엔진**으로만 사용
> (toolset 비활성). hermes-agent의 설계 개념을 LSI 서비스 개선점으로 매핑.

## Hermes-agent 핵심 개념 → LSI 매핑 요약

| Hermes-agent 개념 | LSI 현황 | 개선 여지 |
|---|---|---|
| 닫힌 학습 루프(자기개선·persistent memory·nudge) | 자기개선 loop L1~L3 + cron 보유 | nudge 배달·skill 승격 미흡 |
| MCP 호환(40+ 도구, MCP 서버) | 순수 생성만, 도구 미연동 | **KB를 MCP 서버로 노출** |
| cron + "any platform" 배달 | launchd cron(로그만) | **다이제스트/승인대기 알림 배달** |
| "Lives where you do"(Slack/TG/CLI 게이트웨이) | 웹 전용 | **챗 인터페이스** |
| delegate/parallelize 서브에이전트 | explain 단일 패스 | **병렬 다중 가설 분석** |
| Skills(절차적 기억, agentskills.io) | 선언적 지식(사례/기사) | **진단 플레이북(절차) 승격** |
| 모델 무종속·self-hosted | agno/hermes 선택 | self-hosted(데이터 주권) |
| 메모리(FTS5+요약+user profile) | 전역 KB만 | 세션/사용자 기억(낮음) |

## P1 — 고레버리지(기존 자산 위에 바로 확장)

### 1. LSI 지식 KB를 MCP 서버로 노출
hermes-agent는 MCP 서버를 도구로 붙인다. LSI의 recommender/Known-Issue/RCA 초안을
**MCP 도구**(`search_similar_cases`, `propose_rca`, `get_known_issue`)로 노출하면:
- hermes-agent·Claude·임의 에이전트가 LSI 지식을 도구로 호출 → 지식 자산이 도구
  생태계에서 재사용(P3-10 export/interop의 실행형 확장).
- 노력: 중(FastAPI 엔드포인트가 이미 있어 MCP 래퍼만). 위험: 낮음(읽기 위주).

### 2. cron 자기개선 다이제스트를 채널로 배달
현재 `self_improve_cron.sh`는 로그만 남긴다. hermes-agent의 "cron + delivery to any
platform"처럼 **매일 다이제스트 + 'N건 승인 대기' + 지식공백 알림을 Slack/Telegram에
푸시**. 사람이 큐를 안 들여다봐도 nudge → HITL 환기. 노력: 소(webhook 1개). 위험: 낮음.

## P2 — 접근성·분석 깊이

### 3. 챗 인터페이스(hermes gateway)
LSI는 웹 전용. hermes gateway(Slack/Telegram/CLI)로 엔지니어가 채팅에서
"PM9C3 throttle 비슷한 사례?" → 추천 + RCA 초안 → 승인 대기 등록까지. 마찰 대폭 감소.
승인(Jira 게시)은 HITL 유지. 노력: 중~대. 위험: 중(인증·권한).

### 4. 서브에이전트 병렬 다중 가설 분석
hermes-agent는 격리 서브에이전트로 병렬화. 복합 증상 이슈를 **증상별/후보 근본원인별
병렬 분석 후 종합** → 깊이↑(현재 explain은 단일 패스). agno 멀티에이전트 또는 hermes
delegate로. 노력: 중. 위험: 중(비용·일관성).

### 5. Skills(절차적 기억) — Known-Issue를 진단 플레이북으로
현재 지식은 선언적(사례/기사). hermes-agent는 **절차적 skill을 경험에서 생성·개선**
(agentskills.io 표준). 개선: Known-Issue 기사에 "진단 절차(재현·측정·검증 단계)"를
구조화해 재사용 가능한 플레이북으로. 자기개선 loop가 자주 쓰인 절차를 skill로 승격.
노력: 중. 위험: 낮음(P2-4 기사 계층 확장).

## P3 — 운영·기반

### 6. hermes proxy(OpenAI 호환 로컬) 전환
이미 performance_backlog #9. CLI 서브프로세스 부팅(~수 초/호출) 제거 + 진짜 토큰
스트리밍. hermes-agent 권장 운영 방식. 노력: 소(엔드포인트 전환). 위험: 낮음.

### 7. self-hosted / Nous Portal (데이터 주권)
고객 데이터(특히 이미지)를 외부 API로 못 보내는 정책일 때 self-hosted 엔드포인트.
image_handling.md의 "로컬 비전 모델 옵션"과 연결. 노력: 중(인프라). 위험: 낮음.

### 8. 세션/사용자 메모리 (낮음)
hermes의 FTS5 세션검색 + user profile. 엔지니어별 진행 중 조사·선호 cross-session
기억. 현 단일 도구엔 우선순위 낮음.

## 권고 순서
1) **P1-1 MCP 서버 노출** — 지식 자산을 에이전트 생태계로 개방(가장 큰 레버리지, 읽기라 안전).
2) **P1-2 다이제스트 배달** — 적은 코드로 자기개선 loop를 "능동적"으로(nudge).
3) 이후 P2-5(플레이북)·P2-3(챗)·P3-6(proxy).

## 주의(정직)
- 대부분 **실 운영(실데이터·실채널)** 전제 — 합성 데이터/단일 머신에선 가치 제한적.
- 챗·배달·게이트웨이는 **인증·권한·고객데이터 정책** 검토 필수(이미지 처리와 동일 우려).
- LSI는 hermes를 "생성 엔진"으로만 쓰는 중 — 위 다수는 hermes 도구/게이트웨이/skill
  기능을 실제 활성화해야 하므로, hermes 운영 셋업(proxy·로그인) 선행이 필요.
