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

    assert client.create_thread("Snake game") == {
        "id": "thread-1",
        "title": "Snake game",
    }
    assert client.runtime_status()["api"] == "healthy"
    assert client.list_models() == [{"name": "deepseek-v4-pro"}]
    assert client.memory_summary() == {"enabled": False}
