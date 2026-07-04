from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from awesome_agent.client.conversation import ConversationClient
from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.surfaces.client import SurfaceThread, surface_thread_from_mapping


class HttpSurfaceClient:
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
        repository_id: str | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
    ) -> SurfaceThread:
        payload: dict[str, object] = {"title": title}
        if context_kind is not None:
            payload["context_kind"] = context_kind
        if context_path is not None:
            payload["context_path"] = context_path
        if repository_id is not None:
            payload["repository_id"] = repository_id
        if default_model is not None:
            payload["default_model"] = default_model
        if sandbox_profile is not None:
            payload["sandbox_profile"] = sandbox_profile
        response = self._client.post(f"{self.api_url}/threads", json=payload)
        response.raise_for_status()
        return surface_thread_from_mapping(dict(response.json()))

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
        attachment_ids: tuple[str, ...] = (),
    ) -> Iterable[ConversationStreamEvent]:
        return self._conversation.stream_turn(
            thread_id=thread_id,
            content=content,
            model=model,
            thinking=thinking,
            memory=memory,
            skill_ids=skill_ids,
            attachment_ids=attachment_ids,
        )

    def continue_turn(
        self,
        thread_id: str,
        *,
        expected_run_id: str | None = None,
        after_sequence: int = 0,
    ) -> Iterable[ConversationStreamEvent]:
        return self._conversation.continue_turn(
            thread_id=thread_id,
            expected_run_id=expected_run_id,
            after_sequence=after_sequence,
        )

    def list_thread_runs(self, thread_id: str) -> list[dict[str, Any]]:
        return self._get_list(f"/threads/{thread_id}/runs")

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

    def list_models(self) -> dict[str, Any]:
        response = self._client.get(f"{self.api_url}/models")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected provider-first response from /models.")
        return dict(payload)

    def memory_summary(self) -> dict[str, object]:
        response = self._client.get(f"{self.api_url}/memory")
        if response.status_code == 404:
            return {"enabled": False}
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected object response from /memory.")
        return dict(payload)

    def memory_entries(self, target: str | None = None) -> list[dict[str, object]]:
        params = {"target": target} if target else None
        response = self._client.get(f"{self.api_url}/memory/entries", params=params)
        response.raise_for_status()
        payload = response.json()
        return _items_from_list_or_envelope(payload, path="/memory/entries")

    def delete_memory_entry(self, memory_id: str, *, target: str) -> dict[str, object]:
        response = self._client.delete(
            f"{self.api_url}/memory/entries/{memory_id}",
            params={"target": target},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected memory delete object response.")
        return dict(payload)

    def list_threads(self) -> list[SurfaceThread]:
        return [
            surface_thread_from_mapping(item)
            for item in self._get_list_or_empty("/threads")
        ]

    def resume_thread(self, query: str) -> SurfaceThread:
        response = self._client.get(
            f"{self.api_url}/threads/resolve",
            params={"query": query},
        )
        if response.status_code not in {404, 405}:
            response.raise_for_status()
            return surface_thread_from_mapping(dict(response.json()))
        threads = self.list_threads()
        query_normalized = query.casefold()
        for thread in threads:
            if (
                thread.id == query
                or thread.short_id == query
                or query_normalized in thread.title.casefold()
            ):
                return thread
        raise ValueError(f"Thread not found: {query}")

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        return self._get_list_or_empty(f"/threads/{thread_id}/messages")

    def create_attachment(self, thread_id: str, path: Path) -> dict[str, Any]:
        with path.open("rb") as handle:
            response = self._client.post(
                f"{self.api_url}/threads/{thread_id}/attachments",
                files={"file": (path.name, handle, "application/octet-stream")},
                data={"scope": "next_turn"},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected object response from attachment creation.")
        return dict(payload)

    def list_attachments(
        self,
        thread_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        payload = self._get_object(
            f"/threads/{thread_id}/attachments",
            params={"include_deleted": include_deleted},
        )
        return _items_from_list_or_envelope(
            payload,
            path=f"/threads/{thread_id}/attachments",
        )

    def delete_attachment(self, thread_id: str, attachment_id: str) -> dict[str, Any]:
        response = self._client.delete(
            f"{self.api_url}/threads/{thread_id}/attachments/{attachment_id}"
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected object response from attachment deletion.")
        return dict(payload)

    def last_resumable_run(self, thread_id: str) -> dict[str, Any] | None:
        for run in self._get_list_or_empty(f"/threads/{thread_id}/runs"):
            if run.get("status") in {"cancelled", "interrupted", "paused"}:
                return run
        return None

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> SurfaceThread:
        payload: dict[str, object] = {}
        if default_model is not None:
            payload["default_model"] = default_model
        if thinking_mode is not None:
            payload["thinking_mode"] = thinking_mode
        if local_memory_enabled is not None:
            payload["local_memory_enabled"] = local_memory_enabled
        payload["provider_memory"] = provider_memory
        response = self._client.patch(
            f"{self.api_url}/threads/{thread_id}/settings",
            json=payload,
        )
        response.raise_for_status()
        return surface_thread_from_mapping(dict(response.json()))

    def list_skills(self) -> list[dict[str, Any]]:
        return self._get_items_object("/extensions/skills")

    def list_tools(self) -> dict[str, list[dict[str, Any]]]:
        response = self._client.get(f"{self.api_url}/surface/tools")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected object response from /surface/tools.")
        groups: dict[str, list[dict[str, Any]]] = {}
        for name, items in payload.items():
            if not isinstance(items, Iterable) or isinstance(items, dict | str | bytes):
                groups[str(name)] = []
                continue
            groups[str(name)] = [dict(item) for item in items]
        return groups

    def mcp_status(self) -> list[dict[str, Any]]:
        return self._get_items_object("/extensions/mcp")

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        if thread_id is not None:
            return self._get_object(f"/threads/{thread_id}/usage")
        if run_id is not None:
            return self._get_object(f"/runs/{run_id}/budget")
        return {"run_id": None, "total_tokens": 0, "threshold_status": "not_started"}

    def config_summary(self, thread_id: str | None = None) -> dict[str, object]:
        path = f"/threads/{thread_id}/config" if thread_id else "/config"
        config = self._get_object(path)
        return {"api_url": self.api_url, **config}

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

    def cancel(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not thread_id:
            raise ValueError("thread_id is required for cancelling a Run.")
        response = self._client.post(
            f"{self.api_url}/threads/{thread_id}/runs/{run_id}/cancel"
        )
        response.raise_for_status()
        return dict(response.json())

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not thread_id:
            raise ValueError("thread_id is required for deciding an approval.")
        response = self._client.post(
            f"{self.api_url}/threads/{thread_id}/runs/{run_id}/approvals/{approval_id}",
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
        return _items_from_list_or_envelope(payload, path=path)

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
        return _items_from_list_or_envelope(payload, path=path)

    def _get_object(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> dict[str, Any]:
        response = self._client.get(f"{self.api_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Expected object response from {path}.")
        return dict(payload)

    def _get_items_object(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._get_object(path, params=params)
        return _items_from_list_or_envelope(payload, path=path)


TuiApiClient = HttpSurfaceClient


def _items_from_list_or_envelope(
    payload: object,
    *,
    path: str,
) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, Iterable) or isinstance(payload, dict | str | bytes):
        raise ValueError(f"Expected items list response from {path}.")
    return [dict(item) for item in payload]
