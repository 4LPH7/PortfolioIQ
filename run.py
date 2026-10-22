#!/usr/bin/env python3
"""
PortfolioIQ — One-Command Self-Hosted Full-Stack Launcher
Starts PostgreSQL, Backend REST API, and Frontend Dashboard in one command.

Usage:
    python run.py             # Build & start full stack in Docker, open dashboard
    python run.py -d          # Start in detached background mode
    python run.py --stop      # Stop all running containers
    python run.py --status    # Check status of running services
    python run.py --local     # Run locally without Docker (Python + local Postgres)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_API_KEY = "portfolioiq_default_local_key"  # same fallback as docker-compose.yml
KEY_VAR = "PORTFOLIOIQ_API_KEY"


def print_banner():
    banner = """
======================================================================
  PortfolioIQ -- Self-Hosted Institutional Portfolio Intelligence
======================================================================
"""
    print(banner)


def ensure_env_file():
    """First run only: copy .env.example to .env and give this install its own unlock key."""
    env_file = ROOT_DIR / ".env"
    env_example = ROOT_DIR / ".env.example"
    if env_file.exists() or not env_example.exists():
        return
    key_line = f"{KEY_VAR}={secrets.token_urlsafe(24)}"
    lines = env_example.read_text(encoding="utf-8").splitlines()
    lines = [key_line if line.startswith(f"{KEY_VAR}=") else line for line in lines]
    if key_line not in lines:
        lines.append(key_line)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("[INFO] Created .env from .env.example with a new random unlock key")


def read_unlock_key() -> str:
    """Key the dashboard asks for: the .env value, else the docker-compose fallback."""
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{KEY_VAR}="):
                value = line.split("=", 1)[1].split(" #", 1)[0].strip().strip("\"'")
                if value:
                    return value
    return DEFAULT_API_KEY


def print_ready_banner(dashboard_url: str):
    print("\n======================================================================")
    print("  PortfolioIQ is running!")
    print(f"  Dashboard:  {dashboard_url}")
    print("  Health:     http://localhost:5000/api/v1/health")
    print(f"  Unlock key: {read_unlock_key()}   ({KEY_VAR} in .env)")
    print("======================================================================")


def get_python_executable() -> str:
    """Find the best Python interpreter (virtual environment if available)."""
    candidates = [
        ROOT_DIR / ".venv" / "env" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "bin" / "python",
        ROOT_DIR / "venv" / "Scripts" / "python.exe",
        ROOT_DIR / "venv" / "bin" / "python",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently open and bound on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def check_port_conflicts():
    """Warn early if key application ports are occupied by something other than this stack."""
    ours = subprocess.run(
        ["docker", "compose", "ps", "-q"], cwd=ROOT_DIR, capture_output=True, text=True, check=False
    )
    if ours.stdout.strip():
        return  # our own containers hold the ports; `up` will just recreate them
    ports_to_check = [(8080, "Frontend Dashboard"), (5000, "Backend REST API")]
    conflicts = []
    for port, label in ports_to_check:
        if is_port_in_use(port):
            conflicts.append((port, label))
    if conflicts:
        print("[WARNING] The following ports are already in use on your system:")
        for port, label in conflicts:
            print(f"  - Port {port} ({label})")
        print("Existing containers or applications might need to be stopped if conflicts occur.\n")


def is_docker_available() -> bool:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def wait_for_backend(url: str = "http://localhost:5000/api/v1/health", timeout: int = 30) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def run_docker(detached: bool = False, open_browser: bool = True):
    print("Building and starting PortfolioIQ containers with Docker Compose...")
    check_port_conflicts()

    ret = subprocess.run(["docker", "compose", "up", "--build", "-d"], cwd=ROOT_DIR, check=False)
    if ret.returncode != 0:
        print("[ERROR] Docker Compose failed to start services.")
        sys.exit(ret.returncode)

    print("\nWaiting for Backend REST API to become ready...")
    if not wait_for_backend(timeout=25):
        print("[WARNING] Backend not healthy yet. Check: docker compose logs backend")

    print_ready_banner("http://localhost:8080")

    if open_browser:
        webbrowser.open("http://localhost:8080/index.html")

    if detached:
        print("\nView container logs: docker compose logs -f")
        print("Check status:        python run.py --status")
        print("Stop services:       python run.py --stop  (or docker compose down)\n")
    else:
        print("\nStreaming live container logs (Press Ctrl+C to detach):")
        try:
            subprocess.run(["docker", "compose", "logs", "-f"], cwd=ROOT_DIR, check=False)
        except KeyboardInterrupt:
            print("\nDetached from container logs. Containers are still running in background.")
            print("Check status: python run.py --status")
            print("To stop services: python run.py --stop")


def stop_docker():
    print("Stopping PortfolioIQ Docker containers...")
    subprocess.run(["docker", "compose", "down"], cwd=ROOT_DIR, check=False)
    print("Containers stopped successfully.")


def print_status():
    print("Checking PortfolioIQ Service Status...\n")
    if is_docker_available():
        print("Docker Containers:")
        subprocess.run(
            ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}\t{{.Ports}}"],
            cwd=ROOT_DIR,
            check=False,
        )
        print()

    # Health probe
    print("Probing Local Endpoints:")
    endpoints = [
        ("Frontend Dashboard", "http://localhost:8080"),
        ("Backend Health Probe", "http://localhost:5000/api/v1/health"),
        ("Market Status Probe", "http://localhost:5000/api/v1/market/status"),
    ]
    for label, url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json, text/html"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                code = resp.getcode()
                body = resp.read().decode("utf-8", errors="replace")
                detail = f"HTTP {code}"
                if "application/json" in resp.headers.get("Content-Type", ""):
                    try:
                        parsed = json.loads(body)
                        if "status" in parsed:
                            detail += f" (status: {parsed['status']})"
                        if "db" in parsed:
                            detail += f" (db: {'OK' if parsed['db'] else 'offline'})"
                    except Exception:
                        pass
                print(f"  [ONLINE]  {label:<24} -> {detail} ({url})")
        except Exception as exc:
            print(f"  [OFFLINE] {label:<24} -> {exc} ({url})")
    print()


def run_local(open_browser: bool = True):
    print("[Mode: Local Python Development without Docker]")

    python_bin = get_python_executable()
    print(f"Using Python: {python_bin}")

    print("Applying database migrations...")
    migrate = subprocess.run(
        [python_bin, str(ROOT_DIR / "db" / "run_migrations.py")], cwd=ROOT_DIR, check=False
    )
    if migrate.returncode != 0:
        print("[ERROR] Migrations failed. Check DATABASE_URL in .env points at a running Postgres.")
        sys.exit(migrate.returncode)

    print_ready_banner("http://localhost:5000")
    if open_browser:
        webbrowser.open("http://localhost:5000/")

    os.environ.setdefault("RUN_INLINE_SCHEDULER", "true")
    # An env var beats .env in pydantic-settings, so pass the .env key, never a bare default.
    os.environ.setdefault(KEY_VAR, read_unlock_key())
    subprocess.run([python_bin, str(ROOT_DIR / "flask_app.py")], cwd=ROOT_DIR, check=False)


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="PortfolioIQ -- One-Command Full-Stack Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d", "--detach", action="store_true", help="Run containers in the background and exit"
    )
    parser.add_argument(
        "--stop", "--down", dest="stop", action="store_true", help="Stop all running containers"
    )
    parser.add_argument(
        "-s",
        "--status",
        action="store_true",
        help="Check status of running containers and endpoints",
    )
    parser.add_argument(
        "--local", action="store_true", help="Run locally using Python and native Postgres"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the dashboard in browser automatically",
    )

    args = parser.parse_args()

    ensure_env_file()

    if args.stop:
        stop_docker()
        return

    if args.status:
        print_status()
        return

    open_browser = not args.no_browser

    if args.local:
        run_local(open_browser=open_browser)
        return

    if is_docker_available():
        run_docker(detached=args.detach, open_browser=open_browser)
    elif shutil.which("docker"):
        print("[ERROR] Docker is installed but not running. Start Docker Desktop and re-run.")
        print("        (Or use `python run.py --local` with your own PostgreSQL.)")
        sys.exit(1)
    else:
        print("[WARNING] Docker not found. Falling back to local mode (needs PostgreSQL).")
        run_local(open_browser=open_browser)


if __name__ == "__main__":
    main()
