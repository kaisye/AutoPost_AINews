from __future__ import annotations

import logging
from typing import NotRequired, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from ai_news_agent.config import Settings
from ai_news_agent.facebook import FacebookPublisher
from ai_news_agent.llm import PostWriter
from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import ApprovalResult, ApprovalStatus, Article, ContentItem, ContentStatus, FacebookDraft
from ai_news_agent.news import NewsCollector
from ai_news_agent.telegram import TelegramApprovalClient

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    run_id: str
    recent_posts: list[str]
    candidates: list[dict]
    enriched_articles: list[dict]
    ranked_articles: list[dict]
    selected_articles: list[dict]
    draft: NotRequired[dict]
    approval: NotRequired[dict]
    telegram_message_id: NotRequired[int]
    facebook_post_id: NotRequired[str | None]
    skip_reason: NotRequired[str]
    revision_count: int


class AINewsWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = AgentMemory(settings.database_path)
        self.collector = NewsCollector(settings, self.memory)
        self.writer = PostWriter(settings)
        self.telegram = TelegramApprovalClient(settings)
        self.facebook = FacebookPublisher(settings)
        self.graph = self._build_graph()

    def run(self, run_id: str) -> AgentState:
        initial_state: AgentState = {
            "run_id": run_id,
            "recent_posts": [],
            "candidates": [],
            "enriched_articles": [],
            "ranked_articles": [],
            "selected_articles": [],
            "revision_count": 0,
        }
        return self.graph.invoke(
            initial_state,
            config={"configurable": {"thread_id": run_id}},
        )

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("load_memory", self._load_memory)
        graph.add_node("collect_news", self._collect_news)
        graph.add_node("enrich_articles", self._enrich_articles)
        graph.add_node("rank_articles", self._rank_articles)
        graph.add_node("select_ranked_articles", self._select_ranked_articles)
        graph.add_node("draft_post", self._draft_post)
        graph.add_node("check_duplicate_post", self._check_duplicate_post)
        graph.add_node("send_telegram", self._send_telegram)
        graph.add_node("wait_approval", self._wait_approval)
        graph.add_node("revise_post", self._revise_post)
        graph.add_node("publish_facebook", self._publish_facebook)
        graph.add_node("persist_memory", self._persist_memory)

        graph.set_entry_point("load_memory")
        graph.add_edge("load_memory", "collect_news")
        graph.add_edge("collect_news", "enrich_articles")
        graph.add_edge("enrich_articles", "rank_articles")
        graph.add_edge("rank_articles", "select_ranked_articles")
        graph.add_conditional_edges(
            "select_ranked_articles",
            self._has_enough_articles,
            {"draft": "draft_post", "stop": "persist_memory"},
        )
        graph.add_edge("draft_post", "check_duplicate_post")
        graph.add_conditional_edges(
            "check_duplicate_post",
            self._duplicate_route,
            {"send": "send_telegram", "persist": "persist_memory"},
        )
        graph.add_edge("send_telegram", "wait_approval")
        graph.add_conditional_edges(
            "wait_approval",
            self._approval_route,
            {
                "approved": "publish_facebook",
                "revise": "revise_post",
                "persist": "persist_memory",
            },
        )
        graph.add_edge("revise_post", "send_telegram")
        graph.add_edge("publish_facebook", "persist_memory")
        graph.add_edge("persist_memory", END)
        return graph.compile(checkpointer=InMemorySaver())

    def _load_memory(self, state: AgentState) -> AgentState:
        logger.info("Loading recent post memory.")
        return {**state, "recent_posts": self.memory.recent_posts(limit=5)}

    def _collect_news(self, state: AgentState) -> AgentState:
        logger.info("Collecting AI news candidates.")
        return {**state, "candidates": _articles_to_state(self.collector.collect())}

    def _enrich_articles(self, state: AgentState) -> AgentState:
        logger.info("Enriching article metadata.")
        articles = _articles_from_state(state["candidates"])
        return {**state, "enriched_articles": _articles_to_state(self.collector.enrich(articles))}

    def _rank_articles(self, state: AgentState) -> AgentState:
        logger.info("Ranking article impact and novelty.")
        articles = _articles_from_state(state["enriched_articles"])
        return {**state, "ranked_articles": _articles_to_state(self.collector.rank(articles))}

    def _select_ranked_articles(self, state: AgentState) -> AgentState:
        count = max(1, min(self.settings.post_article_count, self.settings.news_max_candidates))
        posted_urls = self.memory.posted_article_urls()
        fresh_articles = [
            article
            for article in state["ranked_articles"]
            if self.memory.canonical_url(Article.model_validate(article).normalized_url) not in posted_urls
        ]
        selected = fresh_articles[:count]
        logger.info("Selected %s articles.", len(selected))
        return {**state, "selected_articles": selected}

    def _draft_post(self, state: AgentState) -> AgentState:
        logger.info("Drafting Facebook post with %s.", self.settings.openai_model)
        draft = self.writer.write(_articles_from_state(state["selected_articles"]), state["recent_posts"])
        return {**state, "draft": draft.model_dump(mode="json")}

    def _check_duplicate_post(self, state: AgentState) -> AgentState:
        draft = FacebookDraft.model_validate(state["draft"])
        if self.memory.is_duplicate_post(draft):
            logger.info("Skipping duplicate post draft.")
            return {**state, "skip_reason": "duplicate_post"}
        return state

    def _send_telegram(self, state: AgentState) -> AgentState:
        logger.info("Sending Telegram approval request.")
        message_id = self.telegram.send_for_approval(FacebookDraft.model_validate(state["draft"]))
        return {**state, "telegram_message_id": message_id}

    def _wait_approval(self, state: AgentState) -> AgentState:
        logger.info("Waiting for Telegram approval.")
        approval = self.telegram.wait_for_approval(state["telegram_message_id"])
        return {**state, "approval": approval.model_dump(mode="json")}

    def _revise_post(self, state: AgentState) -> AgentState:
        approval = ApprovalResult.model_validate(state["approval"])
        feedback = approval.feedback or "Improve clarity and make the post more executive-ready."
        logger.info("Revising post from Telegram feedback.")
        draft = self.writer.revise(
            FacebookDraft.model_validate(state["draft"]),
            _articles_from_state(state["selected_articles"]),
            feedback,
        )
        return {
            **state,
            "draft": draft.model_dump(mode="json"),
            "revision_count": state["revision_count"] + 1,
        }

    def _publish_facebook(self, state: AgentState) -> AgentState:
        logger.info("Publishing to Facebook is enabled: %s.", self.settings.facebook_enabled)
        facebook_post_id = self.facebook.publish(FacebookDraft.model_validate(state["draft"]))
        return {**state, "facebook_post_id": facebook_post_id}

    def _persist_memory(self, state: AgentState) -> AgentState:
        articles = _articles_from_state(state.get("selected_articles", []))
        if articles:
            self.memory.remember_articles(articles)
        if "draft" in state:
            approval = (
                ApprovalResult.model_validate(state["approval"])
                if "approval" in state
                else ApprovalResult(status=ApprovalStatus.SKIPPED_DUPLICATE)
                if state.get("skip_reason") == "duplicate_post"
                else ApprovalResult(status=ApprovalStatus.PENDING)
            )
            self.memory.remember_post(
                draft=FacebookDraft.model_validate(state["draft"]),
                articles=articles,
                approval=approval,
                facebook_post_id=state.get("facebook_post_id"),
            )
            draft = FacebookDraft.model_validate(state["draft"])
            content_status = (
                ContentStatus.PUBLISHED
                if state.get("facebook_post_id")
                else ContentStatus.APPROVED
                if approval.status == ApprovalStatus.APPROVED
                else ContentStatus.FAILED
                if approval.status in {ApprovalStatus.REJECTED, ApprovalStatus.SKIPPED_DUPLICATE}
                else ContentStatus.WAITING_APPROVAL
            )
            title = articles[0].title if articles else "AI News Post"
            self.memory.create_content_item(
                ContentItem.from_draft(title=title, draft=draft, workflow_id="ai-news").model_copy(
                    update={
                        "status": content_status,
                        "facebook_post_id": state.get("facebook_post_id"),
                    }
                )
            )
        logger.info("Run memory persisted.")
        return state

    def _has_enough_articles(self, state: AgentState) -> str:
        expected = max(1, min(self.settings.post_article_count, self.settings.news_max_candidates))
        return "draft" if len(state["selected_articles"]) >= min(1, expected) else "stop"

    @staticmethod
    def _duplicate_route(state: AgentState) -> str:
        return "persist" if state.get("skip_reason") else "send"

    @staticmethod
    def _approval_route(state: AgentState) -> str:
        approval = ApprovalResult.model_validate(state["approval"])
        if approval.status == ApprovalStatus.APPROVED:
            return "approved"
        if approval.status == ApprovalStatus.EDIT_REQUESTED and state["revision_count"] < 2:
            return "revise"
        return "persist"


def _articles_to_state(articles: list[Article]) -> list[dict]:
    return [article.model_dump(mode="json") for article in articles]


def _articles_from_state(articles: list[dict]) -> list[Article]:
    return [Article.model_validate(article) for article in articles]
