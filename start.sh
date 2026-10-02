#!/usr/bin/env bash
set -e

echo "========================================================"
echo "  PortfolioIQ — One-Command Full Stack Launcher"
echo "========================================================"
echo ""

if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker is not installed or not in PATH."
    exit 1
fi

echo "Building and launching containers via Docker Compose..."
docker compose up --build -d

echo ""
echo "========================================================"
echo "  PortfolioIQ is up and running!"
echo "  Frontend Dashboard: http://localhost:8080"
echo "  Backend REST API:   http://localhost:5000"
echo "  Health Endpoint:    http://localhost:5000/api/v1/health"
echo "========================================================"
echo ""
echo "To view live logs: docker compose logs -f"
echo "To stop:           docker compose down"
echo ""

if command -v xdg-open &> /dev/null; then
    xdg-open "http://localhost:8080/index.html" 2>/dev/null || true
elif command -v open &> /dev/null; then
    open "http://localhost:8080/index.html" 2>/dev/null || true
fi
