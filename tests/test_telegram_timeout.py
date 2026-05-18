from ai_news_agent.models import ApprovalStatus
from ai_news_agent.telegram import TelegramApprovalClient


def test_timeout_can_auto_approve() -> None:
    class Settings:
        telegram_approval_timeout_minutes = 2
        telegram_auto_approve_on_timeout = True

    result = TelegramApprovalClient._timeout_result(Settings(), 10)

    assert result.status == ApprovalStatus.APPROVED
    assert "Auto-approved" in result.feedback


def test_timeout_can_remain_timeout() -> None:
    class Settings:
        telegram_approval_timeout_minutes = 2
        telegram_auto_approve_on_timeout = False

    result = TelegramApprovalClient._timeout_result(Settings(), 10)

    assert result.status == ApprovalStatus.TIMEOUT
