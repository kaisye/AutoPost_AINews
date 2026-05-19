from __future__ import annotations

import html
import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from openai import OpenAIError
from pydantic import ValidationError

from ai_news_agent.config import get_settings
from ai_news_agent.facebook import FacebookPublisher
from ai_news_agent.llm import PostWriter, explain_openai_error
from ai_news_agent.memory import AgentMemory
from ai_news_agent.workflow import AINewsWorkflow

ENV_PATH = Path(".env")


@dataclass
class RunStatus:
    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    message: str = "Ready"
    ok: bool | None = None


RUN_STATUS = RunStatus()
RUN_LOCK = threading.Lock()
SCHEDULER = BackgroundScheduler()
SCHEDULER_JOB_ID = "ai-news-agent-ui-schedule"

app = FastAPI(title="AI News Agent Admin")


@app.on_event("startup")
def start_scheduler() -> None:
    if not SCHEDULER.running:
        SCHEDULER.start()
    reschedule_from_env()


@app.on_event("shutdown")
def stop_scheduler() -> None:
    if SCHEDULER.running:
        SCHEDULER.shutdown(wait=False)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    env = read_env()
    settings = load_settings_or_none()
    records = []
    config_error = None
    if settings:
        records = AgentMemory(settings.database_path).recent_post_records(limit=6)
    else:
        try:
            get_settings.cache_clear()
            get_settings()
        except ValidationError as exc:
            config_error = "; ".join(error["msg"] for error in exc.errors())

    return page(
        title="AI News Agent",
        content=f"""
        {banner(config_error)}
        {theme_bar(env)}
        <section class="panel">
          <div class="panel-title">
            <div>
              <p class="eyebrow">Operations</p>
              <h1>Publishing Console</h1>
            </div>
            <form method="post" action="/run">
              <button class="primary" type="submit" {"disabled" if RUN_STATUS.running else ""}>
                {"Running..." if RUN_STATUS.running else "Run now"}
              </button>
            </form>
          </div>
          {status_card()}
          {schedule_card(env)}
        </section>
        {settings_form(env)}
        {history(records)}
        """,
    )


@app.post("/settings")
def save_settings(
    schedule_mode: Annotated[str, Form()] = "cron",
    schedule_time: Annotated[str, Form()] = "08:00",
    interval_hours: Annotated[int, Form()] = 0,
    interval_minutes: Annotated[int, Form()] = 0,
    approval_timeout: Annotated[int, Form()] = 180,
    auto_approve_on_timeout: Annotated[str | None, Form()] = None,
    facebook_enabled: Annotated[str | None, Form()] = None,
    facebook_page_id: Annotated[str, Form()] = "",
    facebook_page_access_token: Annotated[str, Form()] = "",
    llm_provider: Annotated[str, Form()] = "nvidia",
    openai_api_key: Annotated[str, Form()] = "",
    nvidia_api_key: Annotated[str, Form()] = "",
    openai_model: Annotated[str, Form()] = "openai/gpt-oss-120b",
    openai_base_url: Annotated[str, Form()] = "https://integrate.api.nvidia.com/v1",
    tavily_api_key: Annotated[str, Form()] = "",
    newsapi_key: Annotated[str, Form()] = "",
    telegram_bot_token: Annotated[str, Form()] = "",
    telegram_approver_chat_id: Annotated[str, Form()] = "",
    news_lookback_hours: Annotated[int, Form()] = 36,
    news_max_candidates: Annotated[int, Form()] = 30,
    post_article_count: Annotated[int, Form()] = 1,
) -> RedirectResponse:
    updates = {
        "LLM_PROVIDER": llm_provider,
        "OPENAI_MODEL": openai_model.strip(),
        "OPENAI_BASE_URL": openai_base_url.strip(),
        "SCHEDULE_MODE": schedule_mode if schedule_mode in {"cron", "interval"} else "cron",
        "SCHEDULE_CRON": daily_time_to_cron(schedule_time),
        "SCHEDULE_INTERVAL_HOURS": str(max(0, interval_hours)),
        "SCHEDULE_INTERVAL_MINUTES": str(max(0, interval_minutes)),
        "TELEGRAM_APPROVAL_TIMEOUT_MINUTES": str(approval_timeout),
        "TELEGRAM_AUTO_APPROVE_ON_TIMEOUT": "true"
        if auto_approve_on_timeout == "true"
        else "false",
        "FACEBOOK_ENABLED": "true" if facebook_enabled == "true" else "false",
        "FACEBOOK_PAGE_ID": facebook_page_id.strip(),
        "TELEGRAM_APPROVER_CHAT_ID": telegram_approver_chat_id.strip(),
        "NEWS_LOOKBACK_HOURS": str(news_lookback_hours),
        "NEWS_MAX_CANDIDATES": str(news_max_candidates),
        "POST_ARTICLE_COUNT": str(post_article_count),
    }
    secret_updates = {
        "OPENAI_API_KEY": openai_api_key.strip(),
        "NVIDIA_API_KEY": nvidia_api_key.strip(),
        "TAVILY_API_KEY": tavily_api_key.strip(),
        "NEWSAPI_KEY": newsapi_key.strip(),
        "TELEGRAM_BOT_TOKEN": telegram_bot_token.strip(),
        "FACEBOOK_PAGE_ACCESS_TOKEN": facebook_page_access_token.strip(),
    }
    updates.update({key: value for key, value in secret_updates.items() if value})
    write_env(updates)
    get_settings.cache_clear()
    reschedule_from_env()
    return RedirectResponse("/", status_code=303)


