from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from awesome_agent.tui.client import TuiApiClient


def test_tui_client_lists_runs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"id": "run-1", "goal": "Inspect"}])

    client = TuiApiClient(
        "http://127.0.0.1:8000/",
        transport=httpx.MockTransport(handler),
    )

    assert client.list_runs(limit=25) == [{"id": "run-1", "goal": "Inspect"}]
    assert str(requests[0].url) == "http://127.0.0.1:8000/runs?limit=25"


def test_tui_client_decides_approval() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"event_type": "approval.decided"})

    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    result = client.decide_approval(
        "run-1",
        "approval-1",
        approved=False,
        thread_id="thread-1",
    )

    assert result["event_type"] == "approval.decided"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/threads/thread-1/runs/run-1/approvals/approval-1"
    assert requests[0].read() == b'{"approved":false}'


def test_tui_client_decide_approval_requires_thread_id() -> None:
    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(ValueError, match="thread_id"):
        client.decide_approval("run-1", "approval-1", approved=False)


def test_tui_client_cancel_uses_thread_scoped_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "run-1", "status": "cancelled"})

    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    result = client.cancel("run-1", thread_id="thread-1")

    assert result["status"] == "cancelled"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/threads/thread-1/runs/run-1/cancel"


def test_tui_client_cancel_requires_thread_id() -> None:
    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(ValueError, match="thread_id"):
        client.cancel("run-1")


def test_tui_client_reads_runtime_status_models_and_memory() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/threads" and request.method == "POST":
            return httpx.Response(
                200,
                json={"id": "thread-1", "title": "Snake game"},
            )
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/models":
            return httpx.Response(
                200,
                json={
                    "providers": [
                        {
                            "id": "deepseek",
                            "display_name": "DeepSeek",
                            "configured": True,
                            "credential_env": "AWESOME_AGENT_DEEPSEEK_API_KEY",
                            "api_key_present": True,
                            "models": [
                                {
                                    "id": "deepseek-v4-pro",
                                    "display_name": "DeepSeek V4 Pro",
                                    "provider_id": "deepseek",
                                    "capabilities": [
                                        "streaming",
                                        "tools",
                                        "reasoning",
                                    ],
                                    "recommended_for": ["leader"],
                                    "selected": True,
                                }
                            ],
                        }
                    ],
                    "current": {
                        "provider_id": "deepseek",
                        "model_id": "deepseek-v4-pro",
                    },
                },
            )
        if request.url.path == "/memory":
            return httpx.Response(200, json={"enabled": False})
        return httpx.Response(404)

    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    thread = client.create_thread("Snake game")
    assert thread.id == "thread-1"
    assert thread.title == "Snake game"
    assert thread.short_id == "thread-1"
    assert client.runtime_status()["api"] == "healthy"
    assert client.list_models()["current"] == {
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-pro",
    }
    assert client.memory_summary() == {"enabled": False}


def test_tui_client_sends_turn_options() -> None:
    bodies: list[bytes] = []
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        bodies.append(request.read())
        payload = (
            '{"event":"message.completed",'
            '"thread_id":"00000000-0000-0000-0000-000000000001",'
            '"turn_id":"00000000-0000-0000-0000-000000000002",'
            '"sequence":1,'
            '"trace_id":"trace",'
            '"payload":{"content":"ok"}}'
        )
        return httpx.Response(
            200,
            text=f"event: message.completed\ndata: {payload}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    events = list(
        client.stream_turn(
            "thread-1",
            "hi",
            model="deepseek-v4-flash",
            thinking="off",
            memory={"local_enabled": True, "provider": "mem0"},
            skill_ids=("repository-inspection",),
        )
    )

    assert events[0].payload["content"] == "ok"
    assert paths == ["/threads/thread-1/turns/stream"]
    assert bodies == [
        (
            b'{"content":"hi","model":"deepseek-v4-flash","thinking_mode":"off",'
            b'"memory":{"local_enabled":true,"provider":"mem0"},'
            b'"skill_ids":["repository-inspection"]}'
        )
    ]


def test_tui_client_sends_attachment_ids() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        payload = (
            '{"event":"message.completed",'
            '"thread_id":"00000000-0000-0000-0000-000000000001",'
            '"turn_id":"00000000-0000-0000-0000-000000000002",'
            '"sequence":1,'
            '"trace_id":"trace",'
            '"payload":{"content":"ok"}}'
        )
        return httpx.Response(
            200,
            text=f"event: message.completed\ndata: {payload}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    list(
        client.stream_turn(
            "thread-1",
            "hi",
            attachment_ids=("00000000-0000-0000-0000-000000000003",),
        )
    )

    assert json.loads(bodies[0].decode()) == {
        "content": "hi",
        "attachment_ids": ["00000000-0000-0000-0000-000000000003"],
    }


def test_tui_client_creates_lists_and_deletes_attachment(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/threads/thread-1/attachments":
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "id": "attachment-1",
                        "thread_id": "thread-1",
                        "scope": "next_turn",
                        "status": "pending",
                        "filename": "spec.md",
                        "mime_type": "text/markdown",
                        "media_type": "text",
                        "size": 7,
                        "sha256": "a" * 64,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "thread_id": "thread-1",
                    "items": [
                        {
                            "id": "attachment-1",
                            "thread_id": "thread-1",
                            "filename": "spec.md",
                        }
                    ],
                },
            )
        if request.url.path == "/threads/thread-1/attachments/attachment-1":
            return httpx.Response(
                200,
                json={
                    "id": "attachment-1",
                    "thread_id": "thread-1",
                    "status": "deleted",
                    "filename": "spec.md",
                },
            )
        return httpx.Response(404)

    path = tmp_path / "spec.md"
    path.write_text("# Spec\n", encoding="utf-8")
    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    created = client.create_attachment("thread-1", path)
    listed = client.list_attachments("thread-1")
    deleted = client.delete_attachment("thread-1", "attachment-1")

    assert created["filename"] == "spec.md"
    assert listed[0]["id"] == "attachment-1"
    assert deleted["status"] == "deleted"
    assert [request.method for request in requests] == ["POST", "GET", "DELETE"]


