@echo off
setlocal
title History Similarity Retrieval System

rem ==========================================================================
rem  History Similarity Retrieval System - quick launcher
rem  Usage: double-click this file, or   <this file> 9000   (9000 = custom port)
rem  No install / no build: a pure Python-stdlib local web service.
rem
rem  NOTE: this file is intentionally ASCII-only. cmd.exe parses .bat files
rem  with the active OEM code page, so non-ASCII text here would corrupt the
rem  script on some machines. All Chinese UI text is printed by Python.
rem ==========================================================================

cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8770"
set "PYTHONDONTWRITEBYTECODE=1"

echo ============================================================
echo   History Similarity Retrieval System - quick start
echo ============================================================

rem Pick the first candidate that really answers with a "Python 3.x" version.
rem This rejects the Microsoft Store placeholder (findable by where.exe but
rem it only opens the Store when executed).
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
echo   port         %PORT%
echo.
echo   Starting local service, the browser will open automatically...
echo   Press Ctrl+C in this window to stop the service.
echo ============================================================
echo.

%PY% -m histsim.web.server --port %PORT%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo   [WARN] service exited with code %RC%
echo   Service stopped.
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