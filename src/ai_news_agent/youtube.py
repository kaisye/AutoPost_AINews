from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx

from ai_news_agent.config import Settings
from ai_news_agent.models import ContentItem, MediaType


class YouTubePublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def publish_content(self, content: ContentItem) -> str | None:
        if not self.settings.youtube_enabled:
            return None
        self._validate_settings()
        if content.media_type != MediaType.VIDEO or not content.media_url:
            raise ValueError("YouTube publishing requires content with video media.")

        video_source = str(content.media_url)
        title = content.title[:100]
        description = content.as_post()
        if _is_remote_url(video_source):
            with _download_to_temp(video_source) as video_path:
                return self._upload_video(video_path, title=title, description=description)
        return self._upload_video(Path(video_source), title=title, description=description)

    def _validate_settings(self) -> None:
        required = {
            "YOUTUBE_CLIENT_ID": self.settings.youtube_client_id,
            "YOUTUBE_CLIENT_SECRET": self.settings.youtube_client_secret,
            "YOUTUBE_REFRESH_TOKEN": self.settings.youtube_refresh_token,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError("Missing YouTube configuration: " + ", ".join(missing))

    def _upload_video(self, video_path: Path, title: str, description: str) -> str:
        if not video_path.exists():
            raise ValueError(f"YouTube video file was not found: {video_path}")

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        credentials = Credentials(
            token=None,
            refresh_token=self.settings.youtube_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.settings.youtube_client_id,
            client_secret=self.settings.youtube_client_secret,
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        body: dict[str, Any] = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": self.settings.youtube_category_id,
            },
            "status": {
                "privacyStatus": self.settings.youtube_privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response.get("id")
        if not video_id:
            raise ValueError("YouTube upload completed without a video id.")
        return str(video_id)


def _is_remote_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


class _download_to_temp:
    def __init__(self, url: str) -> None:
        self.url = url
        self.path: Path | None = None

    def __enter__(self) -> Path:
        suffix = Path(self.url.split("?", 1)[0]).suffix or ".mp4"
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        handle.close()
        self.path = Path(handle.name)
        with httpx.stream("GET", self.url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with self.path.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)
        return self.path

    def __exit__(self, *_: object) -> None:
        if self.path and self.path.exists():
            self.path.unlink()
