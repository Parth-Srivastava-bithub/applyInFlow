#!/usr/bin/env bash
# ==============================================================================
# Launch Google Chrome with Remote Debugging Port 9222 (macOS / Linux)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$SCRIPT_DIR/chrome_profile"

mkdir -p "$PROFILE_DIR"

echo "================================================================"
echo " Starting Google Chrome with Remote Debugging Port 9222"
echo " Profile directory: $PROFILE_DIR"
echo "================================================================"

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if [ ! -f "$CHROME_BIN" ]; then
        echo "Error: Google Chrome not found at $CHROME_BIN"
        exit 1
    fi
    "$CHROME_BIN" --remote-debugging-port=9222 --user-data-dir="$PROFILE_DIR" "https://www.linkedin.com" &
else
    # Linux
    if command -v google-chrome &> /dev/null; then
        CHROME_BIN="google-chrome"
    elif command -v google-chrome-stable &> /dev/null; then
        CHROME_BIN="google-chrome-stable"
    elif command -v chromium-browser &> /dev/null; then
        CHROME_BIN="chromium-browser"
    elif command -v chromium &> /dev/null; then
        CHROME_BIN="chromium"
    else
        echo "Error: Neither google-chrome nor chromium found in PATH."
        exit 1
    fi
    "$CHROME_BIN" --remote-debugging-port=9222 --user-data-dir="$PROFILE_DIR" "https://www.linkedin.com" &
fi

echo ""
echo "Chrome launched!"
echo "1. In the Chrome window that just opened, log into your LinkedIn account."
echo "2. Once logged in, AutoApply will connect automatically via CDP on port 9222."
echo "================================================================"
