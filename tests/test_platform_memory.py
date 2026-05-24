from datetime import datetime, timezone
from pathlib import Path

from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import ContentItem, ContentStatus, MediaAsset, MediaType, ScheduleJob, ScheduleStatus


def test_content_item_media_and_schedule_are_stored(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path / "memory.sqlite3")
    asset_id = memory.create_media_asset(
        MediaAsset(
            media_type=MediaType.IMAGE,
            url="https://example.com/image.jpg",
            alt_text="AI dashboard",
        )
    )
    content_id = memory.create_content_item(
        ContentItem(
            title="Manual launch post",
            hook="A sharp opening",
            body="A useful body",
            hashtags=["#AI"],
            media_asset_id=asset_id,
            media_type=MediaType.IMAGE,
            media_url="https://example.com/image.jpg",
        )
    )
    run_at = datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc)
    job_id = memory.create_schedule_job(ScheduleJob(content_id=content_id, run_at=run_at))

    content = memory.content_item(content_id)
    jobs = memory.due_schedule_jobs(run_at)

    assert asset_id > 0
    assert job_id > 0
    assert content is not None
    assert content.title == "Manual launch post"
    assert content.media_type == MediaType.IMAGE
    assert jobs[0].content_id == content_id
    assert jobs[0].status == ScheduleStatus.ACTIVE


def test_content_item_status_can_be_updated(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path / "memory.sqlite3")
    content_id = memory.create_content_item(ContentItem(title="Draft", body="Body"))

    memory.update_content_item(content_id, status=ContentStatus.PUBLISHED, facebook_post_id="fb_1")

    content = memory.content_item(content_id)
    assert content is not None
    assert content.status == ContentStatus.PUBLISHED
    assert content.facebook_post_id == "fb_1"