@app.post("/theme")
def save_theme(
    ui_theme_mode: Annotated[str, Form()] = "light",
    ui_theme_color: Annotated[str, Form()] = "#1264a3",
) -> RedirectResponse:
    write_env(
        {
            "UI_THEME_MODE": ui_theme_mode if ui_theme_mode in {"light", "dark"} else "light",
            "UI_THEME_COLOR": normalize_color(ui_theme_color),
        }
    )
    get_settings.cache_clear()
    return RedirectResponse("/", status_code=303)


@app.post("/run")
def run_now() -> RedirectResponse:
    trigger_workflow("Manual run started. Check Telegram for approval.")
    return RedirectResponse("/", status_code=303)


@app.post("/repost")
def repost_from_memory(
    post_id: Annotated[int, Form()],
    rewrite_instruction: Annotated[str, Form()] = "",
    mode: Annotated[str, Form()] = "rewrite",
) -> RedirectResponse:
    try:
        get_settings.cache_clear()
        settings = get_settings()
        if not settings.facebook_enabled:
            RUN_STATUS.ok = False
            RUN_STATUS.message = "Repost failed: Facebook publishing is disabled."
            return RedirectResponse("/", status_code=303)

        memory = AgentMemory(settings.database_path)
        record = memory.post_record(post_id)
        if not record:
            RUN_STATUS.ok = False
            RUN_STATUS.message = f"Repost failed: post #{post_id} was not found."
            return RedirectResponse("/", status_code=303)

        original_text = str(record["post_text"])
        rewrite_instruction = rewrite_instruction.strip() if mode == "rewrite" else ""
        post_text = (
            PostWriter(settings).rewrite_saved_post(original_text, rewrite_instruction)
            if rewrite_instruction
            else original_text
        )

        image_url = str(record["image_url"]) if record.get("image_url") else None
        facebook_post_id = FacebookPublisher(settings).publish_message(post_text, image_url=image_url)
        try:
            article_urls = json.loads(record["article_urls"] or "[]")
        except json.JSONDecodeError:
            article_urls = []
        memory.remember_repost(
            post_text=post_text,
            article_urls=article_urls,
            original_post_id=post_id,
            facebook_post_id=facebook_post_id,
            image_url=image_url,
            feedback=f"Rewritten and reposted from post #{post_id}: {rewrite_instruction}"
            if rewrite_instruction
            else None,
        )
        RUN_STATUS.ok = True
        RUN_STATUS.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = "Rewritten and reposted" if rewrite_instruction else "Reposted"
        RUN_STATUS.message = f"{action} post #{post_id}. Facebook id: {facebook_post_id or 'not published'}"
    except ValidationError as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = "Repost configuration error: " + "; ".join(error["msg"] for error in exc.errors())
    except Exception as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = f"Repost failed: {exc}"
    return RedirectResponse("/", status_code=303)


