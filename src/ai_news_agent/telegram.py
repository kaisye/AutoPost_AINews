from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from ai_news_agent.config import Settings
from ai_news_agent.models import ApprovalResult, ApprovalStatus, FacebookDraft


class TelegramApprovalClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self.client = httpx.Client(timeout=45)

    def send_for_approval(self, draft: FacebookDraft) -> int:
        text = (
            "Duyệt bài Facebook AI News\n\n"
            f"{draft.as_post()}\n\n"
            "Reply vào message này:\n"
            "APPROVE\n"
            "REJECT: lý do\n"
            "EDIT: yêu cầu chỉnh sửa"
        )
        payload = {
            "chat_id": self.settings.telegram_approver_chat_id,
            "disable_web_page_preview": False,
        }
        if draft.image_url:
            endpoint = "sendPhoto"
            payload["photo"] = str(draft.image_url)
            payload["caption"] = text[:1024]
        else:
            endpoint = "sendMessage"
            payload["text"] = text[:4096]

        response = self.client.post(f"{self.base_url}/{endpoint}", json=payload)
        if response.status_code >= 400:
            detail = response.json().get("description", response.text)
            raise RuntimeError(f"Telegram sendMessage failed: {detail}")
        response.raise_for_status()
        return int(response.json()["result"]["message_id"])

    def wait_for_approval(self, message_id: int) -> ApprovalResult:
        deadline = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.telegram_approval_timeout_minutes
        )
        offset = None
        while datetime.now(timezone.utc) < deadline:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            try:
                response = self.client.get(f"{self.base_url}/getUpdates", params=params)
            except httpx.TimeoutException:
                continue
            response.raise_for_status()
            updates = response.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                reply = message.get("reply_to_message") or {}
                if reply.get("message_id") != message_id:
                    continue
                if str(message.get("chat", {}).get("id")) != self.settings.telegram_approver_chat_id:
                    continue
                decision = (message.get("text") or "").strip()
                return self._parse_decision(decision, message_id)
            time.sleep(2)
        return self._timeout_result(self.settings, message_id)

    @staticmethod
    def _timeout_result(settings: Settings, message_id: int) -> ApprovalResult:
        if settings.telegram_auto_approve_on_timeout:
            return ApprovalResult(
                status=ApprovalStatus.APPROVED,
                feedback=(
                    "Auto-approved after "
                    f"{settings.telegram_approval_timeout_minutes} minute(s) without response."
                ),
                telegram_message_id=message_id,
            )
        return ApprovalResult(status=ApprovalStatus.TIMEOUT, telegram_message_id=message_id)

    @staticmethod
    def _parse_decision(text: str, message_id: int) -> ApprovalResult:
        upper = text.upper()
        if upper.startswith("APPROVE"):
            return ApprovalResult(status=ApprovalStatus.APPROVED, telegram_message_id=message_id)
        if upper.startswith("REJECT"):
            return ApprovalResult(
                status=ApprovalStatus.REJECTED,
                feedback=text.partition(":")[2].strip() or None,
                telegram_message_id=message_id,
            )
        if upper.startswith("EDIT"):
            return ApprovalResult(
                status=ApprovalStatus.EDIT_REQUESTED,
                feedback=text.partition(":")[2].strip() or None,
                telegram_message_id=message_id,
            )
        return ApprovalResult(
            status=ApprovalStatus.EDIT_REQUESTED,
            feedback=f"Unrecognized approval response: {text}",
            telegram_message_id=message_id,
        )
