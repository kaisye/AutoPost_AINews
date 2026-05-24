from __future__ import annotations

from ai_news_agent.config import Settings
from ai_news_agent.facebook import FacebookPublisher
from ai_news_agent.llm import PostWriter
from ai_news_agent.memory import AgentMemory
from ai_news_agent.models import ContentItem, ContentStatus, FacebookDraft
from ai_news_agent.news import NewsCollector
from ai_news_agent.nodes.base import MediaNode, WorkflowContext
from ai_news_agent.telegram import TelegramApprovalClient


class LoadMemoryNode(MediaNode):
    id = "load_memory"
    name = "Load Memory"

    def __init__(self, memory: AgentMemory) -> None:
        self.memory = memory

    def run(self, context: WorkflowContext) -> WorkflowContext:
        return context.with_data(recent_posts=self.memory.recent_posts(limit=5)).log(self.name)


class CollectNewsNode(MediaNode):
    id = "collect_news"
    name = "Collect AI News"

    def __init__(self, collector: NewsCollector) -> None:
        self.collector = collector

    def run(self, context: WorkflowContext) -> WorkflowContext:
        articles = [article.model_dump(mode="json") for article in self.collector.collect()]
        return context.with_data(candidates=articles).log(self.name)


class RankNewsNode(MediaNode):
    id = "rank_news"
    name = "Rank News"

    def __init__(self, collector: NewsCollector) -> None:
        self.collector = collector

    def run(self, context: WorkflowContext) -> WorkflowContext:
        from ai_news_agent.workflow import _articles_from_state, _articles_to_state

        articles = _articles_from_state(context.data.get("candidates", []))
        ranked = self.collector.rank(self.collector.enrich(articles))
        return context.with_data(ranked_articles=_articles_to_state(ranked)).log(self.name)


class ManualInputNode(MediaNode):
    id = "manual_input"
    name = "Manual Content Input"

    def run(self, context: WorkflowContext) -> WorkflowContext:
        content = context.data.get("content")
        if not content:
            raise ValueError("ManualInputNode requires content in workflow context.")
        return context.log(self.name)


class DraftPostNode(MediaNode):
    id = "draft_post"
    name = "Draft Social Post"

    def __init__(self, writer: PostWriter) -> None:
        self.writer = writer

    def run(self, context: WorkflowContext) -> WorkflowContext:
        from ai_news_agent.workflow import _articles_from_state

        articles = _articles_from_state(context.data.get("selected_articles", []))
        draft = self.writer.write(articles, context.data.get("recent_posts", []))
        return context.with_data(draft=draft.model_dump(mode="json")).log(self.name)


class ApprovalNode(MediaNode):
    id = "telegram_approval"
    name = "Telegram Approval"

    def __init__(self, telegram: TelegramApprovalClient) -> None:
        self.telegram = telegram

    def run(self, context: WorkflowContext) -> WorkflowContext:
        draft = FacebookDraft.model_validate(context.data["draft"])
        message_id = self.telegram.send_for_approval(draft)
        approval = self.telegram.wait_for_approval(message_id)
        return context.with_data(approval=approval.model_dump(mode="json")).log(self.name)


class PublishFacebookNode(MediaNode):
    id = "facebook_publish"
    name = "Publish Facebook"

    def __init__(self, publisher: FacebookPublisher) -> None:
        self.publisher = publisher

    def run(self, context: WorkflowContext) -> WorkflowContext:
        content = context.data.get("content")
        if content:
            item = ContentItem.model_validate(content)
            facebook_id = self.publisher.publish_content(item)
        else:
            facebook_id = self.publisher.publish(FacebookDraft.model_validate(context.data["draft"]))
        return context.with_data(facebook_post_id=facebook_id).log(self.name)


class PersistMemoryNode(MediaNode):
    id = "persist_memory"
    name = "Persist Memory"

    def __init__(self, memory: AgentMemory) -> None:
        self.memory = memory

    def run(self, context: WorkflowContext) -> WorkflowContext:
        content = context.data.get("content")
        if content:
            item = ContentItem.model_validate(content)
            if item.id:
                self.memory.update_content_item(
                    item.id,
                    status=ContentStatus.PUBLISHED,
                    facebook_post_id=context.data.get("facebook_post_id"),
                )
        return context.log(self.name)


def node_catalog() -> list[dict[str, str]]:
    return [{"id": node_id, "name": cls.name} for node_id, cls in NODE_REGISTRY.items()]


NODE_REGISTRY: dict[str, type[MediaNode]] = {
    LoadMemoryNode.id: LoadMemoryNode,
    CollectNewsNode.id: CollectNewsNode,
    RankNewsNode.id: RankNewsNode,
    ManualInputNode.id: ManualInputNode,
    DraftPostNode.id: DraftPostNode,
    ApprovalNode.id: ApprovalNode,
    PublishFacebookNode.id: PublishFacebookNode,
    PersistMemoryNode.id: PersistMemoryNode,
}


def build_default_node_dependencies(settings: Settings) -> dict[str, MediaNode]:
    memory = AgentMemory(settings.database_path)
    collector = NewsCollector(settings, memory)
    return {
        "load_memory": LoadMemoryNode(memory),
        "collect_news": CollectNewsNode(collector),
        "rank_news": RankNewsNode(collector),
        "manual_input": ManualInputNode(),
        "draft_post": DraftPostNode(PostWriter(settings)),
        "telegram_approval": ApprovalNode(TelegramApprovalClient(settings)),
        "facebook_publish": PublishFacebookNode(FacebookPublisher(settings)),
        "persist_memory": PersistMemoryNode(memory),
    }
