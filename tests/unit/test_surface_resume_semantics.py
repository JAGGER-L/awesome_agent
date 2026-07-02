from __future__ import annotations

from awesome_agent.surfaces.local_client import LocalSurfaceClient


class FakeHost:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def close(self) -> None:
        pass

    def cancel(self, run_id: str) -> dict[str, object]:
        self.cancelled.append(run_id)
        return {"id": run_id, "status": "cancelled"}


def test_local_surface_cancel_returns_resumable_shape() -> None:
    client = LocalSurfaceClient(host=FakeHost())  # type: ignore[arg-type]

    result = client.cancel("run-1")

    assert result == {"id": "run-1", "status": "cancelled", "transport": "embedded"}
