#!/bin/bash
# 매일 아침 이 Mac 에서 브리핑이 자동으로 돌도록 launchd 에 등록합니다.
#   bash scripts/install_launchd.sh               # 07:00 에 등록 (BRIEF_HOUR / BRIEF_MINUTE 로 변경)
#   bash scripts/install_launchd.sh --uninstall   # 해제
#
# 그 시각부터 15분마다 깨워 보고, 그날 아직 안 돌았고 인터넷이 될 때 한 번만 실행한다
# (판단은 run_local.sh). 잠자기·오프라인이던 날도 Mac 을 열면 곧 받아 볼 수 있다.
set -euo pipefail

LABEL="com.morning-brief.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/morning-brief.log"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOUR="${BRIEF_HOUR:-7}"
MINUTE="${BRIEF_MINUTE:-0}"
AT="$(printf '%02d:%02d' "$((10#$HOUR))" "$((10#$MINUTE))")"
DOMAIN="gui/$(id -u)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "해제했습니다: $LABEL"
  exit 0
fi

# 1) claude CLI 확인 (구독 로그인 상태여야 함)
CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ]; then
  echo "claude CLI 를 찾을 수 없습니다. Claude Code 를 설치하고 'claude' 로 한 번 로그인하세요." >&2
  exit 1
fi

# 2) 가상환경 + 의존성
if [ ! -x "$REPO/.venv/bin/python" ]; then
  python3 -m venv "$REPO/.venv"
fi
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"

# 3) launchd 등록
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
RUN_PATH="$(dirname "$CLAUDE"):$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/scripts/run_local.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
$(for i in 0 15 30 45; do printf '    <dict><key>Minute</key><integer>%d</integer></dict>\n' $(( (10#$MINUTE + i) % 60 )); done)
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$RUN_PATH</string>
    <key>LANG</key>
    <string>ko_KR.UTF-8</string>
    <key>BRIEF_AT</key>
    <string>$AT</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "등록 완료: 매일 $AT 에 실행됩니다. (그때 잠자기·오프라인이면 15분마다 다시 시도해 하루 한 번)"
echo "  지금 바로 실행: bash \"$REPO/scripts/run_local.sh\""
echo "  로그 보기:      tail -f \"$LOG\""
echo "  해제:          bash scripts/install_launchd.sh --uninstall"
echo
echo "참고: 덮개를 닫아 둔 Mac 은 네트워크가 끊겨 있어, 보통 Mac 을 연 뒤 15분 안에 실행됩니다."
WAKE=$(( (10#$HOUR * 60 + 10#$MINUTE + 1435) % 1440 ))   # 실행 5분 전
printf '      매일 자동으로 깨우려면: sudo pmset repeat wakeorpoweron MTWRFSU %02d:%02d:00\n' "$(( WAKE / 60 ))" "$(( WAKE % 60 ))"
