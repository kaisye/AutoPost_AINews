from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil.parser import isoparse
from pydantic import ValidationError

from ai_news_agent.config import Settings
from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import Article


AI_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "openai",
    "anthropic",
    "deepmind",
    "llm",
    "model",
    "agent",
    "copilot",
    "nvidia",
    "inference",
)


class NewsCollector:
    def __init__(self, settings: Settings, memory: AgentMemory) -> None:
        self.settings = settings
        self.memory = memory
        self.client = httpx.Client(timeout=20, follow_redirects=True)

    def collect(self) -> list[Article]:
        articles: list[Article] = []
        articles.extend(self._collect_rss())
        articles.extend(self._collect_hackernews())
        articles.extend(self._collect_tavily())
        articles.extend(self._collect_newsapi())
        return self._dedupe(articles)[: self.settings.news_max_candidates]

    def enrich(self, articles: list[Article]) -> list[Article]:
        enriched: list[Article] = []
        for article in articles:
            if article.summary and article.image_url:
                enriched.append(article)
                continue
            try:
                response = self.client.get(article.normalized_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                summary = article.summary or self._meta(soup, "description")
                image = article.image_url or self._meta(soup, "og:image")
                enriched.append(self._article_with_metadata(article, summary, image))
            except Exception:
                enriched.append(article)
        return enriched

    def rank(self, articles: list[Article]) -> list[Article]:
        seen = self.memory.seen_fingerprints(articles)
        ranked = []
        now = datetime.now(timezone.utc)
        for article in articles:
            age_hours = self._age_hours(article.published_at, now)
            recency = max(0.0, 1.0 - (age_hours / max(self.settings.news_lookback_hours, 1)))
            engagement = self._engagement_score(article.raw_engagement)
            relevance = self._keyword_score(article)
            novelty = 0.15 if self.memory.fingerprint(article.normalized_url) in seen else 1.0
            final = (0.34 * recency) + (0.30 * engagement) + (0.24 * relevance) + (0.12 * novelty)
            ranked.append(
                article.model_copy(
                    update={
                        "impact_score": round(engagement, 4),
                        "relevance_score": round(relevance, 4),
                        "novelty_score": round(novelty, 4),
                        "final_score": round(final, 4),
                    }
                )
            )
        return sorted(ranked, key=lambda item: item.final_score, reverse=True)

    def _collect_rss(self) -> list[Article]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.news_lookback_hours)
        items: list[Article] = []
        for feed_url in self.settings.rss_feeds:
            parsed = feedparser.parse(feed_url)
            source = parsed.feed.get("title", "RSS")
            for entry in parsed.entries[:20]:
                published = self._parse_datetime(entry.get("published") or entry.get("updated"))
                if published and published < cutoff:
                    continue
                if not self._looks_relevant(entry.get("title", ""), entry.get("summary", "")):
                    continue
                article = self._article_or_none(
                    title=entry.get("title", "").strip(),
                    url=entry.get("link"),
                    source=source,
                    published_at=published,
                    summary=BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ")[:500],
                )
                if article:
                    items.append(article)
        return items

    def _collect_hackernews(self) -> list[Article]:
        query = self.settings.news_query.replace(" OR ", " ")
        url = "https://hn.algolia.com/api/v1/search_by_date"
        params = {"query": query, "tags": "story", "hitsPerPage": 30}
        try:
            data = self.client.get(url, params=params).json()
        except Exception:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.news_lookback_hours)
        articles: list[Article] = []
        for hit in data.get("hits", []):
            story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            title = hit.get("title") or hit.get("story_title")
            created = self._parse_datetime(hit.get("created_at"))
            if not title or not story_url or (created and created < cutoff):
                continue
            if not self._looks_relevant(title, ""):
                continue
            article = self._article_or_none(
                title=title,
                url=story_url,
                source="Hacker News",
                published_at=created,
                raw_engagement={
                    "points": hit.get("points") or 0,
                    "comments": hit.get("num_comments") or 0,
                },
            )
            if article:
                articles.append(article)
        return articles

    def _collect_tavily(self) -> list[Article]:
        if not self.settings.tavily_api_key:
            return []
        payload = {
            "api_key": self.settings.tavily_api_key,
            "query": f"latest high impact AI news {self.settings.news_query}",
            "topic": "news",
            "search_depth": "advanced",
            "max_results": 15,
            "include_images": True,
        }
        try:
            data = self.client.post("https://api.tavily.com/search", json=payload).json()
        except Exception:
            return []
        articles = []
        for item in data.get("results", []):
            if not item.get("title") or not item.get("url"):
                continue
            article = self._article_or_none(
                title=item["title"],
                url=item["url"],
                source="Tavily",
                summary=item.get("content"),
                image_url=(item.get("images") or [None])[0],
                raw_engagement={"search_score": item.get("score", 0)},
            )
            if article:
                articles.append(article)
        return articles

    def _collect_newsapi(self) -> list[Article]:
        if not self.settings.newsapi_key:
            return []
        since = (datetime.now(timezone.utc) - timedelta(hours=self.settings.news_lookback_hours)).isoformat()
        params = {
            "apiKey": self.settings.newsapi_key,
            "q": self.settings.news_query,
            "from": since,
            "sortBy": "popularity",
            "language": "en",
            "pageSize": 30,
        }
        try:
            data = self.client.get("https://newsapi.org/v2/everything", params=params).json()
        except Exception:
            return []
        articles = []
        for index, item in enumerate(data.get("articles", [])):
            if not item.get("title") or not item.get("url"):
                continue
            article = self._article_or_none(
                title=item["title"],
                url=item["url"],
                source=item.get("source", {}).get("name") or "NewsAPI",
                published_at=self._parse_datetime(item.get("publishedAt")),
                summary=item.get("description"),
                image_url=item.get("urlToImage"),
                author=item.get("author"),
                raw_engagement={"provider_rank": index},
            )
            if article:
                articles.append(article)
        return articles

    def _dedupe(self, articles: list[Article]) -> list[Article]:
        by_url: dict[str, Article] = {}
        for article in articles:
            key = article.normalized_url.lower()
            if key not in by_url or article.final_score > by_url[key].final_score:
                by_url[key] = article
        return list(by_url.values())

    @staticmethod
    def _article_or_none(**kwargs: Any) -> Article | None:
        try:
            return Article(**kwargs)
        except ValidationError:
            return None

    @staticmethod
    def _article_with_metadata(
        article: Article,
        summary: str | None,
        image_url: str | None,
    ) -> Article:
        data = article.model_dump()
        data["summary"] = summary
        data["image_url"] = image_url
        return NewsCollector._article_or_none(**data) or article

    @staticmethod
    def _meta(soup: BeautifulSoup, name: str) -> str | None:
        selectors = [
            {"property": name},
            {"name": name},
            {"property": f"twitter:{name}"},
            {"name": f"twitter:{name}"},
        ]
        for selector in selectors:
            tag = soup.find("meta", attrs=selector)
            if tag and tag.get("content"):
                return str(tag["content"])
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            if isinstance(value, str) and "," in value:
                parsed = parsedate_to_datetime(value)
            else:
                parsed = isoparse(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _age_hours(published_at: datetime | None, now: datetime) -> float:
        if not published_at:
            return 12
        return max((now - published_at).total_seconds() / 3600, 0)

    @staticmethod
    def _engagement_score(raw: dict[str, float | int | str]) -> float:
        points = float(raw.get("points", 0) or 0)
        comments = float(raw.get("comments", 0) or 0)
        search_score = float(raw.get("search_score", 0) or 0)
        provider_rank = float(raw.get("provider_rank", 30) or 30)
        rank_score = max(0.0, 1.0 - provider_rank / 30)
        hn_score = min(1.0, math.log1p(points + comments * 2) / math.log(800))
        return max(hn_score, min(search_score, 1.0), rank_score)

    @staticmethod
    def _keyword_score(article: Article) -> float:
        text = f"{article.title} {article.summary or ''}".lower()
        hits = sum(1 for keyword in AI_KEYWORDS if keyword in text)
        return min(1.0, hits / 4)

    @staticmethod
    def _looks_relevant(title: str, summary: str) -> bool:
        text = f"{title} {summary}".lower()
        return any(keyword in text for keyword in AI_KEYWORDS)
