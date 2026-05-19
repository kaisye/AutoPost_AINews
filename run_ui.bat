@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\ai-news-agent.exe" (
  echo Virtual environment not found.
  echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[dev]"
  pause
  exit /b 1
)

echo Starting AI News Agent UI...
echo Open http://127.0.0.1:8787 in your browser.
".venv\Scripts\ai-news-agent.exe" ui --host 127.0.0.1 --port 8787
pause
