from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import NoDecode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = Field("nvidia", alias="LLM_PROVIDER")
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    nvidia_api_key: str | None = Field(None, alias="NVIDIA_API_KEY")
    openai_base_url: str | None = Field(None, alias="OPENAI_BASE_URL")
    openai_model: str = Field("openai/gpt-oss-120b", alias="OPENAI_MODEL")
    database_path: Path = Field(Path(".data/ai_news_agent.sqlite3"), alias="DATABASE_PATH")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    news_lookback_hours: int = Field(36, alias="NEWS_LOOKBACK_HOURS")
    news_max_candidates: int = Field(30, alias="NEWS_MAX_CANDIDATES")
    post_article_count: int = Field(1, alias="POST_ARTICLE_COUNT")
    news_query: str = Field(
        "artificial intelligence OR AI OR OpenAI OR Anthropic OR Google DeepMind OR Meta AI",
        alias="NEWS_QUERY",
    )
    rss_feeds: Annotated[list[str], NoDecode] = Field(default_factory=list, alias="RSS_FEEDS")
    tavily_api_key: str | None = Field(None, alias="TAVILY_API_KEY")
    newsapi_key: str | None = Field(None, alias="NEWSAPI_KEY")

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_approver_chat_id: str = Field(..., alias="TELEGRAM_APPROVER_CHAT_ID")
    telegram_approval_timeout_minutes: int = Field(2, alias="TELEGRAM_APPROVAL_TIMEOUT_MINUTES")
    telegram_auto_approve_on_timeout: bool = Field(
        True,
        alias="TELEGRAM_AUTO_APPROVE_ON_TIMEOUT",
    )

    facebook_enabled: bool = Field(False, alias="FACEBOOK_ENABLED")
    facebook_page_id: str | None = Field(None, alias="FACEBOOK_PAGE_ID")
    facebook_page_access_token: str | None = Field(None, alias="FACEBOOK_PAGE_ACCESS_TOKEN")

    run_mode: str = Field("once", alias="RUN_MODE")
    schedule_cron: str = Field("0 8 * * *", alias="SCHEDULE_CRON")
    ui_theme_mode: str = Field("light", alias="UI_THEME_MODE")
    ui_theme_color: str = Field("#1264a3", alias="UI_THEME_COLOR")

    @field_validator("rss_feeds", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def reject_example_secrets(self) -> "Settings":
        provider = self.llm_provider.strip().lower()
        self.llm_provider = provider
        placeholders = {
            "OPENAI_API_KEY": self.openai_api_key == "sk-...",
            "NVIDIA_API_KEY": self.nvidia_api_key == "nvapi-...",
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token == "123456:ABC...",
            "TELEGRAM_APPROVER_CHAT_ID": self.telegram_approver_chat_id == "123456789",
        }
        missing = [name for name, is_placeholder in placeholders.items() if is_placeholder]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Replace placeholder values in .env: {joined}")
        if provider == "nvidia" and not self.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required when LLM_PROVIDER=nvidia")
        if provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if provider not in {"nvidia", "openai"}:
            raise ValueError("LLM_PROVIDER must be either 'nvidia' or 'openai'")
        return self

    @property
    def llm_api_key(self) -> str:
        if self.llm_provider == "nvidia":
            return self.nvidia_api_key or ""
        return self.openai_api_key or ""

    @property
    def llm_base_url(self) -> str | None:
        if self.openai_base_url:
            return self.openai_base_url
        if self.llm_provider == "nvidia":
            return "https://integrate.api.nvidia.com/v1"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()

