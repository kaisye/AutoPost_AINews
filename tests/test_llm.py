from ai_news_agent.llm import _clean_social_text, _loads_json_object


def test_loads_json_object_from_markdown_fence() -> None:
    data = _loads_json_object(
        '```json\n{"hook":"h","body":"b","hashtags":["#AI"],"sources":["s"],"image_url":null}\n```'
    )

    assert data["hook"] == "h"


def test_loads_json_object_from_text_wrapper() -> None:
    data = _loads_json_object(
        'Sure:\n{"hook":"h","body":"b","hashtags":["#AI"],"sources":["s"],"image_url":"https://example.com/a.jpg"}\nDone'
    )

    assert data["sources"] == ["s"]


def test_clean_social_text_removes_aiish_takeaway_heading() -> None:
    text = "🔹 **Takeaways**\n- Builders should watch distribution.\n\n**Kết luận:** This matters now."

    cleaned = _clean_social_text(text)

    assert "Takeaways" not in cleaned
    assert "**" not in cleaned
    assert "🔹" not in cleaned
    assert cleaned == "Builders should watch distribution.\n\nThis matters now."
