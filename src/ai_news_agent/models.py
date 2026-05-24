from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDIT_REQUESTED = "edit_requested"
    TIMEOUT = "timeout"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class ContentStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class ScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Article(BaseModel):
    title: str
    url: HttpUrl
    source: str
    published_at: datetime | None = None
    summary: str | None = None
    image_url: HttpUrl | None = None
    author: str | None = None
    raw_engagement: dict[str, float | int | str] = Field(default_factory=dict)
    impact_score: float = 0.0
    relevance_score: float = 0.0
    novelty_score: float = 0.0
    final_score: float = 0.0

    @property
    def normalized_url(self) -> str:
        return str(self.url).split("?")[0].rstrip("/")


class FacebookDraft(BaseModel):
    hook: str
    body: str
    hashtags: list[str]
    sources: list[str]
    image_url: HttpUrl | None = None

    def as_post(self) -> str:
        tags = " ".join(self.hashtags)
        source_lines = "\n".join(f"- {source}" for source in self.sources)
        return f"{self.hook}\n\n{self.body}\n\n{tags}\n\nNguồn:\n{source_lines}".strip()


class ApprovalResult(BaseModel):
    status: ApprovalStatus
    feedback: str | None = None
    telegram_message_id: int | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MediaAsset(BaseModel):
    id: int | None = None
    media_type: MediaType
    url: HttpUrl | None = None
    local_path: str | None = None
    alt_text: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContentItem(BaseModel):
    id: int | None = None
    title: str
    hook: str = ""
    body: str
    hashtags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    channel: str = "facebook"
    status: ContentStatus = ContentStatus.DRAFT
    media_asset_id: int | None = None
    media_type: MediaType | None = None
    media_url: HttpUrl | None = None
    workflow_id: str = "manual-post"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    facebook_post_id: str | None = None
    platform_post_id: str | None = None

    def as_post(self) -> str:
        parts = [self.hook.strip(), self.body.strip()]
        if self.hashtags:
            parts.append(" ".join(self.hashtags))
        if self.sources:
            parts.append("Nguon:\n" + "\n".join(f"- {source}" for source in self.sources))
        return "\n\n".join(part for part in parts if part).strip()

    @classmethod
    def from_draft(
        cls,
        title: str,
        draft: FacebookDraft,
        channel: str = "facebook",
        workflow_id: str = "ai-news",
    ) -> "ContentItem":
        return cls(
            title=title,
            hook=draft.hook,
            body=draft.body,
            hashtags=draft.hashtags,
            sources=draft.sources,
            channel=channel,
            media_type=MediaType.IMAGE if draft.image_url else None,
            media_url=draft.image_url,
            workflow_id=workflow_id,
        )


class ScheduleJob(BaseModel):
    id: int | None = None
    content_id: int
    workflow_id: str = "manual-post"
    run_at: datetime
    repeat_mode: str = "once"
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run_at: datetime | None = None


class WorkflowTemplate(BaseModel):
    id: str
    name: str
    description: str
    nodes: list[str]
    edges: list[tuple[str, str]]
