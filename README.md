# AI News Agent

An end-to-end Agentic AI workflow that discovers high-impact AI news, ranks relevant articles, writes a deep Facebook analysis post, routes the draft through Telegram approval, prevents duplicate publishing, and optionally publishes to a Facebook Page.

Vietnamese documentation: [README_VI.md](README_VI.md)

The project is designed as a portfolio-grade Agentic AI system: it is not just a single LLM prompt. It combines workflow orchestration, external tools, memory, human approval, scheduling, UI configuration, and production-oriented safeguards.

## Features

- Multi-step LangGraph workflow.
- AI news collection from RSS, Hacker News, Tavily, and NewsAPI.
- Impact ranking based on recency, engagement, relevance, and novelty.
- Deep Vietnamese Facebook post generation.
- Optional article image metadata extraction for Facebook photo posts.
- Telegram approval workflow with `APPROVE`, `REJECT`, and `EDIT` commands.
- Auto-approval after a configurable timeout.
- Optional Facebook Page publishing through the Facebook Graph API.
- SQLite memory for article fingerprints, post history, feedback, and audit logs.
- Duplicate prevention by canonical URL and content similarity.
- Admin UI for running the workflow, scheduling posts, updating configuration, and viewing history.
- Test suite and linting setup.

## Default LLM Provider

The default configuration uses NVIDIA NIM through an OpenAI-compatible API:

- `LLM_PROVIDER=nvidia`
- `OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1`
- `OPENAI_MODEL=openai/gpt-oss-120b`
- `NVIDIA_API_KEY`

You can switch back to OpenAI native mode by setting:

- `LLM_PROVIDER=openai`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Note: `openai/gpt-oss-120b` is a text model. It does not generate images directly. The current workflow uses image metadata from source articles when available.

## Architecture

```mermaid
flowchart LR
  A["Load Memory"] --> B["Collect News"]
  B --> C["Enrich Articles"]
  C --> D["Rank Impact"]
  D --> E["Select Ranked Articles"]
  E --> F["Draft Deep Analysis Post"]
  F --> G["Check Duplicate"]
  G -->|fresh| H["Send Telegram Approval"]
  G -->|duplicate| K["Persist Run Memory"]
  H --> I["Wait Approval"]
  I -->|approved| J["Optional Facebook Publish"]
  I -->|rejected/edit| L["Store Feedback or Revise"]
  J --> K["Persist Run Memory"]
  L --> K
```

## Memory Layers

- `LangGraph checkpoint`: keeps execution state by `thread_id`.
- `SQLite domain memory`: stores article fingerprints, post history, approval status, Telegram feedback, and Facebook post IDs.
- `Prompt memory`: injects recent posts into the LLM prompt to reduce repeated angles and wording.

## Duplicate Prevention

The workflow prevents duplicate publishing through three layers:

- Canonical URL filtering: previously posted source URLs are filtered before drafting, even when a new URL includes tracking parameters such as `utm_source`.
- Content similarity check: after the LLM creates a draft, the workflow compares it with the 20 most recent posts. If it is too similar, the run is stored as `skipped_duplicate` and the post is not sent to Telegram or Facebook.
- Prompt memory: recent posts are passed into the prompt so the model avoids repeating prior angles.

## Requirements

- Python 3.11+
- Telegram bot token and approver chat ID
- NVIDIA API key or OpenAI API key
- Optional Facebook Page ID and Page Access Token

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

Fill in at least:

```env
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=
OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
OPENAI_MODEL=openai/gpt-oss-120b

TELEGRAM_BOT_TOKEN=
TELEGRAM_APPROVER_CHAT_ID=
```

Optional Facebook publishing:

```env
FACEBOOK_ENABLED=true
FACEBOOK_PAGE_ID=
FACEBOOK_PAGE_ACCESS_TOKEN=
```

## Run Once

```powershell
ai-news-agent run-once
```

This runs the full workflow once:

