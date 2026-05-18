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
