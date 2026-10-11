@echo off
setlocal
title PortfolioIQ Launcher

where python >nul 2>nul
if %errorlevel% equ 0 (
    python "%~dp0run.py" %*
    exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    py "%~dp0run.py" %*
    exit /b %errorlevel%
)

:: Fallback if Python is not in PATH
where docker >nul 2>nul
if %errorlevel% equ 0 (
    if not exist "%~dp0.env" (
        if exist "%~dp0.env.example" (
            copy "%~dp0.env.example" "%~dp0.env" >nul
            echo [INFO] Initialized .env configuration from .env.example
        )
    )
    echo Starting PortfolioIQ via Docker Compose...
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
        start http://localhost:8080/index.html
        docker compose logs -f
        exit /b 0
    ) else (
        echo [ERROR] Failed to start Docker containers.
        pause
        exit /b 1
    )
)

echo [ERROR] Neither Python nor Docker was found in your PATH.
pause
exit /b 1
