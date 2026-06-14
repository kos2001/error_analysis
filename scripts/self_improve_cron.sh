#!/usr/bin/env bash
# 자기 개선 loop 주기 실행 래퍼 (cron/launchd용).
#   - 측정·진단(L1) + 지식 변경 제안(L3) 큐 병합을 오프라인으로 1회 수행.
#   - 서버 불필요. 지식 불변(L2 적용·L3 실행은 사람이 검토 후).
#   - 로그: logs/self_improve_cron.log (append).
#
# 설치(아래 '주기 실행 설정' 문서 참고):
#   crontab(Linux):  0 9 * * *  /path/to/repo/scripts/self_improve_cron.sh
#   launchd(macOS):  scripts/com.lsi.selfimprove.plist 로드
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/self_improve_cron.log"

# 자격증명/설정 주입(.env 있으면). app_config(tmp_db)는 모듈이 자체 로드.
if [ -f "$REPO/.env" ]; then set -a; . "$REPO/.env"; set +a; fi

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

TS="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[$TS] self-improve 주기 실행 시작" >> "$LOG"
if (cd "$REPO/src" && "$PY" self_improve.py) >> "$LOG" 2>&1; then
  echo "[$TS] 완료" >> "$LOG"
else
  echo "[$TS] 실패(exit=$?) — 로그 확인" >> "$LOG"
  exit 1
fi
