import pytest
from pydantic import ValidationError

from ai_news_agent.config import Settings


def test_interval_schedule_accepts_hours_and_minutes() -> None:
    settings = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        TELEGRAM_BOT_TOKEN="test",
        TELEGRAM_APPROVER_CHAT_ID="test",
        SCHEDULE_MODE="interval",
        SCHEDULE_INTERVAL_HOURS=2,
        SCHEDULE_INTERVAL_MINUTES=30,
    )

    assert settings.schedule_mode == "interval"
    assert settings.schedule_interval_hours == 2
    assert settings.schedule_interval_minutes == 30


def test_interval_schedule_requires_positive_duration() -> None:
    with pytest.raises(ValidationError):
        Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="test",
            TELEGRAM_BOT_TOKEN="test",
            TELEGRAM_APPROVER_CHAT_ID="test",
            SCHEDULE_MODE="interval",
            SCHEDULE_INTERVAL_HOURS=0,
            SCHEDULE_INTERVAL_MINUTES=0,
        )