def trigger_workflow(message: str) -> bool:
    with RUN_LOCK:
        if RUN_STATUS.running:
            RUN_STATUS.message = "A workflow run is already active."
            return False
        RUN_STATUS.running = True
        RUN_STATUS.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        RUN_STATUS.finished_at = None
        RUN_STATUS.ok = None
        RUN_STATUS.message = message

    thread = threading.Thread(target=run_workflow_background, daemon=True)
    thread.start()
    return True


def scheduled_run() -> None:
    trigger_workflow("Scheduled run started. Check Telegram for approval.")


def reschedule_from_env() -> None:
    env = read_env()
    mode = env.get("SCHEDULE_MODE", "cron")
    if SCHEDULER.get_job(SCHEDULER_JOB_ID):
        SCHEDULER.remove_job(SCHEDULER_JOB_ID)
    trigger = interval_trigger_from_env(env) if mode == "interval" else CronTrigger.from_crontab(
        env.get("SCHEDULE_CRON", "0 8 * * *")
    )
    SCHEDULER.add_job(
        scheduled_run,
        trigger=trigger,
        id=SCHEDULER_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def run_workflow_background() -> None:
    try:
        get_settings.cache_clear()
        settings = get_settings()
        state = AINewsWorkflow(settings).run(run_id=f"ui-{uuid.uuid4()}")
        approval = state.get("approval", {})
        RUN_STATUS.ok = True
        RUN_STATUS.message = f"Finished. Approval status: {approval.get('status', 'unknown')}"
    except ValidationError as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = "Configuration error: " + "; ".join(error["msg"] for error in exc.errors())
    except OpenAIError as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = explain_openai_error(exc)
    except Exception as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = f"Run failed: {exc}"
    finally:
        RUN_STATUS.running = False
        RUN_STATUS.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def daily_time_to_cron(value: str) -> str:
    hour, minute = value.split(":", 1)
    return f"{int(minute)} {int(hour)} * * *"


def cron_to_daily_time(value: str | None) -> str:
    if not value:
        return "08:00"
    parts = value.split()
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[1]):02d}:{int(parts[0]):02d}"
    return "08:00"


