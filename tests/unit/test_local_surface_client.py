from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.surfaces.client import SurfaceThread
from awesome_agent.surfaces.local_client import LocalSurfaceClient


class FakeHost:
    def __init__(self) -> None:
        self.thread = SurfaceThread(
            id="thread-1",
            title="Test",
            short_id="thread-1",
            context_label="workspace",
        )
        self.streamed: list[tuple[str, str]] = []

    def close(self) -> None:
        pass

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread:
        return self.thread

    def list_threads(self) -> list[SurfaceThread]:
        return [self.thread]

    def resume_thread(self, query: str) -> SurfaceThread:
        return self.thread

    def list_thread_messages(self, thread_id: str) -> list[dict[str, object]]:
        return []

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        self.streamed.append((thread_id, content))
        return []

    def runtime_status(self) -> dict[str, object]:
        return {"runtime": "embedded", "transport": "local"}

    def list_models(self) -> list[dict[str, object]]:
        return [{"name": "fake-model"}]

    def memory_summary(self) -> dict[str, object]:
        return {"enabled": False}

    def config_summary(self) -> dict[str, object]:
        return {"mode": "embedded"}


def test_local_surface_client_streams_without_http() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    list(client.stream_turn("thread-1", "hi"))

    assert host.streamed == [("thread-1", "hi")]


def test_local_surface_client_status_does_not_reference_http_health() -> None:
    client = LocalSurfaceClient(host=cast(Any, FakeHost()))

    assert client.runtime_status() == {"runtime": "embedded", "transport": "local"}


def test_local_surface_client_has_no_explicit_run_creation() -> None:
    client = LocalSurfaceClient(host=cast(Any, FakeHost()))

    assert not hasattr(client, "start_" + "explicit_run")
