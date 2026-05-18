from __future__ import annotations

import httpx

from ai_news_agent.config import Settings
from ai_news_agent.models import FacebookDraft


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
