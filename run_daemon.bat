@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\ai-news-agent.exe" (
  echo Virtual environment not found.
  echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[dev]"
  pause
  exit /b 1
)

echo Starting AI News Agent daemon...
echo Keep this window open. The schedule is read from SCHEDULE_CRON in .env.
".venv\Scripts\ai-news-agent.exe" daemon
pause
