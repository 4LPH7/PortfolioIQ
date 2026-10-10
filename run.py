#!/usr/bin/env python3
"""
PortfolioIQ — One-Command Self-Hosted Full-Stack Launcher
Starts PostgreSQL, Backend REST API, and Frontend Dashboard in one command.

Usage:
    python run.py             # Build & start full stack in Docker, open dashboard
    python run.py -d          # Start in detached background mode
    python run.py --stop      # Stop all running containers
    python run.py --local     # Run locally without Docker (Python + local Postgres)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def print_banner():
    banner = """
======================================================================
  PortfolioIQ — Self-Hosted Institutional Portfolio Intelligence
======================================================================
"""
    print(banner)


def ensure_env_file():
    env_file = ROOT_DIR / ".env"
    env_example = ROOT_DIR / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
        print("[INFO] Initialized .env configuration from .env.example")


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
    compose_cmd = ["docker", "compose", "up", "--build"]
    if detached:
        compose_cmd.append("-d")

    if detached:
        ret = subprocess.run(compose_cmd, cwd=ROOT_DIR, check=False)
        if ret.returncode != 0:
            print("[ERROR] Docker Compose failed to start services.")
            sys.exit(ret.returncode)

        print("\nWaiting for Backend REST API to become ready...")
        wait_for_backend(timeout=25)

        print("\n======================================================================")
        print("  PortfolioIQ is running!")
        print("  Frontend Dashboard: http://localhost:8080")
        print("  Backend REST API:   http://localhost:5000")
        print("  Health Status:      http://localhost:5000/api/v1/health")
        print("======================================================================")
        print("\nView container logs: docker compose logs -f")
        print("Stop services:       python run.py --stop  (or docker compose down)\n")

        if open_browser:
            time.sleep(1)
            webbrowser.open("http://localhost:8080/index.html")
    else:
        # Start detached first, open browser, then stream logs
        start_cmd = ["docker", "compose", "up", "--build", "-d"]
        ret = subprocess.run(start_cmd, cwd=ROOT_DIR, check=False)
        if ret.returncode != 0:
            print("[ERROR] Docker Compose failed to start services.")
            sys.exit(ret.returncode)

        print("\nWaiting for Backend REST API to become ready...")
        wait_for_backend(timeout=25)

        print("\n======================================================================")
        print("  PortfolioIQ is running!")
        print("  Frontend Dashboard: http://localhost:8080")
        print("  Backend REST API:   http://localhost:5000")
        print("  Health Status:      http://localhost:5000/api/v1/health")
        print("======================================================================")

        if open_browser:
            webbrowser.open("http://localhost:8080/index.html")

        print("\nStreaming live container logs (Press Ctrl+C to detach):")
        try:
            subprocess.run(["docker", "compose", "logs", "-f"], cwd=ROOT_DIR, check=False)
        except KeyboardInterrupt:
            print("\nDetached from container logs. Containers are still running in background.")
            print("To stop services, run: python run.py --stop")


def stop_docker():
    print("Stopping PortfolioIQ Docker containers...")
    subprocess.run(["docker", "compose", "down"], cwd=ROOT_DIR, check=False)
    print("Containers stopped successfully.")


def run_local(open_browser: bool = True):
    print("[Mode: Local Python Development without Docker]")
    ensure_env_file()

    # Run database migrations
    print("Applying database migrations...")
    python_bin = sys.executable
    subprocess.run(
        [python_bin, str(ROOT_DIR / "db" / "run_migrations.py")], cwd=ROOT_DIR, check=False
    )

    print("\nStarting unified Flask server (serving API + Frontend) on http://localhost:5000...")
    if open_browser:
        time.sleep(1)
        webbrowser.open("http://localhost:5000/")

    os.environ.setdefault("RUN_INLINE_SCHEDULER", "true")
    os.environ.setdefault("PORTFOLIOIQ_API_KEY", "portfolioiq_default_local_key")
    subprocess.run([python_bin, str(ROOT_DIR / "flask_app.py")], cwd=ROOT_DIR, check=False)


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="PortfolioIQ — One-Command Full-Stack Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d", "--detach", action="store_true", help="Run containers in the background and exit"
    )
    parser.add_argument(
        "--stop", "--down", dest="stop", action="store_true", help="Stop all running containers"
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

    open_browser = not args.no_browser

    if args.local:
        run_local(open_browser=open_browser)
        return

    if is_docker_available():
        run_docker(detached=args.detach, open_browser=open_browser)
    else:
        print("[WARNING] Docker daemon is not running or not in PATH.")
        print("Falling back to local development mode...")
        run_local(open_browser=open_browser)


if __name__ == "__main__":
    main()
