from __future__ import annotations

from datetime import datetime, timezone

from pydantic import HttpUrl, TypeAdapter

from ai_news_agent.config import Settings
from ai_news_agent.facebook import FacebookPublisher
from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import (
    ContentItem,
    ContentStatus,
    MediaAsset,
    MediaType,
    ScheduleJob,
    ScheduleStatus,
)
from ai_news_agent.youtube import YouTubePublisher


class MediaPlatform:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = AgentMemory(settings.database_path)
        self.facebook = FacebookPublisher(settings)
        self.youtube = YouTubePublisher(settings)

    def create_manual_content(
        self,
        title: str,
        body: str,
        hook: str = "",
        hashtags: list[str] | None = None,
        sources: list[str] | None = None,
        channel: str = "facebook",
        workflow_id: str = "manual-post",
        media_type: MediaType | None = None,
        media_url: str | None = None,
        alt_text: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> int:
        media_asset_id = None
        parsed_media_url = TypeAdapter(HttpUrl).validate_python(media_url) if media_url else None
        if media_type and parsed_media_url:
            media_asset_id = self.memory.create_media_asset(
                MediaAsset(
                    media_type=media_type,
                    url=parsed_media_url,
                    alt_text=alt_text,
                    metadata={"source": "content_studio"},
                )
            )

        status = ContentStatus.SCHEDULED if scheduled_at else ContentStatus.DRAFT
        content_id = self.memory.create_content_item(
            ContentItem(
                title=title.strip(),
                hook=hook.strip(),
                body=body.strip(),
                hashtags=hashtags or [],
                sources=sources or [],
                channel=channel,
                status=status,
                media_asset_id=media_asset_id,
                media_type=media_type if parsed_media_url else None,
                media_url=parsed_media_url,
                workflow_id=workflow_id,
                scheduled_at=scheduled_at,
            )
        )
        if scheduled_at:
            self.schedule_content(content_id, scheduled_at, workflow_id=workflow_id)
        return content_id

    def schedule_content(
        self,
        content_id: int,
        run_at: datetime,
        workflow_id: str = "scheduled-media-post",
        repeat_mode: str = "once",
    ) -> int:
        content = self.memory.content_item(content_id)
        if not content:
            raise ValueError(f"Content item #{content_id} was not found.")
        job_id = self.memory.create_schedule_job(
            ScheduleJob(
                content_id=content_id,
                workflow_id=workflow_id,
                run_at=_ensure_utc(run_at),
                repeat_mode=repeat_mode,
            )
        )
        self.memory.update_content_item(
            content_id,
            status=ContentStatus.SCHEDULED,
            scheduled_at=_ensure_utc(run_at),
            workflow_id=workflow_id,
        )
        return job_id

    def publish_content(self, content_id: int) -> str | None:
        content = self.memory.content_item(content_id)
        if not content:
            raise ValueError(f"Content item #{content_id} was not found.")
        platform_post_id = self._publish_to_channel(content)
        now = datetime.now(timezone.utc)
        facebook_post_id = platform_post_id if content.channel == "facebook" else None
        self.memory.update_content_item(
            content_id,
            status=ContentStatus.PUBLISHED,
            published_at=now,
            facebook_post_id=facebook_post_id,
            platform_post_id=platform_post_id,
        )
        self.memory.remember_repost(
            post_text=content.as_post(),
            article_urls=[],
            original_post_id=content_id,
            facebook_post_id=facebook_post_id,
            image_url=str(content.media_url)
            if content.media_type == MediaType.IMAGE and content.media_url
            else None,
            feedback=f"Published content item #{content_id}",
        )
        return platform_post_id

    def _publish_to_channel(self, content: ContentItem) -> str | None:
        channel = content.channel.strip().lower()
        if channel == "facebook":
            return self.facebook.publish_content(content)
        if channel == "youtube":
            return self.youtube.publish_content(content)
        raise ValueError(f"Unsupported publishing channel: {content.channel}")

    def run_due_schedule_jobs(self, now: datetime | None = None) -> int:
        current = _ensure_utc(now or datetime.now(timezone.utc))
        processed = 0
        for job in self.memory.due_schedule_jobs(current):
            try:
                self.publish_content(job.content_id)
                self.memory.update_schedule_job(
                    job.id or 0,
                    status=ScheduleStatus.COMPLETED,
                    last_run_at=current,
                )
                processed += 1
            except Exception:
                self.memory.update_schedule_job(
                    job.id or 0,
                    status=ScheduleStatus.FAILED,
                    last_run_at=current,
                )
                self.memory.update_content_item(job.content_id, status=ContentStatus.FAILED)
        return processed


def parse_hashtags(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", " ").split() if item.strip()]


def parse_sources(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
