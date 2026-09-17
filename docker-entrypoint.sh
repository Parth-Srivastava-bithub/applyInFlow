#!/usr/bin/env bash
set -e

echo "=================================================="
echo "  AutoApply Web Service Starting (Docker)"
echo "=================================================="

# Ensure directories exist
mkdir -p /app/output/resumes

export HOST="0.0.0.0"
export PORT="5000"

echo "=================================================="
echo "  AutoApply Dashboard : http://localhost:5000"
echo "  LaTeX Microservice  : http://localhost:8001"
echo "  Scraping Engine     : Chrome Extension Client"
echo "=================================================="

# Start AutoApply Flask Server
exec python server.py
