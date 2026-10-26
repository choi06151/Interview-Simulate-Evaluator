@echo off
setlocal
cd /d "%~dp0"
if exist ".audio-venv\Scripts\pythonw.exe" (
  start "" ".audio-venv\Scripts\pythonw.exe" interview_app.py
) else (
  start "" pythonw interview_app.py
)
endlocal
