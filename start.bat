@echo off
:: Thin wrapper: run.py handles .env, Docker, migrations and the browser.
where python >nul 2>nul && (python "%~dp0run.py" %* & exit /b) || (py "%~dp0run.py" %* & exit /b)