def int_or_default(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except ValueError:
        return default


def interval_trigger_from_env(env: dict[str, str]) -> IntervalTrigger:
    hours = max(0, int_or_default(env.get("SCHEDULE_INTERVAL_HOURS"), 0))
    minutes = max(0, int_or_default(env.get("SCHEDULE_INTERVAL_MINUTES"), 0))
    if hours == 0 and minutes == 0:
        minutes = 1
    return IntervalTrigger(hours=hours, minutes=minutes)


def load_settings_or_none():
    try:
        get_settings.cache_clear()
        return get_settings()
    except ValidationError:
        return None


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def secret_placeholder(env: dict[str, str], key: str) -> str:
    return "Configured - leave blank to keep" if env.get(key) else "Paste value"


def banner(config_error: str | None) -> str:
    if not config_error:
        return ""
    return f'<section class="alert">Configuration issue: {esc(config_error)}</section>'


def status_card() -> str:
    state = "Running" if RUN_STATUS.running else ("OK" if RUN_STATUS.ok else "Attention" if RUN_STATUS.ok is False else "Idle")
    return f"""
    <div class="status-grid">
      <div><span>Status</span><strong>{esc(state)}</strong></div>
      <div><span>Started</span><strong>{esc(RUN_STATUS.started_at or "-")}</strong></div>
      <div><span>Finished</span><strong>{esc(RUN_STATUS.finished_at or "-")}</strong></div>
      <div class="wide"><span>Message</span><strong>{esc(RUN_STATUS.message)}</strong></div>
    </div>
    """


def schedule_card(env: dict[str, str]) -> str:
    next_run = "-"
    if SCHEDULER.running:
        job = SCHEDULER.get_job(SCHEDULER_JOB_ID)
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
    mode = env.get("SCHEDULE_MODE", "cron")
    detail = (
        f'every {esc(env.get("SCHEDULE_INTERVAL_HOURS", "0"))}h {esc(env.get("SCHEDULE_INTERVAL_MINUTES", "0"))}m'
        if mode == "interval"
        else esc(env.get("SCHEDULE_CRON", "0 8 * * *"))
    )
    return f"""
    <div class="schedule-card">
      <span>Schedule</span>
      <strong>{esc(mode)}: {detail}</strong>
      <span>Next run</span>
      <strong>{esc(next_run)}</strong>
    </div>
    """


def theme_bar(env: dict[str, str]) -> str:
    mode = env.get("UI_THEME_MODE", "light")
    color = normalize_color(env.get("UI_THEME_COLOR", "#1264a3"))
    return f"""
    <section class="themebar">
      <div>
        <p class="eyebrow">Appearance</p>
        <strong>Theme</strong>
      </div>
      <form method="post" action="/theme" class="theme-controls">
        <input type="hidden" name="ui_theme_color" value="{esc(color)}">
        <button class="seg {"active" if mode == "light" else ""}" name="ui_theme_mode" value="light" type="submit">Light</button>
        <button class="seg {"active" if mode == "dark" else ""}" name="ui_theme_mode" value="dark" type="submit">Dark</button>
      </form>
      <form method="post" action="/theme" class="color-form">
        <input type="hidden" name="ui_theme_mode" value="{esc(mode)}">
        <input aria-label="Theme color" name="ui_theme_color" type="color" value="{esc(color)}" onchange="this.form.submit()">
      </form>
    </section>
    """


def settings_form(env: dict[str, str]) -> str:
    schedule_time = cron_to_daily_time(env.get("SCHEDULE_CRON"))
    schedule_mode = env.get("SCHEDULE_MODE", "cron")
    facebook_enabled = env.get("FACEBOOK_ENABLED", "false").lower() == "true"
    auto_approve = env.get("TELEGRAM_AUTO_APPROVE_ON_TIMEOUT", "true").lower() == "true"
    nvidia_placeholder = secret_placeholder(env, "NVIDIA_API_KEY")
    openai_placeholder = secret_placeholder(env, "OPENAI_API_KEY")
    tavily_placeholder = secret_placeholder(env, "TAVILY_API_KEY")
    newsapi_placeholder = secret_placeholder(env, "NEWSAPI_KEY")
    telegram_placeholder = secret_placeholder(env, "TELEGRAM_BOT_TOKEN")
    facebook_placeholder = secret_placeholder(env, "FACEBOOK_PAGE_ACCESS_TOKEN")
    return f"""
    <section class="panel">
      <p class="eyebrow">Configuration</p>
      <h2>Schedule, Credentials & Publishing</h2>
      <form class="settings" method="post" action="/settings">
        <div class="form-section">Schedule</div>
        <label>Schedule mode
          <select name="schedule_mode">
            <option value="cron" {"selected" if schedule_mode == "cron" else ""}>Daily time</option>
            <option value="interval" {"selected" if schedule_mode == "interval" else ""}>Every X hours/minutes</option>
          </select>
        </label>
        <label>Daily posting time<input name="schedule_time" type="time" value="{esc(schedule_time)}" required></label>
        <label>Every hours<input name="interval_hours" type="number" min="0" max="168" value="{esc(env.get("SCHEDULE_INTERVAL_HOURS", "0"))}"></label>
        <label>Every minutes<input name="interval_minutes" type="number" min="0" max="1440" value="{esc(env.get("SCHEDULE_INTERVAL_MINUTES", "0"))}"></label>
        <label>Telegram approval timeout (minutes)<input name="approval_timeout" type="number" min="1" max="1440" value="{esc(env.get("TELEGRAM_APPROVAL_TIMEOUT_MINUTES", "180"))}"></label>
        <label class="toggle"><input name="auto_approve_on_timeout" type="checkbox" value="true" {"checked" if auto_approve else ""}> Auto approve after timeout</label>
        <div class="form-section">LLM</div>
        <label>LLM provider
          <select name="llm_provider">
            <option value="nvidia" {"selected" if env.get("LLM_PROVIDER", "nvidia") == "nvidia" else ""}>NVIDIA NIM</option>
            <option value="openai" {"selected" if env.get("LLM_PROVIDER") == "openai" else ""}>OpenAI</option>
          </select>
        </label>
        <label>Model<input name="openai_model" value="{esc(env.get("OPENAI_MODEL", "openai/gpt-oss-120b"))}"></label>
        <label>Base URL<input name="openai_base_url" value="{esc(env.get("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1"))}"></label>
        <label>NVIDIA API Key<input name="nvidia_api_key" type="password" placeholder="{esc(nvidia_placeholder)}"></label>
        <label>OpenAI API Key<input name="openai_api_key" type="password" placeholder="{esc(openai_placeholder)}"></label>
        <div class="form-section">News APIs</div>
        <label>News lookback hours<input name="news_lookback_hours" type="number" min="1" max="168" value="{esc(env.get("NEWS_LOOKBACK_HOURS", "36"))}"></label>
        <label>Max candidates<input name="news_max_candidates" type="number" min="3" max="100" value="{esc(env.get("NEWS_MAX_CANDIDATES", "30"))}"></label>
        <label>Articles after ranking<input name="post_article_count" type="number" min="1" max="10" value="{esc(env.get("POST_ARTICLE_COUNT", "1"))}"></label>
        <label>Tavily API Key<input name="tavily_api_key" type="password" placeholder="{esc(tavily_placeholder)}"></label>
        <label>NewsAPI Key<input name="newsapi_key" type="password" placeholder="{esc(newsapi_placeholder)}"></label>
        <div class="form-section">Telegram</div>
        <label>Telegram Bot Token<input name="telegram_bot_token" type="password" placeholder="{esc(telegram_placeholder)}"></label>
        <label>Telegram Approver Chat ID<input name="telegram_approver_chat_id" value="{esc(env.get("TELEGRAM_APPROVER_CHAT_ID", ""))}"></label>
        <div class="form-section">Facebook</div>
        <label class="toggle"><input name="facebook_enabled" type="checkbox" value="true" {"checked" if facebook_enabled else ""}> Enable Facebook publish after approval</label>
        <label>Facebook Page ID<input name="facebook_page_id" value="{esc(env.get("FACEBOOK_PAGE_ID", ""))}"></label>
        <label>Facebook Page Access Token<input name="facebook_page_access_token" type="password" placeholder="{esc(facebook_placeholder)}"></label>
        <button class="primary" type="submit">Save settings</button>
      </form>
    </section>
    """


def history(records: list[dict]) -> str:
    if not records:
        rows = '<p class="empty">No posts stored yet.</p>'
    else:
        rows = "".join(
            f"""
            <article class="post">
              <div class="post-meta">
                <span>#{esc(record.get("id"))}</span>
                <span>{esc(record.get("created_at"))}</span>
                <span>{esc(record.get("status"))}</span>
                <span>{'Facebook: ' + esc(record.get("facebook_post_id")) if record.get("facebook_post_id") else 'Facebook: not published'}</span>
                <span>{'Image: yes' if record.get("image_url") else 'Image: no'}</span>
              </div>
              <p>{esc(record.get("post_text"))[:900]}</p>
              <form method="post" action="/repost" class="post-actions">
                <input type="hidden" name="post_id" value="{esc(record.get("id"))}">
                <textarea name="rewrite_instruction" rows="2" placeholder="Rewrite instruction before reposting, e.g. make it sharper, less formal, more founder-focused"></textarea>
                <div class="action-row">
                  <button type="submit" name="mode" value="rewrite">Rewrite & repost</button>
                  <button type="submit" name="mode" value="as_is">Repost as is</button>
                </div>
              </form>
            </article>
            """
            for record in records
        )
    return f"""
    <section class="panel">
      <p class="eyebrow">Memory</p>
      <h2>Recent Posts</h2>
      <div class="history">{rows}</div>
    </section>
    """


def page(title: str, content: str) -> str:
    env = read_env()
    theme_mode = env.get("UI_THEME_MODE", "light")
    theme_color = normalize_color(env.get("UI_THEME_COLOR", "#1264a3"))
    dark = theme_mode == "dark"
    bg = "#111418" if dark else "#f7f8fa"
    panel = "#181d23" if dark else "#ffffff"
    text = "#eef2f5" if dark else "#17202a"
    muted = "#a9b3bd" if dark else "#65717e"
    border = "#303842" if dark else "#d8dde3"
    field_bg = "#12171d" if dark else "#ffffff"
    post_text = "#dde5ec" if dark else "#27313b"
    alert_bg = "#2a1717" if dark else "#fff4f2"
    alert_border = "#7f2a25" if dark else "#f5c2c0"
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta http-equiv="refresh" content="20">
      <title>{esc(title)}</title>
      <style>
        :root {{
          --bg: {bg};
          --panel: {panel};
          --text: {text};
          --muted: {muted};
          --border: {border};
          --field-bg: {field_bg};
          --post-text: {post_text};
          --alert-bg: {alert_bg};
          --alert-border: {alert_border};
          --accent: {theme_color};
          --accent-dark: {theme_color};
          --danger: #b42318;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: Arial, Helvetica, sans-serif;
          line-height: 1.5;
        }}
        main {{ width: min(1180px, calc(100vw - 32px)); margin: 28px auto; }}
        h1, h2 {{ margin: 0; letter-spacing: 0; }}
        h1 {{ font-size: 32px; }}
        h2 {{ font-size: 22px; }}
        .eyebrow {{ margin: 0 0 4px; color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 700; }}
        .panel {{
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 22px;
          margin-bottom: 18px;
        }}
        .themebar {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 14px;
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 14px 16px;
          margin-bottom: 18px;
        }}
        .themebar strong {{ display: block; font-size: 18px; }}
        .theme-controls {{
          display: flex;
          border: 1px solid var(--border);
          border-radius: 6px;
          overflow: hidden;
          margin-left: auto;
        }}
        .seg {{
          border: 0;
          border-right: 1px solid var(--border);
          padding: 9px 14px;
          background: var(--field-bg);
          color: var(--text);
          cursor: pointer;
          font-weight: 700;
        }}
        .seg:last-child {{ border-right: 0; }}
        .seg.active {{ background: var(--accent); color: white; }}
        .color-form input {{
          width: 46px;
          height: 38px;
          padding: 3px;
          margin: 0;
          cursor: pointer;
        }}
        .panel-title {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; }}
        .primary {{
          border: 0;
          border-radius: 6px;
          padding: 10px 16px;
          background: var(--accent);
          color: white;
          font-weight: 700;
          cursor: pointer;
        }}
        .primary:hover {{ background: var(--accent-dark); }}
        .primary:disabled {{ opacity: .55; cursor: not-allowed; }}
        .status-grid {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 12px;
          margin-top: 18px;
        }}
        .schedule-card {{
          display: grid;
          grid-template-columns: auto 1fr auto 1fr;
          gap: 10px;
          align-items: center;
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 12px;
          margin-top: 12px;
        }}
        .schedule-card span {{ color: var(--muted); font-size: 13px; }}
        .schedule-card strong {{ overflow-wrap: anywhere; }}
        .status-grid div {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 12px;
          min-height: 70px;
        }}
        .status-grid .wide {{ grid-column: span 4; }}
        .status-grid span, label {{ display: block; color: var(--muted); font-size: 13px; }}
        .status-grid strong {{ display: block; margin-top: 5px; font-size: 15px; overflow-wrap: anywhere; }}
        .settings {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 14px;
          margin-top: 16px;
        }}
        .form-section {{
          grid-column: 1 / -1;
          margin-top: 8px;
          padding-top: 12px;
          border-top: 1px solid var(--border);
          color: var(--text);
          font-size: 14px;
          font-weight: 700;
        }}
        .form-section:first-child {{ margin-top: 0; padding-top: 0; border-top: 0; }}
        input, select {{
          width: 100%;
          margin-top: 6px;
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 10px 11px;
          font: inherit;
          color: var(--text);
          background: var(--field-bg);
        }}
        .toggle {{
          display: flex;
          gap: 10px;
          align-items: center;
          color: var(--text);
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 10px 11px;
        }}
        .toggle input {{ width: auto; margin: 0; }}
        .settings button {{ align-self: end; }}
        .alert {{
          border: 1px solid #f5c2c0;
          border-color: var(--alert-border);
          background: var(--alert-bg);
          color: var(--danger);
          border-radius: 8px;
          padding: 14px 16px;
          margin-bottom: 18px;
          font-weight: 700;
        }}
        .history {{ display: grid; gap: 12px; margin-top: 16px; }}
        .post {{ border: 1px solid var(--border); border-radius: 6px; padding: 14px; }}
        .post p {{ margin: 10px 0 0; white-space: pre-wrap; color: var(--post-text); }}
        .post-meta {{ display: flex; flex-wrap: wrap; gap: 10px; color: var(--muted); font-size: 12px; }}
        .post-actions {{ margin-top: 12px; }}
        .post-actions textarea {{
          width: 100%;
          min-height: 62px;
          resize: vertical;
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 10px 11px;
          font: inherit;
          color: var(--text);
          background: var(--field-bg);
        }}
        .action-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
        .post-actions button {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 8px 12px;
          background: var(--field-bg);
          color: var(--text);
          font-weight: 700;
          cursor: pointer;
        }}
        .post-actions button:hover {{ border-color: var(--accent); color: var(--accent); }}
        .empty {{ color: var(--muted); margin-bottom: 0; }}
        @media (max-width: 760px) {{
          main {{ width: min(100vw - 20px, 1180px); margin: 10px auto; }}
          .panel-title, .settings {{ display: block; }}
          .themebar {{ align-items: stretch; flex-wrap: wrap; }}
          .theme-controls {{ margin-left: 0; }}
          .settings label, .settings button {{ margin-top: 12px; }}
          .status-grid {{ grid-template-columns: 1fr; }}
          .status-grid .wide {{ grid-column: span 1; }}
          .schedule-card {{ grid-template-columns: 1fr; }}
        }}
      </style>
    </head>
    <body><main>{content}</main></body>
    </html>
    """


def normalize_color(value: str | None) -> str:
    if not value:
        return "#1264a3"
    value = value.strip()
    if len(value) == 7 and value.startswith("#"):
        allowed = "0123456789abcdefABCDEF"
        if all(char in allowed for char in value[1:]):
            return value
    return "#1264a3"
