from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.attachments.models import AttachmentSource
from awesome_agent.attachments.repository import InMemoryAttachmentRepository
from awesome_agent.attachments.service import AttachmentService
from awesome_agent.attachments.store import AttachmentContentStore
from awesome_agent.domain.enums import RiskLevel
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.attachments import register_attachment_tools
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ToolInvocation
from awesome_agent.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_attachment_tools_list_and_read_current_run(tmp_path: Path) -> None:
    service = AttachmentService(
        repository=InMemoryAttachmentRepository(),
        store=AttachmentContentStore(tmp_path / "attachments"),
    )
    thread_id = uuid4()
    run_id = uuid4()
    message_id = uuid4()
    attachment = await service.create(
        thread_id=thread_id,
        filename="spec.md",
        content=b"one\ntwo\n",
        mime_type="text/markdown",
        source=AttachmentSource.API,
    )
    await service.bind_to_run(
        thread_id=thread_id,
        attachment_ids=[attachment.id],
        run_id=run_id,
        message_id=message_id,
    )
    registry = ToolRegistry()
    register_attachment_tools(registry, service)
    executor = ToolExecutor(registry, ApprovalPolicy())

    listed = await executor.execute(
        ToolInvocation(
            run_id=run_id,
            tool_name="attachment.list",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"attachment:read"},
        )
    )
    read = await executor.execute(
        ToolInvocation(
            run_id=run_id,
            tool_name="attachment.read",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"attachment:read"},
            arguments={
                "attachment_id": str(attachment.id),
                "start_line": 1,
                "max_lines": 1,
                "max_chars": 100,
            },
        )
    )

    assert listed.output["items"][0]["id"] == str(attachment.id)
    assert read.output["content"] == "one"


def test_attachment_tool_specs_are_low_risk() -> None:
    registry = ToolRegistry()
    register_attachment_tools(
        registry,
        AttachmentService(
            repository=InMemoryAttachmentRepository(),
            store=AttachmentContentStore(Path("unused")),
        ),
    )

    spec, _ = registry.resolve("attachment.read")
    assert spec.risk_level is RiskLevel.LOW
    assert spec.sandbox_required is False
    assert spec.required_capabilities == {"attachment:read"}
