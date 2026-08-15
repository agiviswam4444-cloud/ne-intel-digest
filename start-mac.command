#!/bin/bash
# NE India Security Intelligence Digest — double-click launcher for macOS.
# Uses the bundled .venv, so it is immune to changes in your system PATH
# (e.g. Homebrew putting a different python3 first).
cd "$(dirname "$0")" || exit 1
clear
echo "============================================================"
echo "   NE INDIA // SECURITY INTEL DIGEST"
echo "============================================================"
echo

PY="./.venv/bin/python"

# --- first run / repaired venv -------------------------------------------
if [ ! -x "$PY" ]; then
  echo "Setting up for the first time (a few minutes)..."
  SYS=""
  for c in /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /opt/homebrew/bin/python3 /usr/bin/python3; do
    [ -x "$c" ] && SYS="$c" && break
  done
  if [ -z "$SYS" ]; then
    echo "[!] No python3 found. Install Python 3.12 from https://www.python.org/downloads/"
    read -r -p "Press return to close." _; exit 1
  fi
  "$SYS" -m venv .venv || { echo "[!] Could not create .venv"; read -r _; exit 1; }
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt || { echo "[!] pip failed"; read -r _; exit 1; }
fi

# --- headless browser (used by the crawl / screenshot tiers) --------------
if [ ! -d "$HOME/Library/Caches/ms-playwright" ]; then
  echo "Downloading the headless browser (one time, ~150MB)..."
  "$PY" -m playwright install chromium
fi

# --- collect if there is no database yet ----------------------------------
if [ ! -f data/digest.db ]; then
  echo "No news collected yet — running the first collection (2-3 minutes)..."
  "$PY" run.py collect
fi

# open the dashboard once the server is up
( sleep 6; open "http://127.0.0.1:8642" ) &

echo
echo "Starting the dashboard..."
echo "  * Browser opens at http://127.0.0.1:8642"
echo "  * News refreshes by itself every 30 minutes"
echo "  * KEEP THIS WINDOW OPEN. Press Ctrl+C (or close it) to stop."
echo "============================================================"
echo
"$PY" run.py serve
