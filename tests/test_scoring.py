from __future__ import annotations

from pathlib import Path

from ai_news_agent.config import Settings
from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import Article
from ai_news_agent.news import NewsCollector


def test_higher_engagement_scores_above_plain_article(tmp_path: Path) -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        LLM_PROVIDER="openai",
        TELEGRAM_BOT_TOKEN="test",
        TELEGRAM_APPROVER_CHAT_ID="1",
        DATABASE_PATH=tmp_path / "memory.sqlite3",
        RSS_FEEDS=[],
    )
    collector = NewsCollector(settings, AgentMemory(settings.database_path))
    high = Article(
        title="OpenAI launches new AI agent model",
        url="https://example.com/high",
        source="Test",
        raw_engagement={"points": 500, "comments": 120},
    )
    low = Article(
        title="Small AI update",
        url="https://example.com/low",
        source="Test",
        raw_engagement={},
    )

    ranked = collector.rank([low, high])

    assert ranked[0].url == high.url