1. Collect AI news.
2. Enrich article metadata.
3. Rank impact.
4. Select articles after ranking.
5. Write a Facebook draft.
6. Check duplicates.
7. Send to Telegram for approval.
8. Publish to Facebook if enabled and approved.
9. Persist memory.

Quick Windows launcher:

```text
run_once.bat
```

## Run the Admin UI

```powershell
ai-news-agent ui
```

Open:

```text
http://127.0.0.1:8787
```

The UI supports:

- Manual workflow runs.
- Daily posting schedule.
- LLM provider/model/base URL configuration.
- API key and token management for NVIDIA, OpenAI, Tavily, NewsAPI, Telegram, and Facebook.
- News lookback, candidate count, and selected article count.
- Telegram approval timeout and auto-approval.
- Facebook Page publishing settings.
- Reposting from recent post memory.
- Light/dark mode and theme color.
- Run status and recent post memory.

Secret fields are password inputs. Leave a secret field blank to keep the existing value in `.env`.

Quick Windows launcher:

```text
run_ui.bat
```

## Run Automation

```powershell
ai-news-agent daemon
```

`SCHEDULE_CRON` uses a standard 5-field cron expression. Example:

```env
SCHEDULE_CRON=25 3 * * *
```

This runs every day at 03:25 according to the machine timezone.

You can also run on an interval instead of a fixed daily time:

```env
SCHEDULE_MODE=interval
SCHEDULE_INTERVAL_HOURS=2
SCHEDULE_INTERVAL_MINUTES=30
```

This runs every 2 hours and 30 minutes. Use `SCHEDULE_MODE=cron` to return to the daily/cron schedule.

Important: the scheduler only runs while the UI or daemon process is alive. For production, run it with a service manager such as Windows Task Scheduler, systemd, Docker, or a cloud worker.

Quick Windows launcher:

```text
run_daemon.bat
```

Keep the daemon window open. Closing it stops the scheduled automation.

## Repost from Memory

You can repost a previous memory item from the Admin UI. Each recent post has:

- `Rewrite & repost`: enter a rewrite instruction and let the model rewrite the post before publishing.
- `Repost as is`: publish the stored post text unchanged.

Facebook publishing must be enabled.
If the original post was stored with an image URL, reposting will publish with that image again.

You can also repost from the CLI:

```powershell
ai-news-agent repost --post-id 3
```

To rewrite before reposting:

```powershell
ai-news-agent repost --post-id 3 --rewrite "Make it sharper, less formal, and more founder-focused"
```

The repost is written back to SQLite memory with feedback such as `Reposted from post #3`, including the stored image URL when available, so the audit trail remains clear.

## Telegram Approval

The bot sends a draft with instructions. Reply to the Telegram message with:

- `APPROVE` to approve and continue.
- `REJECT: reason` to reject and store feedback.
- `EDIT: requested changes` to ask the workflow to revise the draft.

If auto-approval is enabled and the timeout expires, the workflow treats the draft as approved.

## Testing

```powershell
pytest
ruff check .
```

Current coverage focuses on:

- LLM response parsing.
- Scoring logic.
- Telegram approval parsing.
- Telegram timeout behavior.
- Memory and duplicate prevention.

## Key Files

- `src/ai_news_agent/workflow.py`: LangGraph workflow.
- `src/ai_news_agent/news.py`: news collection, enrichment, ranking.
- `src/ai_news_agent/llm.py`: LLM post writer and revision logic.
- `src/ai_news_agent/memory.py`: SQLite memory and duplicate checks.
- `src/ai_news_agent/telegram.py`: Telegram approval client.
- `src/ai_news_agent/facebook.py`: Facebook Page publisher.
- `src/ai_news_agent/ui.py`: FastAPI admin UI.
- `AGENTIC_AI_PORTFOLIO_REPORT.md`: detailed Vietnamese portfolio report.
- `README_PORTFOLIO.md`: English portfolio overview.

## Security Notes

- Do not commit `.env`.
- `.env.example` intentionally contains empty secret values.
- Rotate Facebook Page tokens regularly.
- Use least-privilege API credentials.
- Keep approval enabled for sensitive or public-facing publishing workflows.
