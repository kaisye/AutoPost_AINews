from __future__ import annotations

import httpx

from ai_news_agent.config import Settings
from ai_news_agent.models import ContentItem, FacebookDraft, MediaType


class FacebookPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(timeout=30)

    def publish(self, draft: FacebookDraft) -> str | None:
        if not self.settings.facebook_enabled:
            return None
        if not self.settings.facebook_page_id or not self.settings.facebook_page_access_token:
            raise ValueError("FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN are required.")

        endpoint = "photos" if draft.image_url else "feed"
        payload = {
            "access_token": self.settings.facebook_page_access_token,
        }
        if draft.image_url:
            payload["url"] = str(draft.image_url)
            payload["caption"] = draft.as_post()
        else:
            payload["message"] = draft.as_post()

        response = self.client.post(
            f"https://graph.facebook.com/v20.0/{self.settings.facebook_page_id}/{endpoint}",
            data=payload,
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def publish_message(self, message: str, image_url: str | None = None) -> str | None:
        if not self.settings.facebook_enabled:
            return None
        if not self.settings.facebook_page_id or not self.settings.facebook_page_access_token:
            raise ValueError("FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN are required.")

        endpoint = "photos" if image_url else "feed"
        payload = {"access_token": self.settings.facebook_page_access_token}
        if image_url:
            payload["url"] = image_url
            payload["caption"] = message
        else:
            payload["message"] = message

        response = self.client.post(
            f"https://graph.facebook.com/v20.0/{self.settings.facebook_page_id}/{endpoint}",
            data=payload,
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def publish_content(self, content: ContentItem) -> str | None:
        media_url = str(content.media_url) if content.media_url else None
        return self.publish_media_message(
            message=content.as_post(),
            media_type=content.media_type,
            media_url=media_url,
        )

    def publish_media_message(
        self,
        message: str,
        media_type: MediaType | str | None = None,
        media_url: str | None = None,
    ) -> str | None:
        if not self.settings.facebook_enabled:
            return None
        if not self.settings.facebook_page_id or not self.settings.facebook_page_access_token:
            raise ValueError("FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN are required.")

        normalized_type = MediaType(media_type) if media_type else None
        if normalized_type == MediaType.IMAGE and media_url:
            endpoint = "photos"
            payload = {
                "access_token": self.settings.facebook_page_access_token,
                "url": media_url,
                "caption": message,
            }
        elif normalized_type == MediaType.VIDEO and media_url:
            endpoint = "videos"
            payload = {
                "access_token": self.settings.facebook_page_access_token,
                "file_url": media_url,
                "description": message,
            }
        else:
            endpoint = "feed"
            payload = {
                "access_token": self.settings.facebook_page_access_token,
                "message": message,
            }

        response = self.client.post(
            f"https://graph.facebook.com/v20.0/{self.settings.facebook_page_id}/{endpoint}",
            data=payload,
        )
        response.raise_for_status()
        return str(response.json()["id"])
