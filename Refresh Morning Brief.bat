@echo off
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo No .venv found in this folder. See README.md, "Setup".
  pause
  exit /b 1
)
"%PYTHON%" "%~dp0morning_terminal.py" %*
pause
