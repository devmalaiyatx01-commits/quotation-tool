@echo off
REM Local server on the REAL local database (lighting_boq), port 8000.
REM DATABASE_URL comes from the Windows user environment; everything optional comes from
REM .env below. run_dev.bat is the sandbox equivalent - separate database, port 8001.
cd /d "%~dp0"
if not exist data (mkdir data)

REM Optional local settings, chief among them the Groq key for the Match tab's model
REM fallback. Same mechanism run_dev.bat uses for .env.development and the Oracle server
REM uses for its own .env, so there is one pattern to remember rather than three.
REM
REM `eol=#` skips comment lines. Anything set here overrides the Windows environment for
REM this window only - nothing is written to the registry and nothing leaves the machine.
REM .env is gitignored, so a key placed there cannot be committed by accident.
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)

REM Say plainly whether the model fallback is on. Without this the only way to find out is
REM to open the Match tab and wonder - and the whole design is that it fails silently, so
REM "off" and "broken" look identical from the outside.
if defined GROQ_API_KEY (
  if defined LLM_EXTRACT_ENABLED (
    echo   Match tab: model fallback ON
  ) else (
    echo   Match tab: key present but LLM_EXTRACT_ENABLED is not set - fallback OFF
  )
) else (
  echo   Match tab: model fallback OFF ^(regex extraction only - the normal path^)
)

REM Nothing below is allowed to fail silently. Double-clicked, this window closes the instant
REM the script ends - so an error printed on the way out is gone before it can be read, and
REM the only symptom is "run.bat doesn't work". Every exit path from here on either keeps the
REM window open or says plainly what happened.

REM By far the most common cause: something is already serving port 8000, so uvicorn cannot
REM bind and quits immediately. Usually a previous run.bat window still open behind this one,
REM or a server left running by something else.
REM Written with labels rather than an if(...) block on purpose: a variable set inside a
REM parenthesised block needs delayed expansion to read back, and enabling that globally would
REM mangle any .env value containing an exclamation mark.
set "PORTPID="
for /f "tokens=5" %%P in ('%SystemRoot%\System32\netstat.exe -ano ^| %SystemRoot%\System32\findstr.exe /c:"127.0.0.1:8000" ^| %SystemRoot%\System32\findstr.exe /c:"LISTENING"') do set "PORTPID=%%P"
if not defined PORTPID goto :portfree

echo.
echo   Port 8000 is already in use by process %PORTPID%.
echo   That is why the server could not start - only one program can hold a port.
echo.
echo   If you already have this tool running in another window, use that one instead:
echo   http://127.0.0.1:8000
echo.
set "STOPIT="
set /p "STOPIT=  Otherwise, stop process %PORTPID% and start fresh? [y/N]: "
if /i not "%STOPIT%"=="y" goto :leftalone
%SystemRoot%\System32\taskkill.exe /f /pid %PORTPID%
REM netstat names the socket's owner, which is not always a process that still exists: a
REM server killed while a worker of its own outlived it leaves the port held by a PID Windows
REM already considers gone, and taskkill then fails. Worth saying, because the obvious reading
REM of "process not found" is that the port is now free when it is not.
if errorlevel 1 (
  echo.
  echo   Could not stop process %PORTPID% - Windows says it no longer exists, yet the port
  echo   is still held. That means a server was stopped but left a worker process behind.
  echo.
  echo   Close any other Python windows, or run this once in PowerShell:
  echo       Get-Process python ^| Stop-Process -Force
  echo.
  pause
  exit /b 1
)
REM The socket takes a moment to be released before uvicorn can claim it.
%SystemRoot%\System32\ping.exe -n 3 127.0.0.1 >nul
goto :portfree

:leftalone
echo.
echo   Left it running - nothing was started.
echo.
pause
exit /b 1

:portfree

python seed.py
if errorlevel 1 (
  echo.
  echo   seed.py failed - the message above says why. The server was not started.
  echo   A database that is not running is the usual reason.
  echo.
  pause
  exit /b 1
)

echo Starting server at http://127.0.0.1:8000
echo   To stop it: double-click stop.bat, or press Ctrl+C here and answer Y.
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

REM --reload runs the server in a second process that watches for file changes, and that worker
REM does not reliably die with the window - Ctrl+C answered at cmd's "Terminate batch job
REM (Y/N)?" prompt, or the X button, can leave it holding port 8000 with no window to show for
REM it, and the next run.bat then refuses to start. Sweeping it here means stopping the server
REM the obvious way is enough, whichever way that was.
for /f "tokens=5" %%P in ('%SystemRoot%\System32\netstat.exe -ano ^| %SystemRoot%\System32\findstr.exe /c:"127.0.0.1:8000" ^| %SystemRoot%\System32\findstr.exe /c:"LISTENING"') do (
  echo.
  echo   Releasing port 8000 (leftover worker process %%P^)...
  %SystemRoot%\System32\taskkill.exe /f /t /pid %%P >nul 2>&1
)

REM `timeout`, not `pause`: an error is worth reading, but a window that waits forever for a
REM keypress is a window the user reports as "won't close". This one closes itself, and any
REM key closes it sooner.
if errorlevel 1 (
  echo.
  echo   The server stopped. If that was unexpected, the message above says why.
  echo   This window closes on its own in 20 seconds - or press any key.
  REM 2>nul: timeout refuses a redirected stdin, which only happens when the output is being
  REM piped to a file. There the countdown is pointless anyway - swallow the complaint rather
  REM than end a working run with a red ERROR line.
  %SystemRoot%\System32\timeout.exe /t 20 2>nul
)
