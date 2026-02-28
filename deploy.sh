#!/usr/bin/env bash
# =============================================================
# deploy.sh  —  FlightPi code updater
#
# Pulls the latest code from GitHub (main branch) and restarts
# both systemd services. Run this from the Pi whenever you want
# to update the code without manually copying files.
#
# FIRST-TIME SETUP (one time only — see bottom of this file):
#   chmod +x deploy.sh
#
# NORMAL USAGE:
#   ./deploy.sh
#
# FROM ANYWHERE (full path):
#   /home/pi/flight-display/deploy.sh
# =============================================================

set -euo pipefail

# Resolve the directory this script lives in (the project root).
# Works no matter where you call it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/deploy.log"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

echo ""
echo "========================================================"
echo "  FlightPi Deploy  —  $TIMESTAMP"
echo "========================================================"
echo ""

# ---- 1. Pull latest code from GitHub ----
echo "[1/3] Pulling latest code from origin/main ..."
git -C "$SCRIPT_DIR" pull origin main

GIT_HASH="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"
echo "      → Now at commit: $GIT_HASH"
echo ""

# ---- 2. Restart the LCD display service ----
echo "[2/3] Restarting flight-display service ..."
sudo systemctl restart flight-display
sleep 1
STATUS_DISPLAY="$(systemctl is-active flight-display 2>/dev/null || echo unknown)"
echo "      → flight-display: $STATUS_DISPLAY"
echo ""

# ---- 3. Restart the web server service ----
echo "[3/3] Restarting flight-web service ..."
sudo systemctl restart flight-web
sleep 1
STATUS_WEB="$(systemctl is-active flight-web 2>/dev/null || echo unknown)"
echo "      → flight-web: $STATUS_WEB"
echo ""

echo "========================================================"
echo "  Deploy complete!"
echo "  Commit : $GIT_HASH"
echo "  Display: $STATUS_DISPLAY"
echo "  Web    : $STATUS_WEB"
echo "========================================================"
echo ""

# ---- Append a single line to deploy.log (read by /pi status page) ----
echo "[$TIMESTAMP] Deployed $GIT_HASH  display:$STATUS_DISPLAY  web:$STATUS_WEB" >> "$LOG_FILE"
