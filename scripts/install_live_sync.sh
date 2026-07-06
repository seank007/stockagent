#!/bin/zsh
# live_sync 데몬을 launchd 로그인 항목으로 설치한다.
# 사용: zsh scripts/install_live_sync.sh   (프로젝트 루트 기준 아무 데서나 가능)
set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$HOME/.venvs/stockagent/bin/python3"
PLIST="$HOME/Library/LaunchAgents/com.stockagent.livesync.plist"
LOG="$HOME/Library/Logs/stockagent-livesync.log"

if [[ ! -x "$PYTHON" ]]; then
  echo "venv python 없음: $PYTHON" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.stockagent.livesync</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${REPO}/scripts/live_sync.py</string>
    <string>--loop</string>
  </array>
  <key>WorkingDirectory</key><string>${REPO}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.stockagent.livesync" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "설치 완료: com.stockagent.livesync (로그: $LOG)"
