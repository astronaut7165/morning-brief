#!/bin/bash
# 로컬 러너: 최신 상태로 pull → 브리핑 생성·발송 → vault 커밋·push
# launchd 가 매일 아침 실행합니다 (scripts/install_launchd.sh). 직접 실행해도 됩니다.
#   bash scripts/run_local.sh              # 전체 실행
#   bash scripts/run_local.sh --no-notify  # main.py 옵션 그대로 전달
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export LANG="${LANG:-ko_KR.UTF-8}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

# 동시 실행 방지 (수동 실행과 예약 실행이 겹칠 때)
LOCK="$REPO/.run.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "이미 실행 중입니다 ($LOCK). 이전 실행이 비정상 종료됐다면 이 폴더를 지우세요."
  exit 1
fi
trap 'rmdir "$LOCK"' EXIT

echo "===== $(date '+%F %T') 모닝 브리핑 시작 ====="

git pull --rebase --autostash -q || echo "⚠️ git pull 실패 — 로컬 상태로 진행합니다"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" src/main.py "$@"
status=$?

# 자동화가 만드는 파일만 커밋 (curated/ 의 작성 중인 노트는 건드리지 않음)
git add vault/raw state
if ! git diff --cached --quiet; then
  git commit -q -m "briefing: $(date +%F)" && echo "커밋 완료"
  git push -q && echo "push 완료" || echo "⚠️ git push 실패 — 다음 실행 때 함께 올라갑니다"
fi

echo "===== $(date '+%F %T') 종료 (exit $status) ====="
exit $status
