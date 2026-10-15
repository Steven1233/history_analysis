@echo off
setlocal
title History Similarity Retrieval System - FULL START

rem ==========================================================================
rem  FULL START - brings the whole system up and leaves it running in the
rem  background, so this window can be closed afterwards.
rem
rem   1. locates a usable Python 3 interpreter (the Microsoft Store stub is
rem      rejected, it only opens the Store when executed)
rem   2. verifies the runtime dependencies (numpy / netCDF4 / matplotlib)
rem   3. starts the local web service on port 8770, or the first free port
rem      after it, detached from this window
rem   4. waits for the health check, prints the status banner, opens browser
rem   5. records pid + port in .histsim\run\server.json
rem
rem  If the service is already running, nothing is started twice: the script
rem  only reports the existing address and opens the browser.
rem
rem  Usage:  double-click this file         (default port 8770)
rem          <this file> 9000               (start looking from port 9000)
rem
rem  To shut it down completely, run the STOP script that was delivered next
rem  to this file. It ends the service and its parallel IO worker processes.
rem
rem  NOTE: this file is intentionally ASCII-only. cmd.exe parses .bat files
rem  with the active OEM code page, so non-ASCII text here would corrupt the
rem  script on some machines. All Chinese messages are printed by Python.
rem ==========================================================================

cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8770"
set "PYTHONDONTWRITEBYTECODE=1"

echo ============================================================
echo   History Similarity Retrieval System - FULL START
echo ============================================================

rem Pick the first candidate that really answers with a "Python 3.x" version.
set "PY="
set "PYVER="
call :try_py python
if not defined PY call :try_py C:\Python314\python.exe
if not defined PY call :try_py C:\Python313\python.exe
if not defined PY call :try_py C:\Python312\python.exe
if not defined PY call :try_py C:\Python311\python.exe
if not defined PY (
  echo   [ERROR] No usable Python 3 interpreter found.
  echo           Install Python 3.10+ and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

echo   interpreter  %PY%
echo   version      %PYVER%

%PY% -c "import numpy, netCDF4, matplotlib" >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [ERROR] Missing runtime dependencies. Run this first:
  echo.
  echo           %PY% -m pip install numpy netCDF4 matplotlib
  echo.
  pause
  exit /b 1
)
echo   deps         numpy / netCDF4 / matplotlib OK
echo   start port   %PORT%
echo.

%PY% -m histsim.cli serve --port %PORT%
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo   The service is running in the background; this window can be closed.
  echo   To stop it completely, run the STOP script next to this file.
) else (
  echo   [WARN] full start failed with exit code %RC%.
  echo          See .histsim\run\server.err.log for the details.
)
echo.
pause
exit /b %RC%

rem ==========================================================================
rem  Subroutine: accept a candidate only if it prints a "Python 3.x" version.
rem ==========================================================================
:try_py
set "CAND=%~1"
if /i not "%CAND%"=="python" if not exist "%CAND%" exit /b 0
set "OUT="
for /f "delims=" %%v in ('"%CAND%" --version 2^>^&1') do set "OUT=%%v"
echo %OUT% | findstr /b "Python 3" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%CAND%"
set "PYVER=%OUT%"
exit /b 0