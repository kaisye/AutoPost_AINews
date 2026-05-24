from __future__ import annotations

from ai_news_agent.models import WorkflowTemplate


WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "ai-news": WorkflowTemplate(
        id="ai-news",
        name="AI News Auto Post",
        description="Collect, rank, draft, approve, publish, and persist AI news posts.",
        nodes=[
            "load_memory",
            "collect_news",
            "rank_news",
            "draft_post",
            "telegram_approval",
            "facebook_publish",
            "persist_memory",
        ],
        edges=[
            ("load_memory", "collect_news"),
            ("collect_news", "rank_news"),
            ("rank_news", "draft_post"),
            ("draft_post", "telegram_approval"),
            ("telegram_approval", "facebook_publish"),
            ("facebook_publish", "persist_memory"),
        ],
    ),
    "manual-post": WorkflowTemplate(
        id="manual-post",
        name="Manual Post With Approval",
        description="Create a post from user input, optionally attach media, approve, and publish.",
        nodes=["manual_input", "telegram_approval", "facebook_publish", "persist_memory"],
        edges=[
            ("manual_input", "telegram_approval"),
            ("telegram_approval", "facebook_publish"),
            ("facebook_publish", "persist_memory"),
        ],
    ),
    "rewrite-repost": WorkflowTemplate(
        id="rewrite-repost",
        name="Rewrite And Repost",
        description="Rewrite a previous memory item and publish it again with the stored media.",
        nodes=["manual_input", "draft_post", "facebook_publish", "persist_memory"],
        edges=[
            ("manual_input", "draft_post"),
            ("draft_post", "facebook_publish"),
            ("facebook_publish", "persist_memory"),
        ],
    ),
    "scheduled-media-post": WorkflowTemplate(
        id="scheduled-media-post",
        name="Scheduled Media Post",
        description="Publish a prepared content item with image or video at a chosen time.",
        nodes=["manual_input", "facebook_publish", "persist_memory"],
        edges=[("manual_input", "facebook_publish"), ("facebook_publish", "persist_memory")],
    ),
}


def workflow_template(workflow_id: str) -> WorkflowTemplate:
    return WORKFLOW_TEMPLATES.get(workflow_id, WORKFLOW_TEMPLATES["manual-post"])
