<#
.SYNOPSIS
    PortfolioIQ One-Command Launcher (Cross-Platform / Windows PowerShell)
.DESCRIPTION
    Builds and starts all PortfolioIQ services in one command:
    - PostgreSQL Database (container: portfolioiq_postgres on port 5434)
    - Backend REST API & Quant Engine (container: portfolioiq_backend on port 5000)
    - Frontend UI Web Server (container: portfolioiq_frontend on port 8080)
    Automatically opens the dashboard in your default browser.
.EXAMPLE
    .\start.ps1           # Starts full stack in Docker and opens browser
    .\start.ps1 -Detach   # Starts in background
    .\start.ps1 -Status   # Checks service health status
    .\start.ps1 -Stop     # Stops all containers
    .\start.ps1 -LocalDev # Runs locally without Docker
#>

[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$Detach,
    [switch]$LocalDev,
    [switch]$Status,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

# Locate best python executable
$pythonCmd = $null
$candidates = @(
    "$ProjectRoot\.venv\env\Scripts\python.exe",
    "$ProjectRoot\.venv\Scripts\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
)
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) {
        $pythonCmd = $c
        break
    }
}

if ($pythonCmd) {
    $argsList = @("$ProjectRoot\run.py")
    if ($Detach) { $argsList += "-d" }
    if ($LocalDev) { $argsList += "--local" }
    if ($NoBrowser) { $argsList += "--no-browser" }
    if ($Status) { $argsList += "--status" }
    if ($Stop) { $argsList += "--stop" }

    & $pythonCmd $argsList
    exit $LASTEXITCODE
}

# Fallback directly to Docker Compose if Python is not present
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Set-Location $ProjectRoot
    if (-not (Test-Path "$ProjectRoot\.env") -and (Test-Path "$ProjectRoot\.env.example")) {
        Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
        Write-Host "Initialized .env configuration from .env.example" -ForegroundColor Yellow
    }

    if ($Stop) {
        docker compose down
        exit $LASTEXITCODE
    }

    if ($Status) {
        docker compose ps
        exit $LASTEXITCODE
    }

    Write-Host "Building and launching containers via Docker Compose..." -ForegroundColor Green
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker Compose failed to start services."
    }

    Start-Sleep -Seconds 3
    Write-Host "`nPortfolioIQ is running!" -ForegroundColor Green
    Write-Host "  Frontend: http://localhost:8080" -ForegroundColor Cyan
    Write-Host "  Backend:  http://localhost:5000/api/v1/health`n" -ForegroundColor Cyan

    if (-not $NoBrowser) {
        Start-Process "http://localhost:8080/index.html"
    }

    if (-not $Detach) {
        Write-Host "Streaming live container logs (Ctrl+C to detach):`n" -ForegroundColor Yellow
        docker compose logs -f
    }
} else {
    Write-Error "Neither Python nor Docker was found in PATH."
}
