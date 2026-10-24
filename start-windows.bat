@echo off
rem Double-click to start PhotoStory on Windows. The first run sets things up (a minute or two).
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || (echo Python 3 is needed: install it from https://www.python.org/downloads/ & pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\pip install -r requirements.txt || (echo Setup failed, see above. & pause & exit /b 1)
)
.venv\Scripts\python -m photostory
pause
