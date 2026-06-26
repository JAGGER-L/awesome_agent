from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.domain.enums import RiskLevel
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry


class ArtifactReadArguments(BaseModel):
    artifact_id: UUID
    max_bytes: int = Field(default=200_000, ge=1, le=1_000_000)


class ArtifactToolError(RuntimeError):
    pass


def register_artifact_tools(
    registry: ToolRegistry,
    repository: ArtifactMetadataRepository,
) -> None:
    async def read(invocation: ToolInvocation, _: object) -> ToolResult:
        arguments = ArtifactReadArguments.model_validate(invocation.arguments)
        metadata = await repository.get(arguments.artifact_id)
        if not metadata.path.exists() or not metadata.path.is_file():
            raise ArtifactToolError("Artifact content is unavailable.")
        content = metadata.path.read_bytes()
        truncated = len(content) > arguments.max_bytes
        if truncated:
            content = content[: arguments.max_bytes]
        return ToolResult(
            invocation_id=invocation.id,
            output={
                "artifact_id": str(metadata.id),
                "artifact_type": metadata.artifact_type,
                "mime_type": metadata.mime_type,
                "size": metadata.size,
                "sha256": metadata.sha256,
                "summary": metadata.summary,
                "content": content.decode("utf-8", errors="replace"),
                "truncated": truncated,
            },
        )

    registry.register(
        ToolSpec(
            name="artifact.read",
            description="Read bounded UTF-8 text from a persisted artifact.",
            risk_level=RiskLevel.LOW,
            sandbox_required=False,
            required_capabilities={"artifact:read"},
            input_schema=ArtifactReadArguments.model_json_schema(),
        ),
        read,
    )
