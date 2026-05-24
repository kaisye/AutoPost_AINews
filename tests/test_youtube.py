from pathlib import Path

from ai_news_agent.config import Settings
from ai_news_agent.models import MediaType
from ai_news_agent.platform import MediaPlatform
from ai_news_agent.youtube import YouTubePublisher


def test_youtube_publisher_noops_when_disabled(tmp_path: Path) -> None:
    settings = Settings(
        NVIDIA_API_KEY="test",
        TELEGRAM_BOT_TOKEN="test",
        TELEGRAM_APPROVER_CHAT_ID="1",
        DATABASE_PATH=tmp_path / "memory.sqlite3",
    )

    assert YouTubePublisher(settings).publish_content(_youtube_content()) is None


def test_youtube_publisher_requires_credentials(tmp_path: Path) -> None:
    settings = Settings(
        NVIDIA_API_KEY="test",
        TELEGRAM_BOT_TOKEN="test",
        TELEGRAM_APPROVER_CHAT_ID="1",
        DATABASE_PATH=tmp_path / "memory.sqlite3",
        YOUTUBE_ENABLED=True,
    )

    try:
        YouTubePublisher(settings).publish_content(_youtube_content())
    except ValueError as exc:
        assert "YOUTUBE_CLIENT_ID" in str(exc)
    else:
        raise AssertionError("Expected missing YouTube credentials to fail.")


def test_media_platform_routes_youtube_channel(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        NVIDIA_API_KEY="test",
        TELEGRAM_BOT_TOKEN="test",
        TELEGRAM_APPROVER_CHAT_ID="1",
        DATABASE_PATH=tmp_path / "memory.sqlite3",
        YOUTUBE_ENABLED=True,
        YOUTUBE_CLIENT_ID="client",
        YOUTUBE_CLIENT_SECRET="secret",
        YOUTUBE_REFRESH_TOKEN="refresh",
    )
    platform = MediaPlatform(settings)

    class FakeYouTube:
        def publish_content(self, content):
            assert content.channel == "youtube"
            return "yt_123"

    platform.youtube = FakeYouTube()
    content_id = platform.create_manual_content(
        title="Demo video",
        body="Video description",
        channel="youtube",
        media_type=MediaType.VIDEO,
        media_url="https://example.com/video.mp4",
    )

    platform_id = platform.publish_content(content_id)
    content = platform.memory.content_item(content_id)

    assert platform_id == "yt_123"
    assert content is not None
    assert content.platform_post_id == "yt_123"
    assert content.facebook_post_id is None


def _youtube_content():
    from ai_news_agent.models import ContentItem

    return ContentItem(
        title="Demo video",
        body="Video description",
        channel="youtube",
        media_type=MediaType.VIDEO,
        media_url="https://example.com/video.mp4",
    )
