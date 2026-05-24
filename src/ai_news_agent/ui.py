from __future__ import annotations

import html
import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

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
from ai_news_agent.models import ContentStatus, MediaType, ScheduleStatus
from ai_news_agent.nodes import node_catalog
from ai_news_agent.platform import MediaPlatform, parse_hashtags, parse_sources
from ai_news_agent.workflow import AINewsWorkflow
from ai_news_agent.workflows import WORKFLOW_TEMPLATES

ENV_PATH = Path(".env")


@dataclass
class RunStatus:
    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    message: str = "Ready"
    ok: bool | None = None


@dataclass
class ConsoleData:
    env: dict[str, str]
    records: list[dict]
    contents: list
    jobs: list
    config_error: str | None


RUN_STATUS = RunStatus()
RUN_LOCK = threading.Lock()
SCHEDULER = BackgroundScheduler()
SCHEDULER_JOB_ID = "ai-news-agent-ui-schedule"
CONTENT_SCHEDULER_JOB_ID = "media-platform-due-content"

app = FastAPI(title="AI News Agent Admin")


@app.on_event("startup")
def start_scheduler() -> None:
    if not SCHEDULER.running:
        SCHEDULER.start()
    reschedule_from_env()
    reschedule_content_jobs()


@app.on_event("shutdown")
def stop_scheduler() -> None:
    if SCHEDULER.running:
        SCHEDULER.shutdown(wait=False)


@app.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    return RedirectResponse("/overview", status_code=303)


def load_console_data() -> ConsoleData:
    env = read_env()
    settings = load_settings_or_none()
    records = []
    contents = []
    jobs = []
    config_error = None
    if settings:
        memory = AgentMemory(settings.database_path)
        records = memory.recent_post_records(limit=6)
        contents = memory.recent_content_items(limit=12)
        jobs = memory.schedule_jobs(limit=12)
    else:
        try:
            get_settings.cache_clear()
            get_settings()
        except ValidationError as exc:
            config_error = "; ".join(error["msg"] for error in exc.errors())

    return ConsoleData(
        env=env,
        records=records,
        contents=contents,
        jobs=jobs,
        config_error=config_error,
    )


@app.get("/overview", response_class=HTMLResponse)
def overview_page() -> str:
    data = load_console_data()
    return page(
        title="AI News Agent",
        content=overview_dashboard(data),
        active="overview",
    )


@app.get("/studio", response_class=HTMLResponse)
def studio_page() -> str:
    data = load_console_data()
    return page(
        title="Content Studio",
        content=f'{banner(data.config_error)}<div class="workspace">{content_studio()}</div>',
        active="studio",
    )


@app.get("/content", response_class=HTMLResponse)
def content_page() -> str:
    data = load_console_data()
    return page(
        title="Content Library",
        content=f'{banner(data.config_error)}<div class="workspace">{content_library(data.contents)}</div>',
        active="content",
    )


@app.get("/schedule", response_class=HTMLResponse)
def schedule_page() -> str:
    data = load_console_data()
    return page(
        title="Schedule Manager",
        content=f'{banner(data.config_error)}<div class="workspace">{schedule_queue(data.contents, data.jobs)}</div>',
        active="schedule",
    )


@app.get("/workflows", response_class=HTMLResponse)
def workflows_page() -> str:
    data = load_console_data()
    return page(
        title="Workflow Builder",
        content=f'{banner(data.config_error)}<div class="workspace">{workflow_builder()}</div>',
        active="workflows",
    )


@app.get("/ai-news", response_class=HTMLResponse)
def ai_news_page() -> str:
    data = load_console_data()
    return page(
        title="AI News Automation",
        content=f"""
        {banner(data.config_error)}
        <div class="workspace">
          <section class="section">
            <div class="section-head">
              <div>
                <p class="eyebrow">AI News Automation</p>
                <h2>Automated News Pipeline</h2>
                <p class="section-copy">Run the AI news workflow, monitor approval state, and review the active schedule.</p>
              </div>
              <div class="action-row">
                <form method="post" action="/run">
                  <button class="primary" type="submit" {"disabled" if RUN_STATUS.running else ""}>{"Running..." if RUN_STATUS.running else "Run Now"}</button>
                </form>
              </div>
            </div>
            {status_card()}
            {schedule_card(data.env)}
          </section>
          {ai_news_schedule_form(data.env)}
        </div>
        """,
        active="ai-news",
    )


