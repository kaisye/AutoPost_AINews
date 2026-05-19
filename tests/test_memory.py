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


def test_repost_is_stored_with_original_reference(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path / "memory.sqlite3")

    memory.remember_repost(
        post_text="Original post",
        article_urls=["https://example.com/a"],
        original_post_id=7,
        facebook_post_id="fb_123",
    )

    record = memory.recent_post_records(limit=1)[0]
    assert record["status"] == "approved"
    assert record["post_text"] == "Original post"
    assert record["approval_feedback"] == "Reposted from post #7"
    assert record["facebook_post_id"] == "fb_123"
