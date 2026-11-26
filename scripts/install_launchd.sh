#!/bin/bash
# 매일 아침 이 Mac 에서 브리핑이 자동으로 돌도록 launchd 에 등록합니다.
#   bash scripts/install_launchd.sh               # 07:00 에 등록 (BRIEF_HOUR / BRIEF_MINUTE 로 변경)
#   bash scripts/install_launchd.sh --uninstall   # 해제
set -euo pipefail

LABEL="com.morning-brief.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/morning-brief.log"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOUR="${BRIEF_HOUR:-7}"
MINUTE="${BRIEF_MINUTE:-0}"
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
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>$MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$RUN_PATH</string>
    <key>LANG</key>
    <string>ko_KR.UTF-8</string>
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

printf '등록 완료: 매일 %02d:%02d 에 실행됩니다.\n' "$HOUR" "$MINUTE"
echo "  지금 바로 실행: launchctl kickstart $DOMAIN/$LABEL"
echo "  로그 보기:      tail -f \"$LOG\""
echo "  해제:          bash scripts/install_launchd.sh --uninstall"
echo
echo "참고: 그 시각에 Mac 이 잠자기 중이면 깨어날 때 실행되고, 꺼져 있으면 그날은 건너뜁니다."
printf '      매일 자동으로 깨우려면: sudo pmset repeat wakeorpoweron MTWRFSU %02d:%02d:00\n' "$HOUR" "$(( MINUTE >= 5 ? MINUTE - 5 : 0 ))"
