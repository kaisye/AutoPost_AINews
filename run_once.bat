@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\ai-news-agent.exe" (
  echo Virtual environment not found.
  echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[dev]"
  pause
  exit /b 1
)

echo Running AI News Agent once...
".venv\Scripts\ai-news-agent.exe" run-once
echo.
echo Done.
pause
