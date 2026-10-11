#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 &> /dev/null; then
    exec python3 run.py "$@"
elif command -v python &> /dev/null; then
    exec python run.py "$@"
elif command -v docker &> /dev/null; then
    if [ ! -f .env ] && [ -f .env.example ]; then
        cp .env.example .env
        echo "[INFO] Initialized .env configuration from .env.example"
    fi
    echo "Starting PortfolioIQ via Docker Compose..."
    docker compose up --build -d
    echo ""
    echo "========================================================"
    echo "  PortfolioIQ is up and running!"
    echo "  Frontend Dashboard: http://localhost:8080"
    echo "  Backend REST API:   http://localhost:5000"
    echo "  Health Endpoint:    http://localhost:5000/api/v1/health"
    echo "========================================================"
    echo ""
    if command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:8080/index.html" 2>/dev/null || true
    elif command -v open &> /dev/null; then
        open "http://localhost:8080/index.html" 2>/dev/null || true
    fi
    docker compose logs -f
else
    echo "[ERROR] Neither Python nor Docker was found in your PATH."
    exit 1
fi
