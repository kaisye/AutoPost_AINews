from ai_news_agent.models import ApprovalStatus
from ai_news_agent.telegram import TelegramApprovalClient


def test_parse_approve() -> None:
    result = TelegramApprovalClient._parse_decision("APPROVE", 10)

    assert result.status == ApprovalStatus.APPROVED
    assert result.telegram_message_id == 10


def test_parse_edit_feedback() -> None:
    result = TelegramApprovalClient._parse_decision("EDIT: ngắn hơn, sắc hơn", 10)

    assert result.status == ApprovalStatus.EDIT_REQUESTED
    assert result.feedback == "ngắn hơn, sắc hơn"
