from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.schemas import ApprovalDecisionRequest, CreateRunRequest
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.persistence.database import (
    create_engine,
    create_session_factory,
)
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.service import RuntimeService
from awesome_agent.settings import Settings


def create_app(service: RuntimeService | None = None) -> FastAPI:
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            app.state.runtime = service
            yield
            return

        engine = create_engine(settings.database_url)
        app.state.runtime = RuntimeService(
            repository=PostgresRuntimeRepository(create_session_factory(engine)),
            events=EventStream(),
            artifacts=LocalArtifactStore(settings.artifact_root),
            model_resolver=RoleModelResolver.from_settings(settings),
        )
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="awesome_agent", version="0.1.0", lifespan=lifespan)
    if service is not None:
        app.state.runtime = service

    def runtime() -> RuntimeService:
        return cast(RuntimeService, app.state.runtime)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/runs", status_code=201)
    async def create_run(request: CreateRunRequest) -> dict[str, object]:
        run = await runtime().create_run(request.goal)
        return run.model_dump(mode="json")

    @app.get("/runs/{run_id}")
    async def get_run(run_id: UUID) -> dict[str, object]:
        try:
            return (await runtime().get_run(run_id)).model_dump(mode="json")
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: UUID) -> dict[str, object]:
        try:
            run = await runtime().cancel_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        return run.model_dump(mode="json")

    @app.post("/runs/{run_id}/resume")
    async def resume_run(run_id: UUID) -> dict[str, object]:
        try:
            run = await runtime().resume_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return run.model_dump(mode="json")

    @app.get("/runs/{run_id}/agents")
    async def list_agents(run_id: UUID) -> list[dict[str, object]]:
        return [
            agent.model_dump(mode="json")
            for agent in await runtime().list_agents(run_id)
        ]

    @app.get("/runs/{run_id}/todos")
    async def list_todos(run_id: UUID) -> list[dict[str, object]]:
        return [
            todo.model_dump(mode="json") for todo in await runtime().list_todos(run_id)
        ]

    @app.get("/runs/{run_id}/events/history")
    async def event_history(
        run_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
    ) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in await runtime().list_events(
                run_id,
                after_sequence=after_sequence,
            )
        ]

    @app.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        return StreamingResponse(
            _sse(runtime(), run_id, after_sequence=after_sequence),
            media_type="text/event-stream",
        )

    @app.get("/runs/{run_id}/messages")
    async def list_messages(run_id: UUID) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in await runtime().list_events(run_id)
            if event.event_type.value == "message.created"
        ]

    @app.get("/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: UUID) -> list[dict[str, object]]:
        return [
            artifact.model_dump(mode="json")
            for artifact in runtime().list_artifacts(run_id)
        ]

    @app.get("/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: UUID) -> FileResponse:
        try:
            artifact = runtime().artifacts.get(artifact_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="Artifact not found."
            ) from error
        return FileResponse(
            Path(artifact.path),
            media_type=artifact.mime_type,
            filename=Path(artifact.path).name,
        )

    @app.get("/runs/{run_id}/approvals")
    async def list_approvals(run_id: UUID) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in await runtime().list_events(run_id)
            if event.event_type.value.startswith("approval.")
        ]

    @app.post("/runs/{run_id}/approvals/{approval_id}")
    async def decide_approval(
        run_id: UUID,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> dict[str, object]:
        try:
            event = await runtime().decide_approval(
                run_id,
                approval_id=approval_id,
                approved=request.approved,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        return event.model_dump(mode="json")

    @app.get("/runs/{run_id}/verification")
    async def list_verification(run_id: UUID) -> list[dict[str, object]]:
        return []

    return app


async def _sse(
    runtime: RuntimeService,
    run_id: UUID,
    *,
    after_sequence: int,
) -> AsyncIterator[str]:
    async for event in runtime.stream_events(
        run_id,
        after_sequence=after_sequence,
    ):
        yield _format_sse(event)


def _format_sse(event: RuntimeEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"


app = create_app()
