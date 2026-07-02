from __future__ import annotations

import httpx

from awesome_agent.surfaces.client import SurfaceThread, surface_thread_from_mapping
from awesome_agent.tui.client import HttpSurfaceClient


def test_surface_thread_projection_has_short_id() -> None:
    thread = surface_thread_from_mapping(
        {
            "id": "12345678-1234-5678-1234-567812345678",
            "title": "Snake game",
            "context_path": "E:\\project",
        }
    )

    assert thread == SurfaceThread(
        id="12345678-1234-5678-1234-567812345678",
        title="Snake game",
        short_id="12345678",
        context_label="E:\\project",
    )


def test_http_surface_client_preserves_stream_event_parsing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/threads":
            return httpx.Response(
                200,
                json={
                    "id": "12345678-1234-5678-1234-567812345678",
                    "title": "Hello",
                },
            )
        return httpx.Response(404)

    client = HttpSurfaceClient(
        "http://127.0.0.1:8000/",
        transport=httpx.MockTransport(handler),
    )

    thread = client.create_thread("Hello")

    assert thread.short_id == "12345678"
    assert requests[0].url.path == "/threads"
