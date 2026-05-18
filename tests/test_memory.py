from pathlib import Path

from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import ApprovalResult, ApprovalStatus, Article, FacebookDraft


def test_posted_article_urls_are_remembered(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path / "memory.sqlite3")
    article = Article(title="AI News", url="https://example.com/a?ref=x", source="Test")
    draft = FacebookDraft(hook="Hook", body="Body", hashtags=["#AI"], sources=["AI News - url"])

    memory.remember_post(draft, [article], ApprovalResult(status=ApprovalStatus.APPROVED))

    assert "https://example.com/a" in memory.posted_article_urls()


def test_similar_post_is_duplicate(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path / "memory.sqlite3")
    article = Article(title="AI News", url="https://example.com/a", source="Test")
    draft = FacebookDraft(
        hook="AI agents are changing enterprise software",
        body="This analysis explains why AI agents are changing enterprise software and operations.",
        hashtags=["#AI"],
        sources=["AI News - url"],
    )
    memory.remember_post(draft, [article], ApprovalResult(status=ApprovalStatus.APPROVED))

    assert memory.is_duplicate_post(draft)
