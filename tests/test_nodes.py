from ai_news_agent.nodes.base import MediaNode, WorkflowContext
from ai_news_agent.nodes.library import NODE_REGISTRY, ManualInputNode, node_catalog
from ai_news_agent.workflows import WORKFLOW_TEMPLATES


def test_node_registry_exposes_modular_nodes() -> None:
    assert "manual_input" in NODE_REGISTRY
    assert issubclass(NODE_REGISTRY["manual_input"], MediaNode)
    assert any(node["id"] == "facebook_publish" for node in node_catalog())


def test_manual_input_node_requires_content() -> None:
    node = ManualInputNode()
    context = WorkflowContext(run_id="test", workflow_id="manual-post", data={"content": {"title": "x"}})

    result = node.run(context)

    assert result.logs == ["Manual Content Input"]


def test_workflow_templates_define_nodes_and_edges() -> None:
    template = WORKFLOW_TEMPLATES["scheduled-media-post"]

    assert "manual_input" in template.nodes
    assert ("manual_input", "facebook_publish") in template.edges
