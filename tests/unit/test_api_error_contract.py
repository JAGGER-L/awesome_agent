from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
from awesome_agent.settings import Settings


def test_api_errors_include_request_id_and_structured_fields() -> None:
    client = _client()
    missing_thread = uuid4()

    response = client.get(
        f"/threads/{missing_thread}",
        headers={"x-request-id": "request-test"},
    )

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "request-test"
    body = response.json()
    assert body == {
        "code": "not_found",
        "message": "Thread not found.",
        "detail": "Thread not found.",
        "hint": "Verify the requested resource id still exists.",
        "request_id": "request-test",
        "trace_id": None,
        "recoverable": False,
    }


def test_api_validation_errors_use_stable_code() -> None:
    client = _client()

    response = client.post("/threads", json={"title": ""})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["recoverable"] is False
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_api_conflict_errors_are_classified_by_domain() -> None:
    app = create_app(
        service=cast(Any, object()),
        intake=cast(Any, object()),
        registry=cast(Any, object()),
        settings=Settings(_env_file=None),
    )

    @app.get("/test/model-error")
    async def model_error() -> None:
        raise HTTPException(status_code=409, detail="Model is not configured.")

    @app.get("/test/sandbox-error")
    async def sandbox_error() -> None:
        raise HTTPException(status_code=409, detail="Sandbox service is unreachable.")

    @app.get("/test/mcp-error")
    async def mcp_error() -> None:
        raise HTTPException(status_code=409, detail="MCP server failed health check.")

    @app.get("/test/config-error")
    async def config_error() -> None:
        raise HTTPException(status_code=409, detail="Configuration file is invalid.")

    client = TestClient(app)

    assert client.get("/test/model-error").json()["code"] == "model_error"
    assert client.get("/test/sandbox-error").json()["code"] == "sandbox_error"
    assert client.get("/test/mcp-error").json()["code"] == "mcp_error"
    assert client.get("/test/config-error").json()["code"] == "config_error"


def _client() -> TestClient:
    return TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
        )
    )
