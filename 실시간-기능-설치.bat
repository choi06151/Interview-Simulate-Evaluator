@echo off
setlocal
cd /d "%~dp0"
if exist ".audio-venv\Scripts\python.exe" (
  set "PYTHON=.audio-venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)
"%PYTHON%" -m pip install -r requirements-live.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Check Python and network access, then try again.
  pause
  exit /b 1
)
echo.
echo Live interview dependencies are ready.
where ollama >nul 2>&1
if errorlevel 1 (
  echo.
  echo Install Ollama for Windows, then reopen this script to download the local answer model.
  echo Default model: qwen3.5:4b
) else (
  echo Downloading the local answer model qwen3.5:4b...
  ollama pull qwen3.5:4b
  if errorlevel 1 echo Model download failed. Run "ollama pull qwen3.5:4b" after checking Ollama and network access.
)
pause
endlocal
