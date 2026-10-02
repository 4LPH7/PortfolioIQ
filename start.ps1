<#
.SYNOPSIS
    PortfolioIQ One-Command Launcher (Docker & Local)
.DESCRIPTION
    Builds and starts all PortfolioIQ services:
    - PostgreSQL Database (container: portfolioiq_postgres)
    - Backend REST API & Engine (container: portfolioiq_backend on :5000)
    - Frontend UI Web Server (container: portfolioiq_frontend on :8080)
    Automatically opens the dashboard in your default browser.
.EXAMPLE
    .\start.ps1           # Starts full stack in Docker and opens browser
    .\start.ps1 -Detach   # Starts in background
    .\start.ps1 -LocalDev # Runs locally without Docker
#>

[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$Detach,
    [switch]$LocalDev
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "   PortfolioIQ — Full Stack Financial Dashboard" -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

if ($LocalDev) {
    Write-Host "[Mode: Local Development without Docker]" -ForegroundColor Yellow
    $env:DATABASE_URL = "postgresql://portfolioiq_user:portfolioiq_pass_change_me@localhost:5432/portfolioiq"
    if (-not $env:PORTFOLIOIQ_API_KEY) { $env:PORTFOLIOIQ_API_KEY = "dev-api-key" }
    $env:RUN_INLINE_SCHEDULER = "true"
    
    Write-Host "Starting Flask backend on http://localhost:5000..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; python flask_app.py"
    
    Start-Sleep -Seconds 2
    Write-Host "Starting HTTP server for frontend on http://localhost:8080..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot\frontend'; python -m http.server 8080"
    
    Start-Sleep -Seconds 2
    if (-not $NoBrowser) {
        Start-Process "http://localhost:8080/index.html"
    }
    Write-Host "`nPortfolioIQ is running!" -ForegroundColor Cyan
    Write-Host "  Frontend: http://localhost:8080" -ForegroundColor White
    Write-Host "  Backend:  http://localhost:5000/api/v1/health`n" -ForegroundColor White
    return
}

# Verify Docker is available
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Warning "Docker command not found in PATH."
    Write-Host "Falling back to local development mode (-LocalDev)..." -ForegroundColor Yellow
    & "$PSScriptRoot\start.ps1" -LocalDev
    return
}

Set-Location $ProjectRoot

# Copy .env.example to .env if .env does not exist
if (-not (Test-Path "$ProjectRoot\.env") -and (Test-Path "$ProjectRoot\.env.example")) {
    Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
    Write-Host "Initialized .env configuration from .env.example" -ForegroundColor Yellow
}

Write-Host "Building and launching containers via Docker Compose..." -ForegroundColor Green

if ($Detach) {
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker Compose failed to start services."
    }
    Start-Sleep -Seconds 4
    Write-Host "`n========================================================" -ForegroundColor Green
    Write-Host "   PortfolioIQ Containers are Running!" -ForegroundColor Green
    Write-Host "   Frontend Dashboard: http://localhost:8080" -ForegroundColor Cyan
    Write-Host "   Backend API:        http://localhost:5000" -ForegroundColor Cyan
    Write-Host "   API Health:         http://localhost:5000/api/v1/health" -ForegroundColor Cyan
    Write-Host "========================================================`n" -ForegroundColor Green
    Write-Host "To view live logs: docker compose logs -f" -ForegroundColor White
    Write-Host "To stop services:  docker compose down`n" -ForegroundColor White

    if (-not $NoBrowser) {
        Start-Process "http://localhost:8080/index.html"
    }
} else {
    # Launch in background, then open browser and tail logs
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker Compose failed to start services."
    }
    Start-Sleep -Seconds 4
    Write-Host "`nOpening browser at http://localhost:8080/index.html..." -ForegroundColor Cyan
    if (-not $NoBrowser) {
        Start-Process "http://localhost:8080/index.html"
    }
    Write-Host "`nStreaming container logs (Press Ctrl+C to detach):`n" -ForegroundColor Yellow
    docker compose logs -f
}
