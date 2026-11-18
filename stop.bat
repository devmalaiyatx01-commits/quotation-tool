@echo off
REM Stops the local server started by run.bat. Double-click it - no typing.
REM
REM Why this exists: run.bat runs uvicorn with --reload, which watches for file changes from a
REM second process. Ctrl+C in the window makes cmd.exe ask "Terminate batch job (Y/N)?", and
REM answering it does not reliably take the reloader's own worker with it - so the window looks
REM closed while a python process is still holding port 8000, and the next run.bat refuses to
REM start. Closing the window with the X button has the same problem.
REM
REM Rather than explain that, this kills whatever is actually holding the port, and its whole
REM process tree, which is the only thing that reliably frees it.
cd /d "%~dp0"

set "FOUND="
for /f "tokens=5" %%P in ('%SystemRoot%\System32\netstat.exe -ano ^| %SystemRoot%\System32\findstr.exe /c:"127.0.0.1:8000" ^| %SystemRoot%\System32\findstr.exe /c:"LISTENING"') do set "FOUND=%%P"

if not defined FOUND (
  echo.
  echo   Nothing is running on port 8000 - the server is already stopped.
  echo.
  %SystemRoot%\System32\timeout.exe /t 6 >nul 2>&1
  exit /b 0
)

echo.
echo   Stopping the Quotation Tool server (process %FOUND%)...
REM /T takes the child processes too - the reloader's worker is a separate process, and it is
REM the one that actually holds the socket.
%SystemRoot%\System32\taskkill.exe /f /t /pid %FOUND% >nul 2>&1

REM The socket lingers for a moment after the process goes.
%SystemRoot%\System32\ping.exe -n 3 127.0.0.1 >nul

set "STILL="
for /f "tokens=5" %%P in ('%SystemRoot%\System32\netstat.exe -ano ^| %SystemRoot%\System32\findstr.exe /c:"127.0.0.1:8000" ^| %SystemRoot%\System32\findstr.exe /c:"LISTENING"') do set "STILL=%%P"

if defined STILL (
  echo.
  echo   Port 8000 is still held by process %STILL%.
  echo   Run this again, or stop any remaining Python windows from Task Manager.
  echo.
  pause
  exit /b 1
)

echo   Stopped. Port 8000 is free - run.bat will start cleanly.
echo.
%SystemRoot%\System32\timeout.exe /t 6 >nul 2>&1
exit /b 0
