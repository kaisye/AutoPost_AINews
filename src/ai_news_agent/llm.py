from __future__ import annotations

import json
import re

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAIError, RateLimitError
from openai import OpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ai_news_agent.config import Settings
from ai_news_agent.models import Article, FacebookDraft


POST_SCHEMA = {
    "type": "json_schema",
    "name": "facebook_ai_news_post",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hook": {"type": "string"},
            "body": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
            "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 1},
            "image_url": {"type": ["string", "null"]},
        },
        "required": ["hook", "body", "hashtags", "sources", "image_url"],
    },
}


class PostWriter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            max_retries=0,
        )

    @retry(
        retry=retry_if_exception(lambda exc: _is_retryable_openai_error(exc)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write(self, articles: list[Article], recent_posts: list[str]) -> FacebookDraft:
        article_context = "\n\n".join(
            "\n".join(
                [
                    f"Title: {article.title}",
                    f"Source: {article.source}",
                    f"URL: {article.normalized_url}",
                    f"Published: {article.published_at}",
                    f"Summary: {article.summary or 'N/A'}",
                    f"Impact score: {article.final_score}",
                    f"Illustration image URL: {article.image_url or 'N/A'}",
                ]
            )
            for article in articles
        )
        writing_mode = (
            "Create one deep-analysis Facebook post from this single best AI news article."
            if len(articles) == 1
            else (
                f"Create one deep synthesis Facebook post from these {len(articles)} ranked "
                "AI news articles. Connect them into one clear market/technology signal."
            )
        )
        recent_context = "\n---\n".join(recent_posts) or "No previous posts."

        content = self._chat_json(
            system=(
                "You are a senior AI industry editor writing for Vietnamese Facebook audiences. "
                "Write concise, credible, non-clickbait posts. Avoid unsupported claims. "
                "Explain why the news matters for builders, businesses, and AI operators. "
                "Use Vietnamese. Keep the post skimmable and professional. "
                "Return one valid JSON object only. Do not wrap it in markdown."
            ),
            user=(
                f"{writing_mode}\n\n"
                f"Ranked articles:\n{article_context}\n\n"
                f"Recent posts to avoid repeating angle/tone:\n{recent_context}\n\n"
                "JSON shape:\n"
                '{"hook":"...","body":"...","hashtags":["#AI"],'
                '"sources":["title - url"],"image_url":"https://... or null"}\n\n'
                "Requirements:\n"
                "- Start with a strong hook under 170 characters.\n"
                "- Body: 3-4 long paragraphs with a clear analytical structure.\n"
                "- Explain: what happened, why now, who is affected, business/technical impact, risks, and what to watch next.\n"
                "- Include 2-3 practical takeaways for builders, operators, or business leaders.\n"
                f"- Include exactly {len(articles)} source item(s), one per selected article.\n"
                "- Use the first available supplied Illustration image URL as image_url; otherwise null.\n"
                "- No fabricated numbers beyond supplied data.\n"
                "- Return JSON only."
            ),
        )
        return FacebookDraft.model_validate(_loads_json_object(content))

    def _chat_json(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            top_p=1,
            max_tokens=4096,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM returned an empty response.")
        return content

    @retry(
        retry=retry_if_exception(lambda exc: _is_retryable_openai_error(exc)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def revise(
        self,
        draft: FacebookDraft,
        articles: list[Article],
        feedback: str,
    ) -> FacebookDraft:
        article_context = "\n".join(
            f"- {article.title} ({article.source}): {article.normalized_url}" for article in articles
        )
        content = self._chat_json(
            system=(
                "You are a senior Vietnamese social editor. Revise the Facebook post according "
                "to approval feedback while preserving factual accuracy and source coverage. "
                "Return one valid JSON object only. Do not wrap it in markdown."
            ),
            user=(
                f"Current post JSON:\n{draft.model_dump_json()}\n\n"
                f"Sources:\n{article_context}\n\n"
                f"Approval feedback:\n{feedback}\n\n"
                "JSON shape:\n"
                '{"hook":"...","body":"...","hashtags":["#AI"],'
                '"sources":["title - url"],"image_url":"https://... or null"}\n\n'
                "Return the revised post as JSON only."
            ),
        )
        return FacebookDraft.model_validate(_loads_json_object(content))


def _is_retryable_openai_error(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return "insufficient_quota" not in str(exc)
    return isinstance(exc, (APIConnectionError, APITimeoutError, InternalServerError))


def explain_openai_error(exc: OpenAIError) -> str:
    if "insufficient_quota" in str(exc):
        return (
            "The configured LLM provider returned insufficient_quota. Check billing/quota "
            "for the API key's organization/project, then rerun the workflow."
        )
    return f"OpenAI request failed: {exc}"


def _loads_json_object(text: str) -> dict:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    elif not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)
