#!/bin/bash
# Installs two launchd jobs:
#   1. daily collector at 07:01 (fires on first wake if the Mac was asleep/off)
#   2. UI server kept alive at http://127.0.0.1:8642
set -e
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3)"
LA=~/Library/LaunchAgents
mkdir -p "$LA" "$APP_DIR/data"

cat > "$LA/in.ne.inteldigest.collect.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>in.ne.inteldigest.collect</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$APP_DIR/run.py</string><string>collect</string></array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>1</integer></dict>
  <key>StandardOutPath</key><string>$APP_DIR/data/collect.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/data/collect.log</string>
</dict></plist>
EOF

cat > "$LA/in.ne.inteldigest.serve.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>in.ne.inteldigest.serve</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$APP_DIR/run.py</string><string>serve</string></array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$APP_DIR/data/serve.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/data/serve.log</string>
</dict></plist>
EOF

launchctl unload "$LA/in.ne.inteldigest.collect.plist" 2>/dev/null || true
launchctl unload "$LA/in.ne.inteldigest.serve.plist" 2>/dev/null || true
launchctl load "$LA/in.ne.inteldigest.collect.plist"
launchctl load "$LA/in.ne.inteldigest.serve.plist"

echo "Installed."
echo "  Dashboard : http://127.0.0.1:8642"
echo "  Collector : daily 07:01 local time (fires on wake if machine was asleep)"
echo "  Logs      : $APP_DIR/data/*.log"
echo "  Remove    : launchctl unload $LA/in.ne.inteldigest.*.plist && rm $LA/in.ne.inteldigest.*.plist"
