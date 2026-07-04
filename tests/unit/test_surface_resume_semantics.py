from __future__ import annotations

from awesome_agent.surfaces.local_client import LocalSurfaceClient


class FakeHost:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def close(self) -> None:
        pass

    def cancel(self, run_id: str) -> dict[str, object]:
        self.cancelled.append(run_id)
        return {
            "run_id": run_id,
            "status": "completed",
            "dispatch_status": "terminal",
            "event_sequence": None,
        }


def test_local_surface_cancel_returns_resumable_shape() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=host)  # type: ignore[arg-type]

    result = client.cancel("run-1")

    assert host.cancelled == ["run-1"]
    assert result == {
        "run_id": "run-1",
        "status": "completed",
        "dispatch_status": "terminal",
        "event_sequence": None,
    }