@app.get("/history", response_class=HTMLResponse)
def history_page() -> str:
    data = load_console_data()
    return page(
        title="Run History & Logs",
        content=f'{banner(data.config_error)}<div class="workspace">{history(data.records)}</div>',
        active="history",
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> str:
    data = load_console_data()
    return page(
        title="Settings",
        content=f'{banner(data.config_error)}<div class="workspace">{settings_form(data.env)}</div>',
        active="settings",
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
    return RedirectResponse("/settings", status_code=303)


@app.post("/ai-news/schedule")
def save_ai_news_schedule(
    schedule_mode: Annotated[str, Form()] = "cron",
    schedule_time: Annotated[str, Form()] = "08:00",
    interval_hours: Annotated[int, Form()] = 0,
    interval_minutes: Annotated[int, Form()] = 0,
) -> RedirectResponse:
    updates = {
        "SCHEDULE_MODE": schedule_mode if schedule_mode in {"cron", "interval"} else "cron",
        "SCHEDULE_CRON": daily_time_to_cron(schedule_time),
        "SCHEDULE_INTERVAL_HOURS": str(max(0, interval_hours)),
        "SCHEDULE_INTERVAL_MINUTES": str(max(0, interval_minutes)),
    }
    write_env(updates)
    get_settings.cache_clear()
    reschedule_from_env()
    RUN_STATUS.ok = True
    RUN_STATUS.message = "AI News schedule updated."
    return RedirectResponse("/ai-news", status_code=303)


@app.post("/settings/llm")
def save_llm_integration(
    llm_provider: Annotated[str, Form()] = "nvidia",
    openai_model: Annotated[str, Form()] = "openai/gpt-oss-120b",
    openai_base_url: Annotated[str, Form()] = "https://integrate.api.nvidia.com/v1",
    openai_api_key: Annotated[str, Form()] = "",
    nvidia_api_key: Annotated[str, Form()] = "",
) -> RedirectResponse:
    updates = {
        "LLM_PROVIDER": llm_provider if llm_provider in {"nvidia", "openai"} else "nvidia",
        "OPENAI_MODEL": openai_model.strip(),
        "OPENAI_BASE_URL": openai_base_url.strip(),
    }
    secret_updates = {
        "OPENAI_API_KEY": openai_api_key.strip(),
        "NVIDIA_API_KEY": nvidia_api_key.strip(),
    }
    updates.update({key: value for key, value in secret_updates.items() if value})
    write_env(updates)
    get_settings.cache_clear()
    RUN_STATUS.ok = True
    RUN_STATUS.message = "LLM integration updated."
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/news")
def save_news_integration(
    news_lookback_hours: Annotated[int, Form()] = 36,
    news_max_candidates: Annotated[int, Form()] = 30,
    post_article_count: Annotated[int, Form()] = 1,
    tavily_api_key: Annotated[str, Form()] = "",
    newsapi_key: Annotated[str, Form()] = "",
) -> RedirectResponse:
    updates = {
        "NEWS_LOOKBACK_HOURS": str(news_lookback_hours),
        "NEWS_MAX_CANDIDATES": str(news_max_candidates),
        "POST_ARTICLE_COUNT": str(post_article_count),
    }
    secret_updates = {
        "TAVILY_API_KEY": tavily_api_key.strip(),
        "NEWSAPI_KEY": newsapi_key.strip(),
    }
    updates.update({key: value for key, value in secret_updates.items() if value})
    write_env(updates)
    get_settings.cache_clear()
    RUN_STATUS.ok = True
    RUN_STATUS.message = "News source settings updated."
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/approval")
def save_approval_integration(
    approval_timeout: Annotated[int, Form()] = 180,
    auto_approve_on_timeout: Annotated[str | None, Form()] = None,
    telegram_bot_token: Annotated[str, Form()] = "",
    telegram_approver_chat_id: Annotated[str, Form()] = "",
) -> RedirectResponse:
    updates = {
        "TELEGRAM_APPROVAL_TIMEOUT_MINUTES": str(approval_timeout),
        "TELEGRAM_AUTO_APPROVE_ON_TIMEOUT": "true"
        if auto_approve_on_timeout == "true"
        else "false",
        "TELEGRAM_APPROVER_CHAT_ID": telegram_approver_chat_id.strip(),
    }
    if telegram_bot_token.strip():
        updates["TELEGRAM_BOT_TOKEN"] = telegram_bot_token.strip()
    write_env(updates)
    get_settings.cache_clear()
    RUN_STATUS.ok = True
    RUN_STATUS.message = "Approval channel updated."
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/platform")
def save_media_platform(
    platform: Annotated[str, Form()] = "facebook",
    enabled: Annotated[str | None, Form()] = None,
    facebook_page_id: Annotated[str, Form()] = "",
    facebook_page_access_token: Annotated[str, Form()] = "",
    youtube_client_id: Annotated[str, Form()] = "",
    youtube_client_secret: Annotated[str, Form()] = "",
    youtube_refresh_token: Annotated[str, Form()] = "",
    youtube_privacy_status: Annotated[str, Form()] = "private",
    youtube_category_id: Annotated[str, Form()] = "28",
) -> RedirectResponse:
    if platform == "youtube":
        privacy = youtube_privacy_status if youtube_privacy_status in {"private", "unlisted", "public"} else "private"
        updates = {
            "YOUTUBE_ENABLED": "true" if enabled == "true" else "false",
            "YOUTUBE_PRIVACY_STATUS": privacy,
            "YOUTUBE_CATEGORY_ID": youtube_category_id.strip() or "28",
        }
        secret_updates = {
            "YOUTUBE_CLIENT_ID": youtube_client_id.strip(),
            "YOUTUBE_CLIENT_SECRET": youtube_client_secret.strip(),
            "YOUTUBE_REFRESH_TOKEN": youtube_refresh_token.strip(),
        }
        updates.update({key: value for key, value in secret_updates.items() if value})
        write_env(updates)
        get_settings.cache_clear()
        RUN_STATUS.ok = True
        RUN_STATUS.message = "YouTube platform updated."
        return RedirectResponse("/settings", status_code=303)

    if platform != "facebook":
        RUN_STATUS.ok = False
        RUN_STATUS.message = f"{platform.title()} is not wired to a publisher yet."
        return RedirectResponse("/settings", status_code=303)
    updates = {
        "FACEBOOK_ENABLED": "true" if enabled == "true" else "false",
        "FACEBOOK_PAGE_ID": facebook_page_id.strip(),
    }
    if facebook_page_access_token.strip():
        updates["FACEBOOK_PAGE_ACCESS_TOKEN"] = facebook_page_access_token.strip()
    write_env(updates)
    get_settings.cache_clear()
    RUN_STATUS.ok = True
    RUN_STATUS.message = "Facebook platform updated."
    return RedirectResponse("/settings", status_code=303)


@app.post("/content")
def create_content(
    title: Annotated[str, Form()],
    hook: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    hashtags: Annotated[str, Form()] = "",
    sources: Annotated[str, Form()] = "",
    channel: Annotated[str, Form()] = "facebook",
    workflow_id: Annotated[str, Form()] = "manual-post",
    media_type: Annotated[str, Form()] = "",
    media_url: Annotated[str, Form()] = "",
    alt_text: Annotated[str, Form()] = "",
    schedule_at: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "draft",
) -> RedirectResponse:
    try:
        get_settings.cache_clear()
        settings = get_settings()
        scheduled_at = parse_datetime_local(schedule_at) if schedule_at else None
        selected_media_type = MediaType(media_type) if media_type in {"image", "video"} and media_url else None
        content_id = MediaPlatform(settings).create_manual_content(
            title=title,
            hook=hook,
            body=body,
            hashtags=parse_hashtags(hashtags),
            sources=parse_sources(sources),
            channel=channel,
            workflow_id=workflow_id,
            media_type=selected_media_type,
            media_url=media_url.strip() or None,
            alt_text=alt_text.strip() or None,
            scheduled_at=scheduled_at,
        )
        RUN_STATUS.ok = True
        RUN_STATUS.message = f"Created content item #{content_id}."
        if action == "publish":
            platform_post_id = MediaPlatform(settings).publish_content(content_id)
            RUN_STATUS.message = f"Created and published content #{content_id}. Platform id: {platform_post_id or 'not published'}"
    except ValidationError as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = "Content validation error: " + "; ".join(error["msg"] for error in exc.errors())
    except Exception as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = f"Content action failed: {exc}"
    return RedirectResponse("/studio", status_code=303)


@app.post("/content/publish")
def publish_content_now(content_id: Annotated[int, Form()]) -> RedirectResponse:
    try:
        get_settings.cache_clear()
        settings = get_settings()
        platform_post_id = MediaPlatform(settings).publish_content(content_id)
        RUN_STATUS.ok = True
        RUN_STATUS.message = f"Published content #{content_id}. Platform id: {platform_post_id or 'not published'}"
    except Exception as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = f"Publish failed: {exc}"
    return RedirectResponse("/content", status_code=303)


@app.post("/content/schedule")
def schedule_existing_content(
    content_id: Annotated[int, Form()],
    run_at: Annotated[str, Form()],
    workflow_id: Annotated[str, Form()] = "scheduled-media-post",
) -> RedirectResponse:
    try:
        get_settings.cache_clear()
        settings = get_settings()
        job_id = MediaPlatform(settings).schedule_content(
            content_id,
            parse_datetime_local(run_at),
            workflow_id=workflow_id,
        )
        RUN_STATUS.ok = True
        RUN_STATUS.message = f"Scheduled content #{content_id} as job #{job_id}."
    except Exception as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.message = f"Schedule failed: {exc}"
    return RedirectResponse("/schedule", status_code=303)


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
    return RedirectResponse("/overview", status_code=303)


@app.post("/run")
def run_now() -> RedirectResponse:
    trigger_workflow("Manual run started. Check Telegram for approval.")
    return RedirectResponse("/ai-news", status_code=303)


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
            return RedirectResponse("/history", status_code=303)

        memory = AgentMemory(settings.database_path)
        record = memory.post_record(post_id)
        if not record:
            RUN_STATUS.ok = False
            RUN_STATUS.message = f"Repost failed: post #{post_id} was not found."
            return RedirectResponse("/history", status_code=303)

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
    return RedirectResponse("/history", status_code=303)


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


def scheduled_content_run() -> None:
    try:
        get_settings.cache_clear()
        settings = get_settings()
        processed = MediaPlatform(settings).run_due_schedule_jobs()
        if processed:
            RUN_STATUS.ok = True
            RUN_STATUS.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            RUN_STATUS.message = f"Published {processed} due scheduled content item(s)."
    except Exception as exc:
        RUN_STATUS.ok = False
        RUN_STATUS.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        RUN_STATUS.message = f"Scheduled content failed: {exc}"


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


def reschedule_content_jobs() -> None:
    if SCHEDULER.get_job(CONTENT_SCHEDULER_JOB_ID):
        SCHEDULER.remove_job(CONTENT_SCHEDULER_JOB_ID)
    SCHEDULER.add_job(
        scheduled_content_run,
        "interval",
        minutes=1,
        id=CONTENT_SCHEDULER_JOB_ID,
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


def parse_datetime_local(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Saigon"))
    return parsed


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


def ai_news_schedule_form(env: dict[str, str]) -> str:
    schedule_time = cron_to_daily_time(env.get("SCHEDULE_CRON"))
    schedule_mode = env.get("SCHEDULE_MODE", "cron")
    next_run = "-"
    if SCHEDULER.running:
        job = SCHEDULER.get_job(SCHEDULER_JOB_ID)
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""
    <section class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">AI News Schedule</p>
          <h2>Edit Automation Schedule</h2>
          <p class="section-copy">Choose a daily posting time or run the AI news workflow every fixed interval.</p>
        </div>
        <div class="schedule-meta">
          <span>Next run</span>
          <strong>{esc(next_run)}</strong>
        </div>
      </div>
      <form class="settings schedule-settings" method="post" action="/ai-news/schedule">
        <label>Schedule mode
          <select name="schedule_mode">
            <option value="cron" {"selected" if schedule_mode == "cron" else ""}>Daily time</option>
            <option value="interval" {"selected" if schedule_mode == "interval" else ""}>Every X hours/minutes</option>
          </select>
        </label>
        <label>Daily posting time<input name="schedule_time" type="time" value="{esc(schedule_time)}" required></label>
        <label>Every hours<input name="interval_hours" type="number" min="0" max="168" value="{esc(env.get("SCHEDULE_INTERVAL_HOURS", "0"))}"></label>
        <label>Every minutes<input name="interval_minutes" type="number" min="0" max="1440" value="{esc(env.get("SCHEDULE_INTERVAL_MINUTES", "0"))}"></label>
        <div class="form-note">
          <span class="material-symbols-outlined">info</span>
          <span>Daily time uses the local machine timezone. Interval mode ignores the daily time and runs repeatedly.</span>
        </div>
        <button class="primary" type="submit">Update schedule</button>
      </form>
    </section>
    """


def overview_dashboard(data: ConsoleData) -> str:
    metrics = overview_metrics(data.contents, data.jobs, data.records)
    status_label = "Running" if RUN_STATUS.running else "Active" if RUN_STATUS.ok is not False else "Attention"
    status_class = "active" if RUN_STATUS.ok is not False else "failed"
    headline = "System Running" if RUN_STATUS.ok is not False else "Needs Attention"
    description = RUN_STATUS.message if RUN_STATUS.ok is False else "Processing news feeds, media drafts, approval, and publishing jobs."
    return f"""
    {banner(data.config_error)}
    <div class="dashboard-page">
      <div class="dashboard-head">
        <div>
          <h2>Operations Dashboard</h2>
          <p>Real-time status of your automated news pipeline.</p>
        </div>
        <a class="schedule-shortcut" href="/schedule">
          <span class="material-symbols-outlined">calendar_month</span>
          <span>View Schedule</span>
        </a>
      </div>

      <section class="overview-grid">
        <article class="overview-card status-panel">
          <div class="panel-topline">
            <span class="panel-label">Current Status</span>
            <span class="status-badge {status_class}"><i></i>{esc(status_label)}</span>
          </div>
          <div>
            <h3>{esc(headline)}</h3>
            <p>{esc(description)}</p>
          </div>
          <div class="panel-footer">
            <div>
              <span>Last run</span>
              <strong>{esc(RUN_STATUS.finished_at or "No run yet")}</strong>
            </div>
            <span class="material-symbols-outlined">check_circle</span>
          </div>
        </article>

        <article class="overview-card next-run-panel">
          <span class="panel-label">Next AI News Run</span>
          <strong>{esc(next_run_display())}</strong>
          <p>Next automated scrape and post-generation.</p>
          <form method="post" action="/run">
            <button type="submit" {"disabled" if RUN_STATUS.running else ""}>Run AI News Now</button>
          </form>
        </article>

        <div class="metric-grid">
          <article class="metric-card">
            <span>Scheduled</span>
            <strong>{metrics["scheduled"]}</strong>
          </article>
          <article class="metric-card">
            <span>Drafts</span>
            <strong>{metrics["drafts"]}</strong>
          </article>
          <article class="metric-card">
            <span>Published</span>
            <strong>{metrics["published"]}</strong>
          </article>
          <article class="metric-card danger">
            <span>Failed Jobs</span>
            <strong>{metrics["failed"]}</strong>
          </article>
        </div>
      </section>

      <section class="workspace-grid">
        <div class="workspace-table-block">
          <div class="block-title">
            <h3>Current Workspace</h3>
            <a href="/content">View All Library</a>
          </div>
          {overview_workspace_table(data.contents)}
        </div>
        <aside class="activity-block">
          <div class="block-title">
            <h3>Activity</h3>
          </div>
          {overview_activity(data)}
        </aside>
      </section>
    </div>
    """


def overview_metrics(contents: list, jobs: list, records: list[dict]) -> dict[str, str]:
    scheduled = sum(1 for job in jobs if getattr(job, "status", None) == ScheduleStatus.ACTIVE)
    drafts = sum(1 for content in contents if getattr(content, "status", None) == ContentStatus.DRAFT)
    published_content = sum(1 for content in contents if getattr(content, "status", None) == ContentStatus.PUBLISHED)
    published_memory = sum(1 for record in records if record.get("facebook_post_id"))
    failed_content = sum(1 for content in contents if getattr(content, "status", None) == ContentStatus.FAILED)
    failed_jobs = sum(1 for job in jobs if getattr(job, "status", None) == ScheduleStatus.FAILED)
    return {
        "scheduled": compact_count(scheduled),
        "drafts": compact_count(drafts),
        "published": compact_count(max(published_content, published_memory)),
        "failed": compact_count(failed_content + failed_jobs),
    }


def compact_count(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return str(value)


def next_run_display() -> str:
    if not SCHEDULER.running:
        return "Paused"
    job = SCHEDULER.get_job(SCHEDULER_JOB_ID)
    if not job or not job.next_run_time:
        return "Not set"
    now = datetime.now(job.next_run_time.tzinfo)
    total_seconds = max(0, int((job.next_run_time - now).total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def overview_workspace_table(contents: list) -> str:
    rows = "".join(overview_workspace_row(content) for content in contents[:3])
    if not rows:
        rows = """
        <tr>
          <td colspan="4">
            <div class="empty-workspace">
              <span class="material-symbols-outlined">library_add</span>
              <strong>No content items yet</strong>
              <p>Create a draft in Content Studio to populate this workspace.</p>
            </div>
          </td>
        </tr>
        """
    return f"""
    <div class="workspace-table">
      <table>
        <thead>
          <tr>
            <th>Content Source</th>
            <th>Status</th>
            <th>Platform</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def overview_workspace_row(content) -> str:
    detail = (
        f"Scheduled: {content.scheduled_at.strftime('%Y-%m-%d %H:%M')}"
        if content.scheduled_at
        else f"Workflow: {content.workflow_id}"
    )
    return f"""
    <tr>
      <td>
        <div class="source-cell">
          {content_thumb(content)}
          <div>
            <strong>{esc(content.title)}</strong>
            <span>{esc(detail)}</span>
          </div>
        </div>
      </td>
      <td>{status_chip(content.status.value)}</td>
      <td>
        <span class="platform-cell">
          <span class="material-symbols-outlined">{platform_icon(content.channel)}</span>
          {esc(content.channel.title())}
        </span>
      </td>
      <td class="row-actions"><a href="/content"><span class="material-symbols-outlined">edit</span></a></td>
    </tr>
    """


def status_chip(status: str) -> str:
    label = status.replace("_", " ").title()
    css = "failed" if status == "failed" else "scheduled" if status == "scheduled" else "published" if status == "published" else "drafting"
    return f'<span class="status-chip {css}">{esc(label)}</span>'


def platform_icon(channel: str) -> str:
    return {
        "facebook": "public",
        "telegram": "send",
        "linkedin": "share",
    }.get(channel.lower(), "share")


def overview_activity(data: ConsoleData) -> str:
    items: list[tuple[str, str, str, str]] = []
    if data.contents:
        latest = data.contents[0]
        items.append(("description", "draft", f'Draft Created: "{latest.title}" is in the content library.', human_time(latest.created_at)))
    if RUN_STATUS.started_at:
        items.append(("person", "neutral", f"Workflow Started: {RUN_STATUS.message}", RUN_STATUS.started_at))
    if data.records:
        latest_record = data.records[0]
        items.append(("publish", "success", f'Post Published: "{compact_post_title(latest_record.get("post_text"), 48)}"', str(latest_record.get("created_at") or "")))
    if RUN_STATUS.ok is False:
        items.append(("warning", "error", f"System Error: {RUN_STATUS.message}", RUN_STATUS.finished_at or "now"))
    while len(items) < 4:
        fallback = [
            ("description", "draft", "Draft queue is ready for new media posts.", "Ready"),
            ("person", "neutral", "Approval channel is standing by for Telegram review.", "Idle"),
            ("publish", "success", "Publishing service is configured for scheduled jobs.", "Active"),
            ("warning", "error", "No blocking system errors detected.", "Clear"),
        ][len(items)]
        items.append(fallback)
    rendered = "".join(
        f"""
        <div class="activity-item {css}">
          <div class="activity-icon"><span class="material-symbols-outlined">{icon}</span></div>
          <div>
            <p>{esc(message)}</p>
            <span>{esc(timestamp)}</span>
          </div>
        </div>
        """
        for icon, css, message, timestamp in items[:4]
    )
    return f'<div class="activity-card">{rendered}</div>'


def human_time(value: object) -> str:
    if not isinstance(value, datetime):
        return str(value or "")
    return value.strftime("%Y-%m-%d %H:%M")


def theme_bar(env: dict[str, str]) -> str:
    mode = env.get("UI_THEME_MODE", "light")
    return f"""
    <div class="themebar" aria-label="Appearance controls">
      <span class="theme-label">Slate/Snow</span>
      <form method="post" action="/theme" class="theme-controls">
        <input type="hidden" name="ui_theme_color" value="#0f172a">
        <button class="seg {"active" if mode == "light" else ""}" name="ui_theme_mode" value="light" type="submit">Light</button>
        <button class="seg {"active" if mode == "dark" else ""}" name="ui_theme_mode" value="dark" type="submit">Dark</button>
      </form>
    </div>
    """


def content_studio() -> str:
    workflow_options = "".join(
        f'<option value="{esc(template.id)}">{esc(template.name)}</option>'
        for template in WORKFLOW_TEMPLATES.values()
    )
    return f"""
    <section id="studio" class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Content Studio</p>
          <h2>Create Manual Media Post</h2>
        </div>
      </div>
      <form class="settings" method="post" action="/content">
        <label>Title<input name="title" required placeholder="Internal title for this content item"></label>
        <label>Workflow
          <select name="workflow_id">{workflow_options}</select>
        </label>
        <label>Hook<input name="hook" placeholder="Opening line shown before the body"></label>
        <label>Channel
          <select name="channel">
            <option value="facebook">Facebook</option>
            <option value="youtube">YouTube</option>
            <option value="telegram" disabled>Telegram soon</option>
            <option value="linkedin" disabled>LinkedIn soon</option>
          </select>
        </label>
        <label class="full">Body<textarea name="body" rows="7" required placeholder="Write or paste the post content"></textarea></label>
        <label>Hashtags<input name="hashtags" placeholder="#AI #AgenticAI #Automation"></label>
        <label>Media type
          <select name="media_type">
            <option value="">No media</option>
            <option value="image">Image URL</option>
            <option value="video">Video URL</option>
          </select>
        </label>
        <label>Media URL<input name="media_url" type="url" placeholder="https://..."></label>
        <label>Alt text<input name="alt_text" placeholder="Short description for the media"></label>
        <label class="full">Sources<textarea name="sources" rows="3" placeholder="One source per line"></textarea></label>
        <label>Schedule at<input name="schedule_at" type="datetime-local"></label>
        <div class="action-row studio-actions">
          <button class="primary" type="submit" name="action" value="draft">Save draft / schedule</button>
          <button type="submit" name="action" value="publish">Publish now</button>
        </div>
      </form>
    </section>
    """


def workflow_builder() -> str:
    templates = "".join(
        f"""
        <article class="template-card">
          <h3>{esc(template.name)}</h3>
          <p>{esc(template.description)}</p>
          <div class="node-chain">{esc(" -> ".join(template.nodes))}</div>
        </article>
        """
        for template in WORKFLOW_TEMPLATES.values()
    )
    nodes = "".join(
        f'<span class="node-pill">{esc(node["name"])}<small>{esc(node["id"])}</small></span>'
        for node in node_catalog()
    )
    return f"""
    <section id="workflows" class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Workflow Builder</p>
          <h2>Reusable Node Templates</h2>
        </div>
      </div>
      <div class="template-grid">{templates}</div>
      <div class="node-catalog">{nodes}</div>
    </section>
    """


def schedule_queue(contents: list, jobs: list) -> str:
    active_jobs = "".join(
        f"""
        <tr>
          <td>#{esc(job.id)}</td>
          <td>#{esc(job.content_id)}</td>
          <td>{esc(job.workflow_id)}</td>
          <td>{esc(job.run_at.strftime("%Y-%m-%d %H:%M"))}</td>
          <td>{esc(job.repeat_mode)}</td>
          <td>{esc(job.status.value)}</td>
        </tr>
        """
        for job in jobs
    ) or '<tr><td colspan="6">No scheduled jobs yet.</td></tr>'
    content_options = "".join(
        f'<option value="{esc(content.id)}">#{esc(content.id)} {esc(content.title)}</option>'
        for content in contents
    )
    workflow_options = "".join(
        f'<option value="{esc(template.id)}">{esc(template.name)}</option>'
        for template in WORKFLOW_TEMPLATES.values()
    )
    return f"""
    <section id="calendar" class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Schedule Calendar</p>
          <h2>Queued Posts</h2>
        </div>
      </div>
      <form class="inline-form" method="post" action="/content/schedule">
        <select name="content_id" required>{content_options}</select>
        <input name="run_at" type="datetime-local" required>
        <select name="workflow_id">{workflow_options}</select>
        <button class="primary" type="submit">Schedule</button>
      </form>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Job</th><th>Content</th><th>Workflow</th><th>Run at</th><th>Repeat</th><th>Status</th></tr></thead>
          <tbody>{active_jobs}</tbody>
        </table>
      </div>
    </section>
    """


def content_library(contents: list) -> str:
    if not contents:
        cards = '<p class="empty">No content items yet. Create one from Content Studio.</p>'
    else:
        cards = "".join(
            f"""
            <details class="post">
              <summary>
                {content_thumb(content)}
                <span class="post-summary">
                  <span class="post-title">#{esc(content.id)} {esc(content.title)}</span>
                  <span class="post-meta">
                    <span>{esc(content.status.value)}</span>
                    <span>{esc(content.channel)}</span>
                    <span>{esc(content.workflow_id)}</span>
                    <span>{esc(content.scheduled_at.strftime("%Y-%m-%d %H:%M") if content.scheduled_at else "not scheduled")}</span>
                  </span>
                </span>
                <span class="expand-label">Open</span>
              </summary>
              <div class="post-detail">
                <p>{esc(content.as_post())}</p>
                <form class="post-actions" method="post" action="/content/publish">
                  <input type="hidden" name="content_id" value="{esc(content.id)}">
                  <button type="submit">Publish now</button>
                </form>
              </div>
            </details>
            """
            for content in contents
        )
    return f"""
    <section id="content" class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Content Library</p>
          <h2>Drafts, Scheduled & Published</h2>
        </div>
      </div>
      <div class="history">{cards}</div>
    </section>
    """


def settings_form(env: dict[str, str]) -> str:
    facebook_enabled = env.get("FACEBOOK_ENABLED", "false").lower() == "true"
    auto_approve = env.get("TELEGRAM_AUTO_APPROVE_ON_TIMEOUT", "true").lower() == "true"
    nvidia_placeholder = secret_placeholder(env, "NVIDIA_API_KEY")
    openai_placeholder = secret_placeholder(env, "OPENAI_API_KEY")
    tavily_placeholder = secret_placeholder(env, "TAVILY_API_KEY")
    newsapi_placeholder = secret_placeholder(env, "NEWSAPI_KEY")
    telegram_placeholder = secret_placeholder(env, "TELEGRAM_BOT_TOKEN")
    facebook_placeholder = secret_placeholder(env, "FACEBOOK_PAGE_ACCESS_TOKEN")
    youtube_secret_placeholder = secret_placeholder(env, "YOUTUBE_CLIENT_SECRET")
    youtube_refresh_placeholder = secret_placeholder(env, "YOUTUBE_REFRESH_TOKEN")
    youtube_enabled = env.get("YOUTUBE_ENABLED", "false").lower() == "true"
    return f"""
    <section id="settings" class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Integrations</p>
          <h2>Settings Blocks</h2>
          <p class="section-copy">Add or update each provider independently. Secret fields can be left blank to keep the current value.</p>
        </div>
      </div>
      <div class="settings-block-grid">
        <article class="settings-block">
          <div class="settings-block-head">
            <div class="block-icon"><span class="material-symbols-outlined">neurology</span></div>
            <div>
              <h3>LLM Provider</h3>
              <p>{esc(env.get("LLM_PROVIDER", "nvidia")).title()} · {esc(env.get("OPENAI_MODEL", "openai/gpt-oss-120b"))}</p>
            </div>
            <span class="connection-badge {"connected" if env.get("NVIDIA_API_KEY") or env.get("OPENAI_API_KEY") else ""}">{'Connected' if env.get("NVIDIA_API_KEY") or env.get("OPENAI_API_KEY") else 'Needs API'}</span>
          </div>
          <details class="settings-drawer" open>
            <summary><span>Add / update LLM</span><span class="material-symbols-outlined">add</span></summary>
            <form class="integration-form" method="post" action="/settings/llm">
              <label>Provider
                <select name="llm_provider">
                  <option value="nvidia" {"selected" if env.get("LLM_PROVIDER", "nvidia") == "nvidia" else ""}>NVIDIA NIM</option>
                  <option value="openai" {"selected" if env.get("LLM_PROVIDER") == "openai" else ""}>OpenAI</option>
                </select>
              </label>
              <label>Model<input name="openai_model" value="{esc(env.get("OPENAI_MODEL", "openai/gpt-oss-120b"))}"></label>
              <label class="full">Base URL<input name="openai_base_url" value="{esc(env.get("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1"))}"></label>
              <label>NVIDIA API Key<input name="nvidia_api_key" type="password" placeholder="{esc(nvidia_placeholder)}"></label>
              <label>OpenAI API Key<input name="openai_api_key" type="password" placeholder="{esc(openai_placeholder)}"></label>
              <button class="primary" type="submit">Add provider</button>
            </form>
          </details>
        </article>

        <article class="settings-block">
          <div class="settings-block-head">
            <div class="block-icon"><span class="material-symbols-outlined">rss_feed</span></div>
            <div>
              <h3>News Sources</h3>
              <p>{esc(env.get("NEWS_MAX_CANDIDATES", "30"))} candidates · {esc(env.get("POST_ARTICLE_COUNT", "1"))} article(s) after rank</p>
            </div>
            <span class="connection-badge {"connected" if env.get("TAVILY_API_KEY") or env.get("NEWSAPI_KEY") else ""}">{'Connected' if env.get("TAVILY_API_KEY") or env.get("NEWSAPI_KEY") else 'Optional'}</span>
          </div>
          <details class="settings-drawer">
            <summary><span>Add / update source API</span><span class="material-symbols-outlined">add</span></summary>
            <form class="integration-form" method="post" action="/settings/news">
              <label>Lookback hours<input name="news_lookback_hours" type="number" min="1" max="168" value="{esc(env.get("NEWS_LOOKBACK_HOURS", "36"))}"></label>
              <label>Max candidates<input name="news_max_candidates" type="number" min="3" max="100" value="{esc(env.get("NEWS_MAX_CANDIDATES", "30"))}"></label>
              <label>Articles after ranking<input name="post_article_count" type="number" min="1" max="10" value="{esc(env.get("POST_ARTICLE_COUNT", "1"))}"></label>
              <label>Tavily API Key<input name="tavily_api_key" type="password" placeholder="{esc(tavily_placeholder)}"></label>
              <label>NewsAPI Key<input name="newsapi_key" type="password" placeholder="{esc(newsapi_placeholder)}"></label>
              <button class="primary" type="submit">Add source</button>
            </form>
          </details>
        </article>

        <article class="settings-block">
          <div class="settings-block-head">
            <div class="block-icon"><span class="material-symbols-outlined">approval_delegation</span></div>
            <div>
              <h3>Approval Channel</h3>
              <p>Telegram · timeout {esc(env.get("TELEGRAM_APPROVAL_TIMEOUT_MINUTES", "180"))} minutes</p>
            </div>
            <span class="connection-badge {"connected" if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_APPROVER_CHAT_ID") else ""}">{'Connected' if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_APPROVER_CHAT_ID") else 'Needs setup'}</span>
          </div>
          <details class="settings-drawer">
            <summary><span>Add / update Telegram</span><span class="material-symbols-outlined">add</span></summary>
            <form class="integration-form" method="post" action="/settings/approval">
              <label>Telegram Bot Token<input name="telegram_bot_token" type="password" placeholder="{esc(telegram_placeholder)}"></label>
              <label>Approver Chat ID<input name="telegram_approver_chat_id" value="{esc(env.get("TELEGRAM_APPROVER_CHAT_ID", ""))}"></label>
              <label>Approval timeout<input name="approval_timeout" type="number" min="1" max="1440" value="{esc(env.get("TELEGRAM_APPROVAL_TIMEOUT_MINUTES", "180"))}"></label>
              <label class="toggle"><input name="auto_approve_on_timeout" type="checkbox" value="true" {"checked" if auto_approve else ""}> Auto approve after timeout</label>
              <button class="primary" type="submit">Add approval</button>
            </form>
          </details>
        </article>

        <article class="settings-block">
          <div class="settings-block-head">
            <div class="block-icon"><span class="material-symbols-outlined">hub</span></div>
            <div>
              <h3>Media Platforms</h3>
              <p>Facebook and YouTube are active publishers. LinkedIn and TikTok are prepared slots.</p>
            </div>
            <span class="connection-badge {"connected" if (facebook_enabled and env.get("FACEBOOK_PAGE_ID")) or (youtube_enabled and env.get("YOUTUBE_REFRESH_TOKEN")) else ""}">{'Connected' if (facebook_enabled and env.get("FACEBOOK_PAGE_ID")) or (youtube_enabled and env.get("YOUTUBE_REFRESH_TOKEN")) else 'Needs setup'}</span>
          </div>
          <div class="platform-list">
            <details class="settings-drawer" open>
              <summary><span>Facebook Page</span><span class="material-symbols-outlined">add</span></summary>
              <form class="integration-form" method="post" action="/settings/platform">
                <input type="hidden" name="platform" value="facebook">
                <label class="toggle"><input name="enabled" type="checkbox" value="true" {"checked" if facebook_enabled else ""}> Enable Facebook publishing</label>
                <label>Page ID<input name="facebook_page_id" value="{esc(env.get("FACEBOOK_PAGE_ID", ""))}"></label>
                <label class="full">Page Access Token<input name="facebook_page_access_token" type="password" placeholder="{esc(facebook_placeholder)}"></label>
                <button class="primary" type="submit">Add platform</button>
              </form>
            </details>
            <details class="settings-drawer">
              <summary><span>YouTube Upload</span><span class="material-symbols-outlined">add</span></summary>
              <form class="integration-form" method="post" action="/settings/platform">
                <input type="hidden" name="platform" value="youtube">
                <label class="toggle"><input name="enabled" type="checkbox" value="true" {"checked" if youtube_enabled else ""}> Enable YouTube publishing</label>
                <label>Privacy
                  <select name="youtube_privacy_status">
                    <option value="private" {"selected" if env.get("YOUTUBE_PRIVACY_STATUS", "private") == "private" else ""}>Private</option>
                    <option value="unlisted" {"selected" if env.get("YOUTUBE_PRIVACY_STATUS") == "unlisted" else ""}>Unlisted</option>
                    <option value="public" {"selected" if env.get("YOUTUBE_PRIVACY_STATUS") == "public" else ""}>Public</option>
                  </select>
                </label>
                <label>Client ID<input name="youtube_client_id" value="{esc(env.get("YOUTUBE_CLIENT_ID", ""))}"></label>
                <label>Client Secret<input name="youtube_client_secret" type="password" placeholder="{esc(youtube_secret_placeholder)}"></label>
                <label>Refresh Token<input name="youtube_refresh_token" type="password" placeholder="{esc(youtube_refresh_placeholder)}"></label>
                <label>Category ID<input name="youtube_category_id" value="{esc(env.get("YOUTUBE_CATEGORY_ID", "28"))}"></label>
                <button class="primary" type="submit">Add platform</button>
              </form>
            </details>
            {disabled_platform_block("LinkedIn", "business_center")}
            {disabled_platform_block("TikTok", "movie")}
          </div>
        </article>
      </div>
    </section>
    """


def disabled_platform_block(name: str, icon: str) -> str:
    return f"""
    <div class="platform-placeholder">
      <span class="material-symbols-outlined">{icon}</span>
      <div>
        <strong>{esc(name)}</strong>
        <span>Coming soon</span>
      </div>
      <button type="button" disabled>Add</button>
    </div>
    """


def history(records: list[dict]) -> str:
    if not records:
        rows = '<p class="empty">No posts stored yet.</p>'
    else:
        rows = "".join(
            f"""
            <details class="post">
              <summary>
                {post_thumb(record)}
                <span class="post-summary">
                  <span class="post-title">{esc(compact_post_title(record.get("post_text")))}</span>
                  <span class="post-meta">
                    <span>#{esc(record.get("id"))}</span>
                    <span>{esc(record.get("created_at"))}</span>
                    <span>{esc(record.get("status"))}</span>
                    <span>{'Facebook: ' + esc(record.get("facebook_post_id")) if record.get("facebook_post_id") else 'Facebook: not published'}</span>
                  </span>
                </span>
                <span class="expand-label">Open</span>
              </summary>
              <div class="post-detail">
                <p>{esc(record.get("post_text"))}</p>
                <form method="post" action="/repost" class="post-actions">
                  <input type="hidden" name="post_id" value="{esc(record.get("id"))}">
                  <textarea name="rewrite_instruction" rows="2" placeholder="Rewrite instruction before reposting, e.g. make it sharper, less formal, more founder-focused"></textarea>
                  <div class="action-row">
                    <button type="submit" name="mode" value="rewrite">Rewrite & repost</button>
                    <button type="submit" name="mode" value="as_is">Repost as is</button>
                  </div>
                </form>
              </div>
            </details>
            """
            for record in records
        )
    return f"""
    <section id="memory" class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Memory</p>
          <h2>Recent Posts</h2>
        </div>
      </div>
      <div class="history">{rows}</div>
    </section>
    """


def compact_post_title(post_text: object, limit: int = 120) -> str:
    text = " ".join(str(post_text or "").split())
    if not text:
        return "Untitled post"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def post_thumb(record: dict) -> str:
    image_url = record.get("image_url")
    if image_url:
        return f'<img class="post-thumb" src="{esc(image_url)}" alt="">'
    return '<span class="post-thumb placeholder">No image</span>'


def content_thumb(content) -> str:
    if content.media_type == MediaType.IMAGE and content.media_url:
        return f'<img class="post-thumb" src="{esc(content.media_url)}" alt="">'
    if content.media_type == MediaType.VIDEO and content.media_url:
        return '<span class="post-thumb placeholder">Video</span>'
    return '<span class="post-thumb placeholder">No media</span>'


def legacy_page(title: str, content: str) -> str:
    env = read_env()
    theme_mode = env.get("UI_THEME_MODE", "light")
    theme_color = normalize_color(env.get("UI_THEME_COLOR", "#1264a3"))
    dark = theme_mode == "dark"
    bg = "#0f1115" if dark else "#f6f7f9"
    surface = "#161a20" if dark else "#ffffff"
    surface_2 = "#11151b" if dark else "#fbfcfd"
    text = "#edf1f5" if dark else "#171b20"
    muted = "#9aa4af" if dark else "#667085"
    border = "#2a313a" if dark else "#d9dee5"
    field_bg = "#10141a" if dark else "#ffffff"
    post_text = "#dbe3ec" if dark else "#2d3640"
    alert_bg = "#2a1717" if dark else "#fff4f2"
    alert_border = "#7f2a25" if dark else "#f2b8b5"
    subtle = "#202630" if dark else "#eef2f6"
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
          --surface: {surface};
          --surface-2: {surface_2};
          --text: {text};
          --muted: {muted};
          --border: {border};
          --field-bg: {field_bg};
          --post-text: {post_text};
          --alert-bg: {alert_bg};
          --alert-border: {alert_border};
          --subtle: {subtle};
          --accent: {theme_color};
          --danger: #b42318;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
          line-height: 1.5;
        }}
        a {{ color: inherit; text-decoration: none; }}
        main {{ width: min(1360px, calc(100vw - 32px)); margin: 20px auto 36px; }}
        h1, h2 {{ margin: 0; letter-spacing: 0; line-height: 1.2; }}
        h1 {{ font-size: 24px; font-weight: 720; }}
        h2 {{ font-size: 18px; font-weight: 720; }}
        .eyebrow {{ margin: 0 0 4px; color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 720; letter-spacing: .04em; }}
        .topbar {{
          position: sticky;
          top: 0;
          z-index: 5;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          min-height: 70px;
          background: color-mix(in srgb, var(--bg) 88%, transparent);
          backdrop-filter: blur(12px);
          border-bottom: 1px solid var(--border);
          margin-bottom: 20px;
        }}
        .top-actions {{ display: flex; align-items: center; gap: 10px; }}
        .app-grid {{
          display: grid;
          grid-template-columns: 190px minmax(0, 1fr);
          gap: 20px;
          align-items: start;
        }}
        .sidebar {{
          position: sticky;
          top: 90px;
          display: grid;
          gap: 4px;
          padding: 8px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: var(--surface);
        }}
        .sidebar a {{
          border-radius: 6px;
          padding: 9px 10px;
          color: var(--muted);
          font-size: 13px;
          font-weight: 650;
        }}
        .sidebar a:hover {{ background: var(--subtle); color: var(--text); }}
        .workspace {{ display: grid; gap: 16px; }}
        .section {{
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 20px;
        }}
        .section-head {{
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: center;
          padding-bottom: 14px;
          border-bottom: 1px solid var(--border);
        }}
        .themebar {{
          display: flex;
          align-items: center;
          gap: 10px;
        }}
        .theme-controls {{
          display: flex;
          border: 1px solid var(--border);
          border-radius: 6px;
          overflow: hidden;
          background: var(--surface);
        }}
        .seg {{
          border: 0;
          border-right: 1px solid var(--border);
          padding: 8px 12px;
          background: var(--field-bg);
          color: var(--text);
          cursor: pointer;
          font-weight: 650;
          font-size: 13px;
        }}
        .seg:last-child {{ border-right: 0; }}
        .seg.active {{ background: var(--accent); color: white; }}
        .color-form input {{
          width: 38px;
          height: 34px;
          padding: 3px;
          margin: 0;
          cursor: pointer;
        }}
        .primary {{
          border: 0;
          border-radius: 6px;
          padding: 9px 14px;
          background: var(--accent);
          color: white;
          font-weight: 720;
          cursor: pointer;
        }}
        .primary:hover {{ filter: brightness(.96); }}
        .primary:disabled {{ opacity: .55; cursor: not-allowed; }}
        .status-grid {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
          margin-top: 16px;
        }}
        .schedule-card {{
          display: grid;
          grid-template-columns: auto 1fr auto 1fr;
          gap: 10px;
          align-items: center;
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 11px 12px;
          margin-top: 12px;
          background: var(--surface-2);
        }}
        .schedule-card span {{ color: var(--muted); font-size: 13px; }}
        .schedule-card strong {{ overflow-wrap: anywhere; }}
        .status-grid div {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 12px;
          min-height: 68px;
          background: var(--surface-2);
        }}
        .status-grid .wide {{ grid-column: span 4; }}
        .status-grid span, label {{ display: block; color: var(--muted); font-size: 13px; }}
        .status-grid strong {{ display: block; margin-top: 5px; font-size: 14px; overflow-wrap: anywhere; color: var(--text); }}
        .settings {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 14px;
          margin-top: 16px;
        }}
        .settings .full {{ grid-column: 1 / -1; }}
        .form-section {{
          grid-column: 1 / -1;
          margin-top: 8px;
          padding-top: 12px;
          border-top: 1px solid var(--border);
          color: var(--text);
          font-size: 14px;
          font-weight: 720;
        }}
        .form-section:first-child {{ margin-top: 0; padding-top: 0; border-top: 0; }}
        input, select, textarea {{
          width: 100%;
          margin-top: 6px;
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 9px 10px;
          font: inherit;
          color: var(--text);
          background: var(--field-bg);
        }}
        input:focus, select:focus, textarea:focus {{
          border-color: var(--accent);
          box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 16%, transparent);
          outline: none;
        }}
        .toggle {{
          display: flex;
          gap: 10px;
          align-items: center;
          color: var(--text);
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 9px 10px;
          background: var(--surface-2);
        }}
        .toggle input {{ width: auto; margin: 0; }}
        .settings button {{ align-self: end; }}
        .studio-actions {{ align-self: end; }}
        .studio-actions button:not(.primary), .inline-form button:not(.primary) {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 9px 14px;
          background: var(--field-bg);
          color: var(--text);
          font-weight: 720;
          cursor: pointer;
        }}
        .template-grid {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
          margin-top: 16px;
        }}
        .template-card {{
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 14px;
          background: var(--surface-2);
        }}
        .template-card h3 {{ margin: 0 0 6px; font-size: 15px; }}
        .template-card p {{ margin: 0 0 10px; color: var(--muted); font-size: 13px; }}
        .node-chain {{
          color: var(--text);
          font-size: 12px;
          overflow-wrap: anywhere;
        }}
        .node-catalog {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 14px;
        }}
        .node-pill {{
          display: inline-flex;
          align-items: center;
          gap: 7px;
          border: 1px solid var(--border);
          border-radius: 999px;
          padding: 7px 10px;
          background: var(--field-bg);
          font-weight: 700;
          font-size: 12px;
        }}
        .node-pill small {{ color: var(--muted); font-weight: 650; }}
        .inline-form {{
          display: grid;
          grid-template-columns: minmax(160px, 1fr) minmax(190px, 1fr) minmax(190px, 1fr) auto;
          gap: 10px;
          align-items: end;
          margin-top: 16px;
        }}
        .table-wrap {{ overflow-x: auto; margin-top: 14px; }}
        table {{
          width: 100%;
          border-collapse: collapse;
          border: 1px solid var(--border);
          border-radius: 8px;
          overflow: hidden;
          font-size: 13px;
        }}
        th, td {{ border-bottom: 1px solid var(--border); padding: 9px 10px; text-align: left; }}
        th {{ background: var(--surface-2); color: var(--muted); font-weight: 720; }}
        td {{ color: var(--text); }}
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
        .history {{ display: grid; gap: 10px; margin-top: 16px; }}
        .post {{ border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); overflow: hidden; }}
        .post[open] {{ border-color: color-mix(in srgb, var(--accent) 38%, var(--border)); }}
        .post summary {{
          display: grid;
          grid-template-columns: 92px minmax(0, 1fr) auto;
          gap: 12px;
          align-items: center;
          padding: 12px;
          cursor: pointer;
          list-style: none;
        }}
        .post summary::-webkit-details-marker {{ display: none; }}
        .post summary:hover {{ background: color-mix(in srgb, var(--subtle) 72%, transparent); }}
        .post-thumb {{
          width: 92px;
          height: 62px;
          border: 1px solid var(--border);
          border-radius: 6px;
          object-fit: cover;
          background: var(--field-bg);
        }}
        .post-thumb.placeholder {{
          display: grid;
          place-items: center;
          color: var(--muted);
          font-size: 11px;
          font-weight: 650;
        }}
        .post-summary {{ display: grid; gap: 6px; min-width: 0; }}
        .post-title {{
          color: var(--text);
          font-size: 14px;
          font-weight: 700;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }}
        .post-detail {{ border-top: 1px solid var(--border); padding: 14px; }}
        .post-detail p {{ margin: 0; white-space: pre-wrap; color: var(--post-text); }}
        .post-meta {{ display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 12px; }}
        .expand-label {{
          border: 1px solid var(--border);
          border-radius: 999px;
          padding: 5px 9px;
          color: var(--muted);
          font-size: 12px;
          font-weight: 700;
        }}
        .post[open] .expand-label {{ color: var(--accent); border-color: var(--accent); }}
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
          margin-top: 0;
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
          .topbar {{ position: static; display: grid; gap: 12px; padding-bottom: 14px; }}
          .top-actions {{ align-items: stretch; flex-wrap: wrap; }}
          .app-grid {{ grid-template-columns: 1fr; }}
          .sidebar {{ position: static; grid-template-columns: repeat(3, 1fr); }}
          .sidebar a {{ text-align: center; }}
          .settings {{ display: block; }}
          .settings label, .settings button {{ margin-top: 12px; }}
          .template-grid, .inline-form {{ grid-template-columns: 1fr; }}
          .status-grid {{ grid-template-columns: 1fr; }}
          .status-grid .wide {{ grid-column: span 1; }}
          .schedule-card {{ grid-template-columns: 1fr; }}
          .post summary {{ grid-template-columns: 70px minmax(0, 1fr); }}
          .post-thumb {{ width: 70px; height: 54px; }}
          .expand-label {{ display: none; }}
        }}
      </style>
    </head>
    <body><main>{content}</main></body>
    </html>
    """


def page(title: str, content: str, active: str = "overview") -> str:
    env = read_env()
    theme_mode = env.get("UI_THEME_MODE", "light")
    dark = theme_mode == "dark"
    palette = {
        "bg": "#0f172a" if dark else "#f8fafc",
        "surface": "#111827" if dark else "#ffffff",
        "surface_2": "#1f2937" if dark else "#f8fafc",
        "surface_3": "#334155" if dark else "#f1f5f9",
        "text": "#f8fafc" if dark else "#0f172a",
        "muted": "#cbd5e1" if dark else "#64748b",
        "border": "#334155" if dark else "#e2e8f0",
        "field": "#0f172a" if dark else "#ffffff",
        "primary": "#f8fafc" if dark else "#0f172a",
        "on_primary": "#0f172a" if dark else "#ffffff",
        "primary_hover": "#e2e8f0" if dark else "#1e293b",
        "focus": "#334155" if dark else "#e2e8f0",
        "alert_bg": "#331c1c" if dark else "#fff1f2",
        "alert_border": "#7f1d1d" if dark else "#fecdd3",
    }
    run_label = "Running..." if RUN_STATUS.running else "Run Now"
    run_disabled = "disabled" if RUN_STATUS.running else ""
    return f"""
    <!doctype html>
    <html class="{esc(theme_mode)}" lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta http-equiv="refresh" content="20">
      <title>{esc(title)}</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
      <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
      <style>
        :root {{
          --bg: {palette["bg"]};
          --surface: {palette["surface"]};
          --surface-2: {palette["surface_2"]};
          --surface-3: {palette["surface_3"]};
          --text: {palette["text"]};
          --muted: {palette["muted"]};
          --border: {palette["border"]};
          --field-bg: {palette["field"]};
          --primary: {palette["primary"]};
          --on-primary: {palette["on_primary"]};
          --primary-hover: {palette["primary_hover"]};
          --focus: {palette["focus"]};
          --alert-bg: {palette["alert_bg"]};
          --alert-border: {palette["alert_border"]};
          --accent: var(--primary);
          --danger: #ba1a1a;
          --ok: #047857;
          --warn: #b45309;
          --sidebar-width: 240px;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
          font-size: 14px;
          line-height: 1.5;
        }}
        a {{ color: inherit; text-decoration: none; }}
        h1, h2, h3 {{ margin: 0; line-height: 1.25; letter-spacing: 0; }}
        h1 {{ font-size: 20px; font-weight: 600; }}
        h2 {{ font-size: 24px; font-weight: 600; letter-spacing: 0; }}
        h3 {{ font-size: 15px; font-weight: 600; }}
        .material-symbols-outlined {{
          font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;
          display: inline-block;
          vertical-align: middle;
        }}
        .shell-sidebar {{
          position: fixed;
          left: 0;
          top: 0;
          z-index: 40;
          width: var(--sidebar-width);
          height: 100vh;
          display: flex;
          flex-direction: column;
          gap: 4px;
          padding: 16px;
          background: var(--surface);
          border-right: 1px solid var(--border);
        }}
        .brand {{ padding: 0 8px; margin-bottom: 28px; }}
        .brand p {{ margin: 3px 0 0; color: var(--muted); font-size: 12px; font-weight: 600; }}
        .shell-nav {{ display: grid; gap: 4px; }}
        .shell-nav a, .shell-settings {{
          display: flex;
          align-items: center;
          gap: 12px;
          border-radius: 6px;
          padding: 10px 12px;
          color: var(--muted);
          font-weight: 500;
          transition: background .15s, color .15s;
        }}
        .shell-nav a:hover, .shell-nav a.active, .shell-settings:hover, .shell-settings.active {{ background: var(--surface-3); color: var(--text); }}
        .shell-nav a.active {{ font-weight: 600; }}
        .sidebar-footer {{ margin-top: auto; border-top: 1px solid var(--border); padding-top: 14px; }}
        .avatar-row {{ display: flex; align-items: center; gap: 10px; padding: 10px 12px; color: var(--muted); }}
        .avatar-row strong {{ color: var(--text); font-size: 13px; }}
        .avatar {{ width: 32px; height: 32px; border-radius: 999px; background: var(--surface-3); display: grid; place-items: center; color: var(--text); }}
        .shell-topbar {{
          position: fixed;
          top: 0;
          right: 0;
          left: var(--sidebar-width);
          z-index: 30;
          height: 64px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 20px;
          padding: 0 24px;
          background: var(--surface);
          border-bottom: 1px solid var(--border);
        }}
        .searchbox {{ position: relative; width: min(430px, 42vw); }}
        .searchbox span {{ position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--muted); }}
        .searchbox input {{ padding-left: 40px; margin: 0; background: var(--surface-2); border-color: var(--border); }}
        .top-actions, .action-row {{ display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }}
        .app-main {{ margin-left: var(--sidebar-width); padding-top: 64px; min-height: 100vh; }}
        main {{ width: min(1280px, calc(100vw - var(--sidebar-width) - 64px)); margin: 0 auto; padding: 32px 0 48px; }}
        .workspace {{ display: grid; gap: 24px; }}
        .section {{
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 24px;
        }}
        .section-head {{
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 18px;
          padding-bottom: 18px;
          border-bottom: 1px solid var(--border);
        }}
        .section-copy {{ margin: 6px 0 0; color: var(--muted); }}
        .eyebrow {{ margin: 0 0 6px; color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 500; letter-spacing: .02em; }}
        .themebar {{ display: flex; align-items: center; gap: 10px; }}
        .theme-label {{ color: var(--muted); font-size: 12px; font-weight: 500; }}
        .theme-controls {{ display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; background: var(--surface); }}
        .seg {{ border: 0; border-right: 1px solid var(--border); padding: 8px 11px; background: var(--field-bg); color: var(--text); cursor: pointer; font-weight: 500; font-size: 13px; }}
        .seg:last-child {{ border-right: 0; }}
        .seg.active {{ background: var(--primary); color: var(--on-primary); }}
        .primary, .primary-link {{
          border: 1px solid var(--primary);
          border-radius: 6px;
          padding: 9px 14px;
          background: var(--primary);
          color: var(--on-primary);
          font-weight: 600;
          cursor: pointer;
        }}
        .primary:hover, .primary-link:hover {{ background: var(--primary-hover); border-color: var(--primary-hover); }}
        .primary:disabled {{ opacity: .55; cursor: not-allowed; }}
        .secondary-link {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 9px 14px;
          background: var(--surface);
          color: var(--text);
          font-weight: 500;
          cursor: pointer;
        }}
        .secondary-link:hover {{ background: var(--surface-2); }}
        .status-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 20px; }}
        .status-grid div, .schedule-card, .template-card, .toggle {{
          border: 1px solid var(--border);
          border-radius: 6px;
          background: var(--surface-2);
        }}
        .status-grid div {{ min-height: 72px; padding: 14px; }}
        .status-grid .wide {{ grid-column: span 4; }}
        .status-grid span, label {{ display: block; color: var(--muted); font-size: 13px; font-weight: 500; }}
        .status-grid strong {{ display: block; margin-top: 5px; color: var(--text); font-size: 14px; overflow-wrap: anywhere; }}
        .schedule-card {{ display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 12px; align-items: center; padding: 13px 14px; margin-top: 14px; }}
        .schedule-card span {{ color: var(--muted); font-size: 13px; }}
        .schedule-card strong {{ overflow-wrap: anywhere; }}
        .settings {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 16px; }}
        .settings .full {{ grid-column: 1 / -1; }}
        .settings-block-grid {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 16px;
          margin-top: 18px;
        }}
        .settings-block {{
          border: 1px solid var(--border);
          border-radius: 8px;
          background: var(--surface);
          overflow: hidden;
        }}
        .settings-block-head {{
          display: grid;
          grid-template-columns: 42px minmax(0, 1fr) auto;
          gap: 14px;
          align-items: start;
          padding: 18px;
          border-bottom: 1px solid var(--border);
          background: var(--surface-2);
        }}
        .block-icon {{
          width: 42px;
          height: 42px;
          border: 1px solid var(--border);
          border-radius: 8px;
          display: grid;
          place-items: center;
          background: var(--surface);
          color: var(--text);
        }}
        .settings-block h3 {{ font-size: 16px; font-weight: 700; }}
        .settings-block p {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}
        .connection-badge {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 4px 8px;
          color: var(--muted);
          background: var(--surface);
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
          white-space: nowrap;
        }}
        .connection-badge.connected {{ border-color: #bbf7d0; background: #ecfdf5; color: #047857; }}
        .settings-drawer {{ border-top: 0; }}
        .settings-drawer summary {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 14px 18px;
          cursor: pointer;
          list-style: none;
          color: var(--text);
          font-weight: 700;
        }}
        .settings-drawer summary::-webkit-details-marker {{ display: none; }}
        .settings-drawer summary:hover {{ background: var(--surface-2); }}
        .settings-drawer summary .material-symbols-outlined {{ font-size: 20px; color: var(--muted); }}
        .settings-drawer[open] summary .material-symbols-outlined {{ transform: rotate(45deg); }}
        .integration-form {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 14px;
          padding: 0 18px 18px;
        }}
        .integration-form .full {{ grid-column: 1 / -1; }}
        .integration-form button {{ align-self: end; }}
        .platform-list {{ display: grid; gap: 0; }}
        .platform-placeholder {{
          display: grid;
          grid-template-columns: 36px minmax(0, 1fr) auto;
          align-items: center;
          gap: 12px;
          padding: 14px 18px;
          border-top: 1px solid var(--border);
          color: var(--muted);
        }}
        .platform-placeholder strong {{ display: block; color: var(--text); font-size: 14px; }}
        .platform-placeholder span:not(.material-symbols-outlined) {{ display: block; font-size: 12px; }}
        .platform-placeholder button {{
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 7px 10px;
          background: var(--surface-2);
          color: var(--muted);
        }}
        .schedule-settings {{ grid-template-columns: repeat(4, minmax(0, 1fr)); align-items: end; }}
        .schedule-meta {{
          display: grid;
          gap: 2px;
          min-width: 220px;
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 10px 12px;
          background: var(--surface-2);
        }}
        .schedule-meta span, .form-note {{
          color: var(--muted);
          font-size: 12px;
          font-weight: 500;
        }}
        .schedule-meta strong {{ color: var(--text); font-size: 13px; overflow-wrap: anywhere; }}
        .form-note {{
          grid-column: 1 / -2;
          display: flex;
          align-items: center;
          gap: 8px;
          border: 1px solid var(--border);
          border-radius: 6px;
          background: var(--surface-2);
          padding: 10px 12px;
        }}
        .form-note .material-symbols-outlined {{ font-size: 18px; }}
        .form-section {{ grid-column: 1 / -1; margin-top: 8px; padding-top: 12px; border-top: 1px solid var(--border); color: var(--text); font-size: 14px; font-weight: 600; }}
        .form-section:first-child {{ margin-top: 0; padding-top: 0; border-top: 0; }}
        input, select, textarea {{ width: 100%; margin-top: 6px; border: 1px solid var(--border); border-radius: 6px; padding: 9px 10px; font: inherit; color: var(--text); background: var(--field-bg); }}
        input:focus, select:focus, textarea:focus {{ border-color: var(--primary); box-shadow: 0 0 0 2px var(--focus); outline: none; }}
        .toggle {{ display: flex; gap: 10px; align-items: center; color: var(--text); padding: 9px 10px; }}
        .toggle input {{ width: auto; margin: 0; }}
        .settings button {{ align-self: end; }}
        .studio-actions {{ align-self: end; }}
        .studio-actions button:not(.primary), .inline-form button:not(.primary), .post-actions button {{ border: 1px solid var(--border); border-radius: 6px; padding: 9px 14px; background: var(--field-bg); color: var(--text); font-weight: 600; cursor: pointer; }}
        .template-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }}
        .template-card {{ padding: 16px; }}
        .template-card p {{ margin: 6px 0 10px; color: var(--muted); font-size: 13px; }}
        .node-chain {{ color: var(--text); font-size: 12px; overflow-wrap: anywhere; font-family: "JetBrains Mono", monospace; }}
        .node-catalog {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
        .node-pill {{ display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--border); border-radius: 6px; padding: 7px 10px; background: var(--field-bg); font-weight: 600; font-size: 12px; }}
        .node-pill small {{ color: var(--muted); font-weight: 500; }}
        .inline-form {{ display: grid; grid-template-columns: minmax(160px, 1fr) minmax(190px, 1fr) minmax(190px, 1fr) auto; gap: 10px; align-items: end; margin-top: 16px; }}
        .table-wrap {{ overflow-x: auto; margin-top: 14px; }}
        table {{ width: 100%; border-collapse: collapse; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; font-size: 13px; }}
        th, td {{ border-bottom: 1px solid var(--border); padding: 10px 12px; text-align: left; }}
        th {{ background: var(--surface-2); color: var(--muted); font-weight: 500; text-transform: uppercase; font-size: 12px; letter-spacing: .02em; }}
        td {{ color: var(--text); }}
        .alert {{ border: 1px solid var(--alert-border); background: var(--alert-bg); color: var(--danger); border-radius: 8px; padding: 14px 16px; margin-bottom: 18px; font-weight: 700; }}
        .history {{ display: grid; gap: 10px; margin-top: 16px; }}
        .post {{ border: 1px solid var(--border); border-radius: 8px; background: var(--surface); overflow: hidden; }}
        .post[open] {{ border-color: var(--text); }}
        .post summary {{ display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 12px; cursor: pointer; list-style: none; }}
        .post summary::-webkit-details-marker {{ display: none; }}
        .post summary:hover {{ background: var(--surface-2); }}
        .post-thumb {{ width: 92px; height: 62px; border: 1px solid var(--border); border-radius: 6px; object-fit: cover; background: var(--field-bg); }}
        .post-thumb.placeholder {{ display: grid; place-items: center; color: var(--muted); font-size: 11px; font-weight: 500; }}
        .post-summary {{ display: grid; gap: 6px; min-width: 0; }}
        .post-title {{ color: var(--text); font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .post-detail {{ border-top: 1px solid var(--border); padding: 14px; }}
        .post-detail p {{ margin: 0; white-space: pre-wrap; color: var(--text); }}
        .post-meta {{ display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 12px; }}
        .expand-label {{ border: 1px solid var(--border); border-radius: 6px; padding: 5px 9px; color: var(--muted); font-size: 12px; font-weight: 500; }}
        .post[open] .expand-label {{ color: var(--text); border-color: var(--text); }}
        .post-actions {{ margin-top: 12px; }}
        .post-actions textarea {{ width: 100%; min-height: 62px; resize: vertical; margin-top: 0; }}
        .post-actions button:hover {{ border-color: var(--text); color: var(--text); background: var(--surface-2); }}
        .empty {{ color: var(--muted); margin-bottom: 0; }}
        .dashboard-page {{ display: flex; flex-direction: column; gap: 32px; }}
        .dashboard-head {{
          display: flex;
          align-items: flex-end;
          justify-content: space-between;
          gap: 24px;
        }}
        .dashboard-head h2 {{ font-size: 24px; font-weight: 600; }}
        .dashboard-head p {{ margin: 5px 0 0; color: var(--muted); font-size: 14px; }}
        .schedule-shortcut {{
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border-radius: 6px;
          background: var(--surface-3);
          color: var(--text);
          padding: 9px 14px;
          font-weight: 600;
          font-size: 13px;
        }}
        .schedule-shortcut:hover {{ background: var(--border); }}
        .schedule-shortcut .material-symbols-outlined {{ font-size: 18px; }}
        .overview-grid {{
          display: grid;
          grid-template-columns: minmax(0, 1.05fr) minmax(280px, 1.05fr) minmax(280px, 1fr);
          gap: 24px;
          align-items: stretch;
        }}
        .overview-card, .metric-card, .workspace-table, .activity-card {{
          border: 1px solid var(--border);
          border-radius: 8px;
          background: var(--surface);
        }}
        .status-panel {{
          min-height: 260px;
          padding: 26px 24px 22px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          gap: 24px;
        }}
        .panel-topline, .panel-footer {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }}
        .panel-label {{
          color: var(--muted);
          font-size: 12px;
          font-weight: 600;
          letter-spacing: .06em;
          text-transform: uppercase;
        }}
        .status-badge {{
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 4px 9px;
          border-radius: 0;
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
        }}
        .status-badge i {{
          width: 6px;
          height: 6px;
          border-radius: 999px;
          display: inline-block;
        }}
        .status-badge.active {{ background: #ecfdf5; color: #047857; }}
        .status-badge.active i {{ background: #10b981; }}
        .status-badge.failed {{ background: #fee2e2; color: #b91c1c; }}
        .status-badge.failed i {{ background: #ef4444; }}
        .status-panel h3 {{ font-size: 32px; line-height: 40px; font-weight: 600; letter-spacing: -0.02em; }}
        .status-panel p {{ margin: 6px 0 0; color: var(--muted); font-size: 14px; max-width: 34ch; }}
        .panel-footer {{ border-top: 1px solid var(--border); padding-top: 14px; }}
        .panel-footer span:first-child {{ display: block; color: var(--muted); font-size: 11px; font-weight: 600; text-transform: uppercase; }}
        .panel-footer strong {{ display: block; margin-top: 2px; font-weight: 500; }}
        .panel-footer .material-symbols-outlined {{ color: var(--muted); }}
        .next-run-panel {{
          min-height: 260px;
          padding: 28px 24px 24px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          gap: 16px;
          background: #000;
          color: #fff;
          overflow: hidden;
        }}
        .next-run-panel .panel-label {{ color: #7c839b; }}
        .next-run-panel strong {{ display: block; font-size: 48px; line-height: 1; font-weight: 800; letter-spacing: -0.03em; }}
        .next-run-panel p {{ margin: 0; color: rgba(255, 255, 255, .76); font-weight: 600; max-width: 30ch; }}
        .next-run-panel button {{
          width: 100%;
          border: 1px solid rgba(255, 255, 255, .22);
          border-radius: 0;
          padding: 12px 14px;
          background: rgba(255, 255, 255, .1);
          color: #fff;
          font: inherit;
          font-weight: 700;
          cursor: pointer;
        }}
        .next-run-panel button:hover {{ background: rgba(255, 255, 255, .16); }}
        .metric-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
        .metric-card {{
          min-height: 122px;
          padding: 22px 20px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
        }}
        .metric-card span {{
          color: var(--muted);
          font-size: 12px;
          font-weight: 600;
          letter-spacing: .04em;
          text-transform: uppercase;
        }}
        .metric-card strong {{ font-size: 26px; line-height: 1; font-weight: 700; }}
        .metric-card.danger span, .metric-card.danger strong {{ color: #dc2626; }}
        .workspace-grid {{ display: grid; grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr); gap: 24px; align-items: start; }}
        .workspace-table-block, .activity-block {{ display: flex; flex-direction: column; gap: 16px; }}
        .block-title {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 0 8px;
        }}
        .block-title h3 {{ font-size: 20px; font-weight: 600; }}
        .block-title a {{ font-size: 13px; font-weight: 600; color: var(--text); }}
        .block-title a:hover {{ text-decoration: underline; }}
        .workspace-table {{ overflow: hidden; }}
        .workspace-table table {{ border: 0; border-radius: 0; }}
        .workspace-table th {{ padding: 14px 24px; background: var(--surface-3); }}
        .workspace-table td {{ padding: 18px 24px; vertical-align: middle; }}
        .source-cell {{ display: flex; align-items: center; gap: 16px; min-width: 260px; }}
        .source-cell .post-thumb {{ width: 40px; height: 40px; border-radius: 4px; flex: 0 0 auto; }}
        .source-cell strong {{ display: block; max-width: 260px; font-weight: 700; line-height: 1.35; }}
        .source-cell span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }}
        .status-chip {{
          display: inline-flex;
          align-items: center;
          padding: 3px 8px;
          border-radius: 3px;
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
        }}
        .status-chip.drafting {{ background: #dbeafe; color: #475569; }}
        .status-chip.scheduled {{ background: #fed7aa; color: #7c2d12; }}
        .status-chip.published {{ background: #d1fae5; color: #047857; }}
        .status-chip.failed {{ background: #fecaca; color: #b91c1c; }}
        .platform-cell {{ display: inline-flex; align-items: center; gap: 6px; color: var(--muted); }}
        .platform-cell .material-symbols-outlined {{ font-size: 17px; }}
        .row-actions {{ text-align: right; }}
        .row-actions a {{
          display: inline-grid;
          place-items: center;
          width: 32px;
          height: 32px;
          border-radius: 999px;
          color: var(--muted);
        }}
        .row-actions a:hover {{ background: var(--surface-3); color: var(--text); }}
        .empty-workspace {{ padding: 28px; text-align: center; color: var(--muted); }}
        .empty-workspace .material-symbols-outlined {{ font-size: 28px; }}
        .empty-workspace strong {{ display: block; color: var(--text); margin-top: 8px; }}
        .empty-workspace p {{ margin: 4px 0 0; }}
        .activity-card {{ padding: 26px 24px; display: flex; flex-direction: column; gap: 24px; }}
        .activity-item {{ display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 16px; position: relative; }}
        .activity-item:not(:last-child)::after {{
          content: "";
          position: absolute;
          left: 15px;
          top: 34px;
          bottom: -24px;
          width: 1px;
          background: var(--border);
        }}
        .activity-icon {{
          position: relative;
          z-index: 1;
          width: 32px;
          height: 32px;
          border-radius: 999px;
          display: grid;
          place-items: center;
          background: #dbeafe;
          color: #475569;
        }}
        .activity-icon .material-symbols-outlined {{ font-size: 16px; }}
        .activity-item.success .activity-icon {{ background: #d1fae5; color: #047857; }}
        .activity-item.error .activity-icon {{ background: #fee2e2; color: #dc2626; }}
        .activity-item.neutral .activity-icon {{ background: #e5e7eb; color: #374151; }}
        .activity-item p {{ margin: 0; color: var(--text); font-size: 14px; line-height: 1.35; }}
        .activity-item span {{ display: block; margin-top: 6px; color: var(--muted); font-size: 11px; font-weight: 500; }}
        @media (max-width: 900px) {{
          .shell-sidebar {{ position: static; width: auto; height: auto; border-right: 0; border-bottom: 1px solid var(--border); }}
          .shell-nav {{ grid-template-columns: repeat(2, 1fr); }}
          .sidebar-footer {{ display: none; }}
          .shell-topbar {{ position: static; left: 0; height: auto; padding: 14px 16px; align-items: stretch; flex-direction: column; }}
          .searchbox {{ width: 100%; }}
          .top-actions {{ width: 100%; }}
          .app-main {{ margin-left: 0; padding-top: 0; }}
          main {{ width: min(100vw - 24px, 1180px); padding: 18px 0 32px; }}
          .section-head {{ display: grid; }}
          .settings {{ display: block; }}
          .settings-block-grid {{ grid-template-columns: 1fr; }}
          .settings-block-head {{ grid-template-columns: 42px minmax(0, 1fr); }}
          .connection-badge {{ grid-column: 2; justify-self: start; }}
          .integration-form {{ grid-template-columns: 1fr; }}
          .integration-form .full {{ grid-column: auto; }}
          .schedule-settings {{ display: grid; grid-template-columns: 1fr; }}
          .form-note {{ grid-column: auto; }}
          .settings label, .settings button {{ margin-top: 12px; }}
          .template-grid, .inline-form {{ grid-template-columns: 1fr; }}
          .status-grid {{ grid-template-columns: 1fr; }}
          .status-grid .wide {{ grid-column: span 1; }}
          .schedule-card {{ grid-template-columns: 1fr; }}
          .post summary {{ grid-template-columns: 70px minmax(0, 1fr); }}
          .post-thumb {{ width: 70px; height: 54px; }}
          .expand-label {{ display: none; }}
          .dashboard-head, .workspace-grid {{ grid-template-columns: 1fr; display: grid; }}
          .overview-grid {{ grid-template-columns: 1fr; }}
          .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
          .next-run-panel strong {{ font-size: 40px; }}
        }}
      </style>
    </head>
    <body>
      <aside class="shell-sidebar">
        <div class="brand">
          <h1>AutoPost AI</h1>
          <p>Media Operations</p>
        </div>
        {sidebar_nav(active)}
        <div class="sidebar-footer">
          <a class="shell-settings {"active" if active == "settings" else ""}" href="/settings"><span class="material-symbols-outlined">settings</span><span>Settings</span></a>
          <div class="avatar-row">
            <div class="avatar"><span class="material-symbols-outlined">person</span></div>
            <div><strong>Operator</strong><br><small>Local Console</small></div>
          </div>
        </div>
      </aside>
      <header class="shell-topbar">
        <div class="searchbox">
          <span class="material-symbols-outlined">search</span>
          <input placeholder="Search operations, news, or drafts..." type="text">
        </div>
        <div class="top-actions">
          {theme_bar(env)}
          <form method="post" action="/run">
            <button class="secondary-link" type="submit" {run_disabled}>{run_label}</button>
          </form>
          <a class="primary-link" href="/studio">Create Content</a>
        </div>
      </header>
      <div class="app-main"><main>{content}</main></div>
    </body>
    </html>
    """


def sidebar_nav(active: str) -> str:
    items = [
        ("overview", "/overview", "dashboard", "Overview"),
        ("studio", "/studio", "auto_awesome", "Content Studio"),
        ("content", "/content", "library_books", "Content Library"),
        ("schedule", "/schedule", "calendar_today", "Schedule"),
        ("workflows", "/workflows", "account_tree", "Workflows"),
        ("ai-news", "/ai-news", "newspaper", "AI News"),
        ("history", "/history", "history", "Run History"),
    ]
    return '<nav class="shell-nav">' + "\n".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">'
        f'<span class="material-symbols-outlined">{icon}</span><span>{label}</span></a>'
        for key, href, icon, label in items
    ) + "</nav>"


def normalize_color(value: str | None) -> str:
    if not value:
        return "#1264a3"
    value = value.strip()
    if len(value) == 7 and value.startswith("#"):
        allowed = "0123456789abcdefABCDEF"
        if all(char in allowed for char in value[1:]):
            return value
    return "#1264a3"
