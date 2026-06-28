from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.persistence.budget import BudgetRepository, ContextCompactionRecord
from awesome_agent.runtime.budget import estimate_tokens


@dataclass(frozen=True, slots=True)
class TeamPayloadCompaction:
    inline_payload: Any
    artifact_refs: list[UUID] = field(default_factory=list)
    compacted: bool = False
    before_estimated_tokens: int = 0
    after_estimated_tokens: int = 0


async def compact_team_payload(
    *,
    run_id: UUID,
    agent_id: UUID | None,
    graph_name: str,
    graph_version: int,
    payload_kind: str,
    payload: Any,
    artifact_store: LocalArtifactStore | None,
    artifact_repository: ArtifactMetadataRepository | None,
    budget_repository: BudgetRepository | None,
    max_inline_tokens: int,
) -> TeamPayloadCompaction:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    before_tokens = estimate_tokens(serialized)
    if (
        before_tokens <= max_inline_tokens
        or artifact_store is None
        or artifact_repository is None
    ):
        return TeamPayloadCompaction(
            inline_payload=payload,
            before_estimated_tokens=before_tokens,
            after_estimated_tokens=before_tokens,
        )

    metadata = artifact_store.write(
        run_id=run_id,
        agent_id=agent_id,
        artifact_type="team-context",
        filename=f"{payload_kind}.json",
        content=serialized.encode("utf-8"),
        mime_type="application/json",
        summary=(f"Compacted {payload_kind} payload for {graph_name}@{graph_version}."),
    )
    await artifact_repository.record(metadata)
    inline_payload = {
        "compacted": True,
        "payload_kind": payload_kind,
        "artifact_refs": [str(metadata.id)],
        "summary": (
            f"{payload_kind} payload offloaded to artifact {metadata.id}; "
            f"{before_tokens} estimated tokens preserved outside inline context."
        ),
    }
    after_tokens = estimate_tokens(json.dumps(inline_payload, sort_keys=True))
    if budget_repository is not None:
        await budget_repository.record_compaction(
            ContextCompactionRecord(
                run_id=run_id,
                agent_id=agent_id,
                graph_name=graph_name,
                graph_version=graph_version,
                before_estimated_tokens=before_tokens,
                after_estimated_tokens=after_tokens,
                summary=inline_payload["summary"],
                artifact_refs=[metadata.id],
            )
        )
    return TeamPayloadCompaction(
        inline_payload=inline_payload,
        artifact_refs=[metadata.id],
        compacted=True,
        before_estimated_tokens=before_tokens,
        after_estimated_tokens=after_tokens,
    )
