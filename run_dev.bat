@echo off
REM Local development server - separate database (lighting_boq_dev), separate port
REM (8001), --reload for fast iteration. Use this instead of run.bat while building new
REM features so nothing you're actively working on (including a half-finished schema
REM change) ever touches the database the team's live QuotationTool service depends on.
REM
REM For full isolation (so an in-progress *code* change also can't affect the live service
REM on its next restart) work from a SEPARATE checkout too - see HOW_TO_RUN.md's
REM "Developing alongside a live deployment" section for the one-time git worktree command.
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in (".env.development") do set "%%A=%%B"
echo Starting DEV server (database: lighting_boq_dev) at http://127.0.0.1:8001
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
