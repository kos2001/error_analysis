# 자기 개선 loop 주기 실행(cron) 운영 설정

매일 1회 **측정·진단(L1) + 지식 변경 제안(L3) 큐 갱신**을 자동 수행한다.
서버 불필요(오프라인). **지식은 불변** — L2 파라미터 적용·L3 제안 실행은 사람이 검토 후.

## 무엇이 도는가
`scripts/self_improve_cron.sh` → `src/self_improve.py` (run_full):
- 측정: 유용성·KB품질·지식공백·자산통계 → 날짜별 리포트(`claudedocs/self_improve/`)
- 진단: 신호→우선순위 권고
- 제안: 미승격 군집 승격·공백 RCA 작성·비유용 사례 폐기검토·온톨로지 정규화
  → `data/improve_queue.json` 큐에 병합(거부/완료 보존)
- 로그: `logs/self_improve_cron.log`

사람은 다음 날 `GET /improve/queue` (또는 큐 파일)만 확인하면 된다.

## 설치

### macOS (launchd, 권장 · sudo 불필요)
```sh
cd /path/to/repo
sed "s#__REPO__#$(pwd)#" scripts/com.lsi.selfimprove.plist > ~/Library/LaunchAgents/com.lsi.selfimprove.plist
launchctl load ~/Library/LaunchAgents/com.lsi.selfimprove.plist
launchctl list | grep com.lsi.selfimprove        # 확인
launchctl start com.lsi.selfimprove              # 즉시 1회 실행(테스트)
```
제거(되돌리기):
```sh
launchctl unload ~/Library/LaunchAgents/com.lsi.selfimprove.plist
rm ~/Library/LaunchAgents/com.lsi.selfimprove.plist
```

### Linux (crontab)
```sh
chmod +x scripts/self_improve_cron.sh
( crontab -l 2>/dev/null; echo "0 9 * * * $(pwd)/scripts/self_improve_cron.sh" ) | crontab -
crontab -l                                       # 확인
```
제거: `crontab -e` 에서 해당 줄 삭제.

## 주기 변경
- launchd: plist의 `StartCalendarInterval`(Hour/Minute). 여러 시각은 array로.
- crontab: `0 9 * * *`(매일 09:00) → 예) `0 */6 * * *`(6시간마다).

## 주의
- 리랭커는 cron에서 off(외부 API 비용 0). 군집화는 로컬 fastembed.
- `data/improve_queue.json`·`self_improve_history.json`은 누적 — git 추적(버전·공유).
  다중 머신에서 동시 cron 시 충돌 가능(P1-1과 동일 한계) → 단일 운영 노드 권장.
