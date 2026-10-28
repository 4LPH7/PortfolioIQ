#!/usr/bin/env bash
# Thin wrapper: run.py handles .env, Docker, migrations and the browser.
cd "$(dirname "${BASH_SOURCE[0]}")"
exec "$(command -v python3 || command -v python)" run.py "$@"
