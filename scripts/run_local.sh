#!/bin/bash
# 로컬 러너: 최신 상태로 pull → 브리핑 생성 → 커밋·push → 발송
# launchd 가 매일 아침 실행합니다 (scripts/install_launchd.sh). 직접 실행해도 됩니다.
#   bash scripts/run_local.sh              # 전체 실행
#   bash scripts/run_local.sh --no-notify  # main.py 옵션 그대로 전달
#
# 전체를 함수로 감싸 bash 가 실행 전에 끝까지 읽어 두게 한다. git pull 이 실행 중에 이 파일을
# 바꿔도 뒤섞여 실행되지 않고, pull 뒤에는 새 버전으로 다시 시작한다.
set -uo pipefail

run() {
  export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
  export LANG="${LANG:-ko_KR.UTF-8}"
  local repo
  repo="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$repo" || exit 1

  if [ -z "${BRIEF_PULLED:-}" ]; then
    echo "===== $(date '+%F %T') 모닝 브리핑 시작 ====="
    git pull --rebase --autostash -q || echo "⚠️ git pull 실패 — 로컬 상태로 진행합니다"
    BRIEF_PULLED=1 exec /bin/bash "$repo/scripts/run_local.sh" "$@"
  fi

  # 동시 실행 방지 (수동 실행과 예약 실행이 겹칠 때)
  LOCK="$repo/.run.lock"
  if ! mkdir "$LOCK" 2>/dev/null; then
    echo "이미 실행 중입니다 ($LOCK). 이전 실행이 비정상 종료됐다면 이 폴더를 지우세요."
    exit 1
  fi
  trap 'rmdir "$LOCK"' EXIT

  local py="$repo/.venv/bin/python"
  [ -x "$py" ] || py="$(command -v python3)"
  "$py" src/main.py --publish "$@"
  local status=$?

  # main.py 가 못 올린 것이 있으면 마저 커밋·push (curated/ 의 작성 중인 노트는 건드리지 않음)
  local p
  for p in vault/raw state docs; do [ -e "$p" ] && git add "$p"; done
  if ! git diff --cached --quiet; then
    git commit -q -m "briefing: $(date +%F)" && echo "커밋 완료"
  fi
  if [ -n "$(git log --oneline '@{u}..' 2>/dev/null)" ]; then
    git push -q && echo "push 완료" || echo "⚠️ git push 실패 — 다음 실행 때 함께 올라갑니다"
  fi

  echo "===== $(date '+%F %T') 종료 (exit $status) ====="
  exit $status
}

run "$@"
