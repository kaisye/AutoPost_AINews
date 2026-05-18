from __future__ import annotations

import argparse
import logging
import uuid

from openai import OpenAIError
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError

from ai_news_agent.config import get_settings
from ai_news_agent.llm import explain_openai_error
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
    trigger = CronTrigger.from_crontab(settings.schedule_cron)
    scheduler.add_job(run_once, trigger=trigger, id="ai-news-agent", replace_existing=True)
    logging.getLogger(__name__).info("Scheduler started with cron: %s", settings.schedule_cron)
    scheduler.start()


def run_ui(host: str, port: int) -> None:
    import uvicorn

    configure_logging("INFO")
    uvicorn.run("ai_news_agent.ui:app", host=host, port=port, reload=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI news LangGraph agent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run-once", help="Run the workflow once")
    subparsers.add_parser("daemon", help="Run the scheduled automation")
    ui_parser = subparsers.add_parser("ui", help="Run the local admin UI")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", default=8787, type=int)
    args = parser.parse_args()

    try:
        if args.command == "ui":
            run_ui(args.host, args.port)
        elif args.command == "daemon":
            run_daemon()
        else:
            run_once()
    except ValidationError as exc:
        print("Configuration error. Please update .env before running.")
        for error in exc.errors():
            print(f"- {error['msg']}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
