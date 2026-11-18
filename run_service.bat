@echo off
REM Production entry point, used by the QuotationTool Windows service (registered via
REM setup_production_services.ps1) - NOT what you double-click for local dev. Differs from
REM run.bat deliberately: no --reload (a service shouldn't restart itself on every file save),
REM no seed.py (unnecessary on every service start/restart - run it by hand if you ever need
REM it), and reads its config from .env.production instead of relying on inherited user
REM environment variables, since a Windows service doesn't run in this user's login session.
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in (".env.production") do set "%%A=%%B"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
