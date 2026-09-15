#!/usr/bin/env bash
set -e

echo "=================================================="
echo "  AutoApply Docker Container Starting"
echo "=================================================="

# Ensure directories exist
mkdir -p /data/chrome_profile
mkdir -p /app/output/resumes

# Set display
export DISPLAY=:99
export CDP_URL="http://127.0.0.1:9222"
export HOST="0.0.0.0"
export PORT="5000"

# 1. Start Xvfb (Virtual Framebuffer) & Window Manager
echo "[1/4] Starting Xvfb & fluxbox on display :99..."
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
sleep 1
fluxbox &
sleep 1

# 2. Start x11vnc & noVNC
echo "[2/4] Starting VNC & noVNC web viewer on port 6080..."
x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -quiet &
websockify --web=/usr/share/novnc 6080 localhost:5900 > /dev/null 2>&1 &

# 3. Launch Google Chrome with CDP & Persistent Profile
echo "[3/4] Launching Google Chrome with remote debugging on port 9222..."
# Clean up any stale singleton locks from previous container runs & suppress ToS dialog
rm -f /data/chrome_profile/SingletonLock /data/chrome_profile/SingletonCookie /data/chrome_profile/SingletonSocket
touch "/data/chrome_profile/First Run"

CHROME_BIN="google-chrome-stable"
if ! command -v google-chrome-stable &> /dev/null; then
    if command -v google-chrome &> /dev/null; then
        CHROME_BIN="google-chrome"
    elif command -v chromium &> /dev/null; then
        CHROME_BIN="chromium"
    fi
fi

$CHROME_BIN \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --remote-allow-origins=* \
    --user-data-dir=/data/chrome_profile \
    --no-first-run \
    --disable-fre \
    --no-default-browser-check \
    --disable-dev-shm-usage \
    --no-sandbox \
    --disable-gpu \
    --window-size=1440,900 \
    --start-maximized \
    --password-store=basic \
    --disable-blink-features=AutomationControlled \
    "https://www.linkedin.com/login" > /dev/null 2>&1 &

# Wait for Chrome CDP to be responsive
echo "Waiting for Chrome CDP to initialize..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "Chrome CDP is ready on http://127.0.0.1:9222!"
        break
    fi
    sleep 1
done

echo "=================================================="
echo "  Services Status:"
echo "  - AutoApply Dashboard : http://localhost:5000"
echo "  - noVNC Web Login GUI : http://localhost:6080"
echo "  - Chrome DevTools CDP : http://localhost:9222"
echo "=================================================="

# 4. Start AutoApply Flask Server
echo "[4/4] Starting AutoApply Dashboard..."
exec python server.py
