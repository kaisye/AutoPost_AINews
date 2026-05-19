from __future__ import annotations

import argparse
import json
import logging
import uuid

from openai import OpenAIError
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError

from ai_news_agent.config import get_settings
from ai_news_agent.facebook import FacebookPublisher
from ai_news_agent.llm import PostWriter, explain_openai_error
from ai_news_agent.memory import AgentMemory
from ai_news_agent.workflow import AINewsWorkflow


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def run_once() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    workflow = AINewsWorkflow(settings)
    run_id = f"ai-news-{uuid.uuid4()}"
    try:
        state = workflow.run(run_id=run_id)
    except OpenAIError as exc:
        logging.getLogger(__name__).error(explain_openai_error(exc))
        raise SystemExit(2) from exc
    approval = state.get("approval")
    logging.getLogger(__name__).info("Run finished. approval=%s", approval)


def run_daemon() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    scheduler = BlockingScheduler()
    trigger = schedule_trigger(settings)
    scheduler.add_job(run_once, trigger=trigger, id="ai-news-agent", replace_existing=True)
    logging.getLogger(__name__).info("Scheduler started with %s schedule: %s", settings.schedule_mode, trigger)
    scheduler.start()


def run_ui(host: str, port: int) -> None:
    import uvicorn

    configure_logging("INFO")
    uvicorn.run("ai_news_agent.ui:app", host=host, port=port, reload=False)


def schedule_trigger(settings):
    if settings.schedule_mode == "interval":
        return IntervalTrigger(
            hours=settings.schedule_interval_hours,
            minutes=settings.schedule_interval_minutes,
        )
    return CronTrigger.from_crontab(settings.schedule_cron)


def repost(post_id: int, rewrite_instruction: str | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    memory = AgentMemory(settings.database_path)
    record = memory.post_record(post_id)
    if not record:
        raise SystemExit(f"Post #{post_id} was not found in memory.")
    if not settings.facebook_enabled:
        raise SystemExit("FACEBOOK_ENABLED must be true before reposting.")

    original_text = str(record["post_text"])
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
    logging.getLogger(__name__).info("Reposted post #%s to Facebook id=%s", post_id, facebook_post_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI news LangGraph agent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run-once", help="Run the workflow once")
    subparsers.add_parser("daemon", help="Run the scheduled automation")
    repost_parser = subparsers.add_parser("repost", help="Repost a stored memory post to Facebook")
    repost_parser.add_argument("--post-id", required=True, type=int)
    repost_parser.add_argument("--rewrite", help="Rewrite instruction before reposting")
    ui_parser = subparsers.add_parser("ui", help="Run the local admin UI")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", default=8787, type=int)
    args = parser.parse_args()

    try:
        if args.command == "ui":
            run_ui(args.host, args.port)
        elif args.command == "daemon":
            run_daemon()
        elif args.command == "repost":
            repost(args.post_id, args.rewrite)
        else:
            run_once()
    except ValidationError as exc:
        print("Configuration error. Please update .env before running.")
        for error in exc.errors():
            print(f"- {error['msg']}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
