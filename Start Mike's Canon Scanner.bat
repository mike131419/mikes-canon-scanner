@echo off
cd /d "%~dp0"
title Mike's Canon Scanner
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python is not installed. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause & exit /b 1
)
if not exist ".deps_ok" (
  echo First run: installing Pillow and pywin32...
  %PY% -m pip install --user -r requirements.txt || (pause & exit /b 1)
  echo ok> .deps_ok
)
start "" %PY%w mikes_canon_scanner.py
