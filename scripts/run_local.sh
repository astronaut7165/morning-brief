#!/bin/bash
# 로컬 러너: 최신 상태로 pull → 브리핑(기술·비즈니스, 코인)마다 생성 → 커밋·push → 발송
# launchd 가 매일 아침 실행합니다 (scripts/install_launchd.sh). 직접 실행해도 됩니다.
#   bash scripts/run_local.sh              # 전체 실행
#   bash scripts/run_local.sh --no-notify  # main.py 옵션 그대로 전달
#
# 예약 실행(launchd 가 BRIEF_AT=07:00 을 넘겨 15분마다 부름): 그 시각 이후, 오늘 아직 안 돌았고,
# 인터넷이 될 때만 실행한다. 덮개를 닫은 Mac 이 잠깐 깨는 '다크 웨이크'에는 네트워크가 없어
# 그때 돌면 전부 실패하므로, 네트워크가 잡히는 다음 회차(보통 Mac 을 연 직후)로 미룬다.
#
# 전체를 함수로 감싸 bash 가 실행 전에 끝까지 읽어 두게 한다. git pull 이 실행 중에 이 파일을
# 바꿔도 뒤섞여 실행되지 않고, pull 뒤에는 새 버전으로 다시 시작한다.
set -uo pipefail

online() {
  # 최대 $1초 동안 10초 간격으로 확인. sleep 은 잠자기 동안 멈추므로 벽시계로 잰다
  local deadline=$(( $(date +%s) + $1 ))
  while :; do
    curl -fsS -o /dev/null --max-time 10 https://github.com 2>/dev/null && return 0
    [ "$(date +%s)" -ge "$deadline" ] && return 1
    sleep 10
  done
}

run() {
  export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
  export LANG="${LANG:-ko_KR.UTF-8}"
  local repo done_file today
  repo="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$repo" || exit 1
  done_file="$repo/.run.done"   # 오늘 실행을 시작한 날짜 (예약 실행이 하루 한 번만 돌게)
  today="$(date +%F)"

  if [ -z "${BRIEF_PULLED:-}" ]; then
    if [ -n "${BRIEF_AT:-}" ]; then
      [[ "$(date +%H%M)" < "${BRIEF_AT/:/}" ]] && exit 0
      [ "$(cat "$done_file" 2>/dev/null)" = "$today" ] && exit 0
    fi
    if ! online 120; then
      echo "$(date '+%F %T') 인터넷 연결이 없어 건너뜁니다${BRIEF_AT:+ — 15분 뒤 다시 시도}"
      exit 0
    fi
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
  echo "$today" > "$done_file"
  # 실행 중에는 잠자기 방지 (이 셸이 끝나면 함께 풀림)
  command -v caffeinate >/dev/null && caffeinate -i -w $$ &

  local py="$repo/.venv/bin/python"
  [ -x "$py" ] || py="$(command -v python3)"
  # 브리핑마다 설정 파일 하나: 기술·비즈니스 → 코인 순서. 하나가 실패해도 다음 것은 돈다
  local status=0 cfg
  for cfg in config/sources.yaml config/crypto.yaml; do
    [ -f "$cfg" ] || continue
    "$py" src/main.py --config "$cfg" --publish "$@" || status=1
  done

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
