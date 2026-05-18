from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ai_news_agent.models import Article, ApprovalResult, FacebookDraft


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
                    approval_feedback text,
                    facebook_post_id text
                );
                """
            )

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

    def recent_post_records(self, limit: int = 10) -> list[dict[str, str | None]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select created_at, status, post_text, article_urls, approval_feedback, facebook_post_id
                from posts
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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
                    created_at, status, post_text, article_urls, approval_feedback, facebook_post_id
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    approval.status.value,
                    draft.as_post(),
                    json.dumps([article.normalized_url for article in articles]),
                    approval.feedback,
                    facebook_post_id,
                ),
            )


def _token_set(text: str) -> set[str]:
    normalized = text.lower()
    return {
        token
        for token in re.findall(r"\w+", normalized, flags=re.UNICODE)
        if len(token) >= 4
    }
