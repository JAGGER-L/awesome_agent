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
