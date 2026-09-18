#!/usr/bin/env bash
set -e

echo "=================================================="
echo "  AutoApply Web Service Starting (Docker)"
echo "=================================================="

# Ensure directories exist
mkdir -p /app/output/resumes

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5000}"

echo "=================================================="
echo "  AutoApply Dashboard : http://${HOST}:${PORT}"
echo "  Scraping Engine     : Chrome Extension Client"
echo "=================================================="

# Start AutoApply Flask Server
exec python server.py