def test_tui_client_continues_turn_with_expected_run_id() -> None:
    bodies: list[bytes] = []
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        bodies.append(request.read())
        payload = (
            '{"event":"turn.continued",'
            '"thread_id":"00000000-0000-0000-0000-000000000001",'
            '"turn_id":"00000000-0000-0000-0000-000000000002",'
            '"sequence":1,'
            '"trace_id":"trace",'
            '"payload":{"run_id":"run-1","resumed":true}}'
        )
        return httpx.Response(
            200,
            text=f"event: turn.continued\ndata: {payload}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = TuiApiClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )

    events = list(
        client.continue_turn(
            "thread-1",
            expected_run_id="run-1",
            after_sequence=2,
        )
    )

    assert events[0].payload["run_id"] == "run-1"
    assert paths == ["/threads/thread-1/turns/continue/stream"]
    assert [json.loads(body.decode()) for body in bodies] == [
        {"expected_run_id": "run-1", "after_sequence": 2}
    ]


def test_tui_client_reads_surface_capability_endpoints() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        payloads = {
            "/extensions/skills": {
                "configured": True,
                "items": [{"id": "repository-inspection"}],
            },
            "/surface/tools": {
                "builtin": [{"name": "repo.read"}],
                "sandbox": [{"name": "shell.execute"}],
                "mcp": [],
                "extension": [],
            },
            "/extensions/mcp": {
                "configured": True,
                "items": [{"id": "github", "status": "healthy"}],
            },
            "/threads/thread-1/usage": {
                "thread_id": "thread-1",
                "total_tokens": 30,
                "threshold_status": "within_budget",
            },
            "/config": {
                "api_host": "127.0.0.1",
                "local_config_path": "/home/user/.awesome-agent/config.toml",
                "artifact_root": "/home/user/.awesome-agent/runs",
                "workspace_root": None,
                "sandbox_backend": "aio-docker",
                "local_cli_sandbox_backend": "local",
                "observability_enabled": True,
                "deepseek_api_key_env": "AWESOME_AGENT_DEEPSEEK_API_KEY",
                "deepseek_api_key_configured": False,
                "mem0_api_key_env": "AWESOME_AGENT_MEM0_API_KEY",
                "mem0_api_key_configured": False,
            },
        }
        return httpx.Response(200, json=payloads[request.url.path])

    client = TuiApiClient(
        "http://testserver",
        transport=httpx.MockTransport(handler),
    )

    assert client.list_skills()[0]["id"] == "repository-inspection"
    assert client.list_tools()["builtin"][0]["name"] == "repo.read"
    assert client.mcp_status()[0]["status"] == "healthy"
    assert client.usage_summary("thread-1", None)["total_tokens"] == 30
    assert client.config_summary()["api_url"] == "http://testserver"
    assert "/surface/tools" in requested_paths


def test_tui_client_resumes_thread_and_reads_messages() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/threads/resolve":
            assert request.url.params["query"] == "snake"
            return httpx.Response(
                200,
                json={"id": "thread-1", "title": "Snake", "context_path": "E:\\repo"},
            )
        if request.url.path == "/threads/thread-1/messages":
            return httpx.Response(
                200,
                json=[{"role": "user", "content": "hi", "kind": "message"}],
            )
        return httpx.Response(404)

    client = TuiApiClient(
        "http://testserver",
        transport=httpx.MockTransport(handler),
    )

    thread = client.resume_thread("snake")
    messages = client.list_thread_messages("thread-1")

    assert thread.id == "thread-1"
    assert thread.context_label == "E:\\repo"
    assert messages[0]["content"] == "hi"
    assert requested_paths == ["/threads/resolve", "/threads/thread-1/messages"]


def test_tui_client_finds_last_resumable_run_from_thread_runs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/threads/thread-1/runs"
        return httpx.Response(
            200,
            json=[
                {"id": "run-finished", "status": "completed"},
                {"id": "run-paused", "status": "paused"},
            ],
        )

    client = TuiApiClient(
        "http://testserver",
        transport=httpx.MockTransport(handler),
    )

    assert client.last_resumable_run("thread-1") == {
        "id": "run-paused",
        "status": "paused",
    }


def test_tui_client_updates_thread_settings() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "thread-1",
                "title": "Settings",
                "default_model": "deepseek-v4-flash",
                "thinking_mode": "off",
                "local_memory_enabled": True,
                "provider_memory": None,
            },
        )

    client = TuiApiClient(
        "http://testserver",
        transport=httpx.MockTransport(handler),
    )

    thread = client.update_thread_settings(
        "thread-1",
        default_model="deepseek-v4-flash",
        thinking_mode="off",
        local_memory_enabled=True,
        provider_memory=None,
    )

    assert thread.default_model == "deepseek-v4-flash"
    assert thread.thinking_mode == "off"
    assert thread.local_memory_enabled is True
    assert thread.provider_memory is None
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/threads/thread-1/settings"
    assert requests[0].read() == (
        b'{"default_model":"deepseek-v4-flash","thinking_mode":"off",'
        b'"local_memory_enabled":true,"provider_memory":null}'
    )
