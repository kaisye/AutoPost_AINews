from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ai_news_agent.models import (
    Article,
    ApprovalResult,
    ContentItem,
    ContentStatus,
    FacebookDraft,
    MediaAsset,
    MediaType,
    ScheduleJob,
    ScheduleStatus,
)


class AgentMemory:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists articles (
                    fingerprint text primary key,
                    url text not null,
                    title text not null,
                    source text not null,
                    first_seen_at text not null,
                    last_score real not null default 0
                );

                create table if not exists posts (
                    id integer primary key autoincrement,
                    created_at text not null,
                    status text not null,
                    post_text text not null,
                    article_urls text not null,
                    image_url text,
                    approval_feedback text,
                    facebook_post_id text
                );

                create table if not exists media_assets (
                    id integer primary key autoincrement,
                    media_type text not null,
                    url text,
                    local_path text,
                    alt_text text,
                    metadata text not null default '{}',
                    created_at text not null
                );

                create table if not exists content_items (
                    id integer primary key autoincrement,
                    title text not null,
                    hook text not null default '',
                    body text not null,
                    hashtags text not null default '[]',
                    sources text not null default '[]',
                    channel text not null default 'facebook',
                    status text not null,
                    media_asset_id integer,
                    media_type text,
                    media_url text,
                    workflow_id text not null default 'manual-post',
                    created_at text not null,
                    scheduled_at text,
                    published_at text,
                    facebook_post_id text,
                    platform_post_id text
                );

                create table if not exists schedule_jobs (
                    id integer primary key autoincrement,
                    content_id integer not null,
                    workflow_id text not null default 'manual-post',
                    run_at text not null,
                    repeat_mode text not null default 'once',
                    status text not null,
                    created_at text not null,
                    last_run_at text
                );
                """
            )
            self._ensure_column(conn, "posts", "image_url", "text")
            self._ensure_column(conn, "content_items", "platform_post_id", "text")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {definition}")

    @staticmethod
    def canonical_url(url: str) -> str:
        return url.split("?")[0].rstrip("/").lower()

    @staticmethod
    def fingerprint(url: str) -> str:
        canonical = AgentMemory.canonical_url(url)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def seen_fingerprints(self, articles: Iterable[Article]) -> set[str]:
        fingerprints = [self.fingerprint(article.normalized_url) for article in articles]
        if not fingerprints:
            return set()
        placeholders = ",".join("?" for _ in fingerprints)
        with self._connect() as conn:
            rows = conn.execute(
                f"select fingerprint from articles where fingerprint in ({placeholders})",
                fingerprints,
            ).fetchall()
        return {row["fingerprint"] for row in rows}

    def remember_articles(self, articles: Iterable[Article]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                self.fingerprint(article.normalized_url),
                article.normalized_url,
                article.title,
                article.source,
                now,
                article.final_score,
            )
            for article in articles
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                insert into articles (fingerprint, url, title, source, first_seen_at, last_score)
                values (?, ?, ?, ?, ?, ?)
                on conflict(fingerprint) do update set last_score = excluded.last_score
                """,
                rows,
            )

    def recent_posts(self, limit: int = 5) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "select post_text from posts order by created_at desc limit ?",
                (limit,),
            ).fetchall()
        return [row["post_text"] for row in rows]

    def posted_article_urls(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("select article_urls from posts").fetchall()
        urls: set[str] = set()
        for row in rows:
            try:
                urls.update(json.loads(row["article_urls"]))
            except json.JSONDecodeError:
                continue
        return {self.canonical_url(url) for url in urls}

    def is_duplicate_post(self, draft: FacebookDraft, threshold: float = 0.82) -> bool:
        candidate = _token_set(draft.as_post())
        if not candidate:
            return False
        for previous in self.recent_posts(limit=20):
            existing = _token_set(previous)
            if not existing:
                continue
            overlap = len(candidate & existing) / len(candidate | existing)
            if overlap >= threshold:
                return True
        return False

    def recent_post_records(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, created_at, status, post_text, article_urls, image_url, approval_feedback, facebook_post_id
                from posts
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def post_record(self, post_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select id, created_at, status, post_text, article_urls, image_url, approval_feedback, facebook_post_id
                from posts
                where id = ?
                """,
                (post_id,),
            ).fetchone()
        return dict(row) if row else None

    def remember_post(
        self,
        draft: FacebookDraft,
        articles: list[Article],
        approval: ApprovalResult,
        facebook_post_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into posts (
                    created_at, status, post_text, article_urls, image_url, approval_feedback, facebook_post_id
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    approval.status.value,
                    draft.as_post(),
                    json.dumps([article.normalized_url for article in articles]),
                    str(draft.image_url) if draft.image_url else None,
                    approval.feedback,
                    facebook_post_id,
                ),
            )

    def remember_repost(
        self,
        post_text: str,
        article_urls: list[str],
        original_post_id: int,
        facebook_post_id: str | None = None,
        feedback: str | None = None,
        image_url: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into posts (
                    created_at, status, post_text, article_urls, image_url, approval_feedback, facebook_post_id
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    "approved",
                    post_text,
                    json.dumps(article_urls),
                    image_url,
                    feedback or f"Reposted from post #{original_post_id}",
                    facebook_post_id,
                ),
            )

    def create_media_asset(self, asset: MediaAsset) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert into media_assets (media_type, url, local_path, alt_text, metadata, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.media_type.value,
                    str(asset.url) if asset.url else None,
                    asset.local_path,
                    asset.alt_text,
                    json.dumps(asset.metadata),
                    asset.created_at.isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def create_content_item(self, content: ContentItem) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert into content_items (
                    title, hook, body, hashtags, sources, channel, status, media_asset_id,
                    media_type, media_url, workflow_id, created_at, scheduled_at, published_at,
                    facebook_post_id, platform_post_id
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _content_row_values(content),
            )
            return int(cursor.lastrowid)

    def update_content_item(self, content_id: int, **updates: object) -> None:
        allowed = {
            "title",
            "hook",
            "body",
            "hashtags",
            "sources",
            "channel",
            "status",
            "media_asset_id",
            "media_type",
            "media_url",
            "workflow_id",
            "scheduled_at",
            "published_at",
            "facebook_post_id",
            "platform_post_id",
        }
        values: list[object] = []
        assignments: list[str] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(_serialize_platform_value(value))
        if not assignments:
            return
        values.append(content_id)
        with self._connect() as conn:
            conn.execute(
                f"update content_items set {', '.join(assignments)} where id = ?",
                values,
            )

    def content_item(self, content_id: int) -> ContentItem | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select id, title, hook, body, hashtags, sources, channel, status,
                       media_asset_id, media_type, media_url, workflow_id, created_at,
                       scheduled_at, published_at, facebook_post_id, platform_post_id
                from content_items
                where id = ?
                """,
                (content_id,),
            ).fetchone()
        return _content_from_row(row) if row else None

    def recent_content_items(self, limit: int = 20) -> list[ContentItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, title, hook, body, hashtags, sources, channel, status,
                       media_asset_id, media_type, media_url, workflow_id, created_at,
                       scheduled_at, published_at, facebook_post_id, platform_post_id
                from content_items
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [_content_from_row(row) for row in rows]

    def create_schedule_job(self, job: ScheduleJob) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert into schedule_jobs (content_id, workflow_id, run_at, repeat_mode, status, created_at, last_run_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.content_id,
                    job.workflow_id,
                    job.run_at.isoformat(),
                    job.repeat_mode,
                    job.status.value,
                    job.created_at.isoformat(),
                    job.last_run_at.isoformat() if job.last_run_at else None,
                ),
            )
            return int(cursor.lastrowid)

    def schedule_jobs(self, limit: int = 20) -> list[ScheduleJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, content_id, workflow_id, run_at, repeat_mode, status, created_at, last_run_at
                from schedule_jobs
                order by run_at asc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def due_schedule_jobs(self, now: datetime) -> list[ScheduleJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, content_id, workflow_id, run_at, repeat_mode, status, created_at, last_run_at
                from schedule_jobs
                where status = ? and run_at <= ?
                order by run_at asc
                """,
                (ScheduleStatus.ACTIVE.value, now.isoformat()),
            ).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def update_schedule_job(self, job_id: int, **updates: object) -> None:
        allowed = {"run_at", "repeat_mode", "status", "last_run_at"}
        values: list[object] = []
        assignments: list[str] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(_serialize_platform_value(value))
        if not assignments:
            return
        values.append(job_id)
        with self._connect() as conn:
            conn.execute(
                f"update schedule_jobs set {', '.join(assignments)} where id = ?",
                values,
            )


def _token_set(text: str) -> set[str]:
    normalized = text.lower()
    return {
        token
        for token in re.findall(r"\w+", normalized, flags=re.UNICODE)
        if len(token) >= 4
    }


def _content_row_values(content: ContentItem) -> tuple[object, ...]:
    return (
        content.title,
        content.hook,
        content.body,
        json.dumps(content.hashtags),
        json.dumps(content.sources),
        content.channel,
        content.status.value,
        content.media_asset_id,
        content.media_type.value if content.media_type else None,
        str(content.media_url) if content.media_url else None,
        content.workflow_id,
        content.created_at.isoformat(),
        content.scheduled_at.isoformat() if content.scheduled_at else None,
        content.published_at.isoformat() if content.published_at else None,
        content.facebook_post_id,
        content.platform_post_id,
    )


def _content_from_row(row: sqlite3.Row) -> ContentItem:
    return ContentItem(
        id=row["id"],
        title=row["title"],
        hook=row["hook"],
        body=row["body"],
        hashtags=json.loads(row["hashtags"] or "[]"),
        sources=json.loads(row["sources"] or "[]"),
        channel=row["channel"],
        status=ContentStatus(row["status"]),
        media_asset_id=row["media_asset_id"],
        media_type=MediaType(row["media_type"]) if row["media_type"] else None,
        media_url=row["media_url"],
        workflow_id=row["workflow_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        scheduled_at=datetime.fromisoformat(row["scheduled_at"]) if row["scheduled_at"] else None,
        published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
        facebook_post_id=row["facebook_post_id"],
        platform_post_id=row["platform_post_id"] if "platform_post_id" in row.keys() else None,
    )


def _schedule_from_row(row: sqlite3.Row) -> ScheduleJob:
    return ScheduleJob(
        id=row["id"],
        content_id=row["content_id"],
        workflow_id=row["workflow_id"],
        run_at=datetime.fromisoformat(row["run_at"]),
        repeat_mode=row["repeat_mode"],
        status=ScheduleStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_run_at=datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None,
    )


def _serialize_platform_value(value: object) -> object:
    if isinstance(value, (ContentStatus, ScheduleStatus, MediaType)):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return json.dumps(value)
    return str(value) if hasattr(value, "unicode_string") else value
