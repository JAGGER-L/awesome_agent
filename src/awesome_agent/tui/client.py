from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from awesome_agent.client.conversation import ConversationClient
from awesome_agent.conversation.events import ConversationStreamEvent


class TuiApiClient:
    def __init__(
        self,
        api_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(timeout=30, transport=transport)
        self._conversation = ConversationClient(self.api_url, client=self._client)

    def close(self) -> None:
        self._client.close()

    def create_thread(
        self,
        title: str,
        *,
        context_kind: str | None = None,
        context_path: str | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {"title": title}
        if context_kind is not None:
            payload["context_kind"] = context_kind
        if context_path is not None:
            payload["context_path"] = context_path
        if default_model is not None:
            payload["default_model"] = default_model
        if sandbox_profile is not None:
            payload["sandbox_profile"] = sandbox_profile
        response = self._client.post(f"{self.api_url}/threads", json=payload)
        response.raise_for_status()
        return dict(response.json())

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        return self._conversation.stream_turn(
            thread_id=thread_id,
            content=content,
            model=model,
        )

    def runtime_status(self) -> dict[str, object]:
        response = self._client.get(
            f"{self.api_url}/ready",
            params={"profile": "api"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected object response from /ready.")
        return {"api": payload.get("status", "unknown")}

    def list_models(self) -> list[dict[str, Any]]:
        response = self._client.get(f"{self.api_url}/models")
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Iterable) or isinstance(payload, dict | str | bytes):
            raise ValueError("Expected list response from /models.")
        return [dict(item) for item in payload]

    def memory_summary(self) -> dict[str, object]:
        response = self._client.get(f"{self.api_url}/memory")
        if response.status_code == 404:
            return {"enabled": False, "source": "not_configured"}
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected object response from /memory.")
        return dict(payload)

    def list_threads(self) -> list[dict[str, Any]]:
        return self._get_list_or_empty("/threads")

    def list_skills(self) -> list[dict[str, Any]]:
        return self._get_list_or_empty("/extensions/skills")

    def list_tools(self) -> dict[str, list[str]]:
        return {"builtin": [], "mcp": [], "sandbox": []}

    def mcp_status(self) -> list[dict[str, Any]]:
        return []

    def list_uploads(self) -> list[dict[str, Any]]:
        return []

    def list_current_artifacts(self, run_id: str | None) -> list[dict[str, Any]]:
        if run_id is None:
            return []
        return self.artifacts(run_id)

    def usage_summary(self, run_id: str | None) -> dict[str, object]:
        return {"run": run_id or "-", "tokens": 0}

    def config_summary(self) -> dict[str, object]:
        return {"api_url": self.api_url}

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._get_list("/runs", params={"limit": limit})

    def get_run(self, run_id: str) -> dict[str, Any]:
        response = self._client.get(f"{self.api_url}/runs/{run_id}")
        response.raise_for_status()
        return dict(response.json())

    def diagnostics(self, run_id: str) -> dict[str, Any]:
        response = self._client.get(f"{self.api_url}/runs/{run_id}/diagnostics")
        response.raise_for_status()
        return dict(response.json())

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return self._get_list(f"/runs/{run_id}/events/history")

    def approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self._get_list(f"/runs/{run_id}/approvals")

    def model_calls(self, run_id: str) -> list[dict[str, Any]]:
        return self._get_list(f"/runs/{run_id}/model-calls")

    def verification(self, run_id: str) -> list[dict[str, Any]]:
        return self._get_list(f"/runs/{run_id}/verification")

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return self._get_list(f"/runs/{run_id}/artifacts")

    def cancel(self, run_id: str) -> dict[str, Any]:
        response = self._client.post(f"{self.api_url}/runs/{run_id}/cancel")
        response.raise_for_status()
        return dict(response.json())

    def resume(self, run_id: str) -> dict[str, Any]:
        response = self._client.post(f"{self.api_url}/runs/{run_id}/resume")
        response.raise_for_status()
        return dict(response.json())

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> dict[str, Any]:
        response = self._client.post(
            f"{self.api_url}/runs/{run_id}/approvals/{approval_id}",
            json={"approved": approved},
        )
        response.raise_for_status()
        return dict(response.json())

    def _get_list(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> list[dict[str, Any]]:
        response = self._client.get(f"{self.api_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Iterable) or isinstance(payload, dict | str | bytes):
            raise ValueError(f"Expected list response from {path}.")
        return [dict(item) for item in payload]

    def _get_list_or_empty(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> list[dict[str, Any]]:
        response = self._client.get(f"{self.api_url}{path}", params=params)
        if response.status_code in {404, 405}:
            return []
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Iterable) or isinstance(payload, dict | str | bytes):
            raise ValueError(f"Expected list response from {path}.")
        return [dict(item) for item in payload]
