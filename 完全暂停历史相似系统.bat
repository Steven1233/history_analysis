@echo off
setlocal
title History Similarity Retrieval System - FULL STOP

rem ==========================================================================
rem  FULL STOP - shuts the local web service down completely.
rem
rem   1. locates the running service: the pid recorded by the START script in
rem      .histsim\run\server.json, plus a netstat lookup over ports
rem      8770..8789 as a fallback (covers services started by hand or by the
rem      old foreground launcher, which leaves no run record)
rem   2. refuses to kill a service that is executing searches unless you
rem      confirm it, or pass "force"
rem   3. ends the service AND its parallel IO worker subprocesses (/T tree
rem      kill), otherwise orphan workers would keep file handles open
rem   4. verifies the ports really stopped answering, removes the run record
rem
rem  Usage:  double-click this file         (asks before dropping live searches)
rem          <this file> force             (no question, stop anyway)
rem          <this file> force 9000        (look for the service from port 9000)
rem          <this file> 9000              (same, only the fallback port)
rem
rem  Nothing is deleted: data source registrations, the slice cache and all
rem  finished results stay in .histsim. Run the START script again to resume.
rem
rem  NOTE: this file is intentionally ASCII-only (see the START script header
rem  for why). All Chinese messages are printed by Python.
rem ==========================================================================

cd /d "%~dp0"

set "FORCE="
set "PORT="
if /i "%~1"=="force" set "FORCE=--force"
if /i "%~1"=="-f" set "FORCE=--force"
if /i "%~1"=="--force" set "FORCE=--force"
if defined FORCE (
  set "PORT=%~2"
) else (
  set "PORT=%~1"
)
set "ARGS="
if defined FORCE set "ARGS=%ARGS% --force"
if not "%PORT%"=="" set "ARGS=%ARGS% --port %PORT%"
set "PYTHONDONTWRITEBYTECODE=1"

echo ============================================================
echo   History Similarity Retrieval System - FULL STOP
echo ============================================================

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

rem No dependency check here on purpose: stopping the service only needs the
rem standard library, so it still works if numpy/netCDF4/matplotlib broke.
echo   interpreter  %PY%
echo   version      %PYVER%
echo.

%PY% -m histsim.cli stop%ARGS%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo   [WARN] full stop exited with code %RC% (see above).
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