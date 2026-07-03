from pathlib import Path


def test_conversation_service_does_not_import_model_provider_or_tool_executor() -> None:
    source = Path("src/awesome_agent/conversation/service.py").read_text()

    forbidden = [
        "from awesome_agent.modeling.provider",
        "from awesome_agent.tools.repository import execute_repository_call",
        "_leader_executor.stream(",
        "provider.stream(",
        "provider.complete(",
    ]
    for needle in forbidden:
        assert needle not in source
