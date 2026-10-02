@echo off
title PortfolioIQ Launcher
echo ========================================================
echo   PortfolioIQ -- One-Command Full Stack Launcher
echo ========================================================
echo.

where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARNING] Docker is not installed or not in PATH!
    echo Launching local development mode...
    powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1" -LocalDev
    exit /b %errorlevel%
)

echo Starting PostgreSQL, Backend API, and Frontend UI containers...
docker compose up --build -d

if %errorlevel% equ 0 (
    echo.
    echo ========================================================
    echo   PortfolioIQ Containers are Running!
    echo   Frontend Dashboard: http://localhost:8080
    echo   Backend REST API:   http://localhost:5000
    echo   Health Endpoint:    http://localhost:5000/api/v1/health
    echo ========================================================
    echo.
    echo Opening dashboard in browser...
    start http://localhost:8080/index.html
    echo.
    echo View logs with: docker compose logs -f
    echo Stop with:      docker compose down
) else (
    echo [ERROR] Failed to start Docker containers.
)
pause
