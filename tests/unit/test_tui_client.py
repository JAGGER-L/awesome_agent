from __future__ import annotations

import httpx

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

    result = client.decide_approval("run-1", "approval-1", approved=False)

    assert result["event_type"] == "approval.decided"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/runs/run-1/approvals/approval-1"
    assert requests[0].read() == b'{"approved":false}'


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
            return httpx.Response(200, json=[{"name": "deepseek-v4-pro"}])
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
    assert client.list_models() == [{"name": "deepseek-v4-pro"}]
    assert client.memory_summary() == {"enabled": False}


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
            "/threads/thread-1/uploads": {"configured": False, "items": []},
            "/threads/thread-1/artifacts": {
                "items": [{"path": "/mnt/user-data/workspace/snake.html"}],
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
    assert client.list_uploads("thread-1") == []
    assert client.list_current_artifacts("thread-1", None)[0]["path"].endswith(
        "snake.html"
    )
    assert client.usage_summary("thread-1", None)["total_tokens"] == 30
    assert client.config_summary()["api_url"] == "http://testserver"
    assert "/surface/tools" in requested_paths


def test_tui_client_resumes_thread_and_reads_messages() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/threads/resume":
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
    assert requested_paths == ["/threads/resume", "/threads/thread-1/messages"]


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
