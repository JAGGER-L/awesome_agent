from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from awesome_agent.application import diagnostics as diagnostics_module
from awesome_agent.application.diagnostics import ApplicationDiagnosticWriter
from awesome_agent.application.middleware import (
    ApplicationObservation,
    ApplicationOperation,
    DiagnosticUsage,
)
from awesome_agent.core.filesystem import DirectoryPin
from awesome_agent.core.resource_lock import (
    ResourceLockTimeout,
    exclusive_resource_lock,
)


class _ProcessBarrier(Protocol):
    def wait(self, timeout: float | None = None) -> int: ...


def _observation(index: int) -> ApplicationObservation:
    return ApplicationObservation(
        timestamp=datetime(2026, 7, 27, 12, 0, index % 60, tzinfo=UTC),
        session_id=f"session_{'a' * 32}",
        correlation_id=f"correlation_{index:032d}",
        operation=ApplicationOperation.GET_STATE,
        outcome="success",
        duration_ms=index,
        usage=DiagnosticUsage(input_tokens=index, model_calls=1),
    )


def _data_files(logs_dir: Path) -> list[Path]:
    return sorted(logs_dir.glob("application.jsonl*"))


def _records(logs_dir: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in _data_files(logs_dir):
        for line in path.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    return records


def _encoded_observation(index: int) -> bytes:
    return (
        json.dumps(
            _observation(index).model_dump(mode="json", exclude_none=True),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _write_diagnostics_in_process(
    logs_dir: str,
    indices: tuple[int, ...],
    max_file_bytes: int,
    barrier: _ProcessBarrier,
) -> None:
    writer = ApplicationDiagnosticWriter(
        Path(logs_dir),
        max_file_bytes=max_file_bytes,
        queue_capacity=len(indices) + 1,
        lock_timeout_seconds=5,
        close_timeout_seconds=10,
    )
    barrier.wait(timeout=10)
    for index in indices:
        writer.try_emit(_observation(index))
    asyncio.run(writer.aclose())


@pytest.mark.asyncio
async def test_writer_persists_only_the_strict_jsonl_schema(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    writer = ApplicationDiagnosticWriter(logs_dir)

    writer.try_emit(_observation(1))
    await writer.aclose()

    raw = (logs_dir / "application.jsonl").read_text(encoding="utf-8")
    record = json.loads(raw)
    assert tuple(sorted(record)) == (
        "correlation_id",
        "duration_ms",
        "operation",
        "outcome",
        "session_id",
        "timestamp",
        "usage",
        "version",
    )
    assert "prompt" not in raw
    assert "query" not in raw
    assert "url" not in raw.casefold()
    assert "path" not in raw.casefold()
    assert "secret" not in raw.casefold()
    assert raw.endswith("\n")


@pytest.mark.asyncio
async def test_writer_keeps_exactly_five_bounded_data_files(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    sample_size = (
        len(
            json.dumps(
                _observation(1).model_dump(mode="json", exclude_none=True),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        + 1
    )
    writer = ApplicationDiagnosticWriter(
        logs_dir,
        max_file_bytes=sample_size * 2,
    )

    for index in range(20):
        writer.try_emit(_observation(index))
    await writer.aclose()

    files = _data_files(logs_dir)
    assert [path.name for path in files] == [
        "application.jsonl",
        "application.jsonl.1",
        "application.jsonl.2",
        "application.jsonl.3",
        "application.jsonl.4",
    ]
    assert all(path.stat().st_size <= sample_size * 2 for path in files)
    assert all(isinstance(record, dict) for record in _records(logs_dir))
    assert not (logs_dir / "application.jsonl.5").exists()


@pytest.mark.asyncio
async def test_two_writers_serialize_one_shared_log_set(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    first = ApplicationDiagnosticWriter(logs_dir, max_file_bytes=1024 * 1024)
    second = ApplicationDiagnosticWriter(logs_dir, max_file_bytes=1024 * 1024)

    for index in range(20):
        first.try_emit(_observation(index))
        second.try_emit(_observation(index + 20))
    await asyncio.gather(first.aclose(), second.aclose())

    records = _records(logs_dir)
    assert len(records) == 40
    assert {record["correlation_id"] for record in records} == {
        f"correlation_{index:032d}" for index in range(40)
    }


def test_two_processes_serialize_forced_rotations_without_losing_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``spawn`` must be able to import this test module in a fresh interpreter.
    # Pytest's importlib mode does not otherwise promise that the repository
    # root is present in the child process import path.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[3]))
    logs_dir = tmp_path / "logs"
    expected_indices = tuple(range(20))
    max_record_bytes = max(
        len(_encoded_observation(index)) for index in expected_indices
    )
    max_file_bytes = max_record_bytes * 5
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    processes = [
        context.Process(
            target=_write_diagnostics_in_process,
            args=(
                str(logs_dir),
                tuple(range(offset, offset + 10)),
                max_file_bytes,
                barrier,
            ),
        )
        for offset in (0, 10)
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=10)
        for process in processes:
            process.join(timeout=20)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0, 0]
    files = _data_files(logs_dir)
    assert len(files) > 1
    assert {path.name for path in files}.issubset(
        {
            "application.jsonl",
            "application.jsonl.1",
            "application.jsonl.2",
            "application.jsonl.3",
            "application.jsonl.4",
        }
    )
    assert all(path.stat().st_size <= max_file_bytes for path in files)
    records = _records(logs_dir)
    assert len(records) == len(expected_indices)
    assert {record["correlation_id"] for record in records} == {
        f"correlation_{index:032d}" for index in expected_indices
    }


@pytest.mark.asyncio
async def test_queue_backpressure_never_waits_for_the_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ApplicationDiagnosticWriter(
        tmp_path / "logs",
        queue_capacity=1,
    )
    started = threading.Event()
    release = threading.Event()

    def blocked_write(_line: bytes) -> None:
        started.set()
        release.wait(timeout=1)

    monkeypatch.setattr(writer, "_write_line", blocked_write)
    writer.try_emit(_observation(1))
    assert started.wait(timeout=1)
    writer.try_emit(_observation(2))

    before = time.perf_counter()
    writer.try_emit(_observation(3))
    elapsed = time.perf_counter() - before

    release.set()
    await writer.aclose()
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_transient_lock_timeout_drops_only_the_current_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ApplicationDiagnosticWriter(tmp_path / "logs")
    original_write = writer._write_line
    first_attempted = threading.Event()
    attempts = 0

    def fail_once(line: bytes) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_attempted.set()
            raise ResourceLockTimeout
        original_write(line)

    monkeypatch.setattr(writer, "_write_line", fail_once)
    writer.try_emit(_observation(1))
    assert first_attempted.wait(timeout=1)
    writer.try_emit(_observation(2))
    await writer.aclose()

    assert [record["correlation_id"] for record in _records(tmp_path / "logs")] == [
        f"correlation_{2:032d}"
    ]


@pytest.mark.asyncio
async def test_writer_repairs_a_partial_trailing_record(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    active = logs_dir / "application.jsonl"
    active.write_bytes(b"{}\npartial-private-body")
    writer = ApplicationDiagnosticWriter(logs_dir)

    writer.try_emit(_observation(1))
    await writer.aclose()

    lines = active.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {}
    assert json.loads(lines[1])["correlation_id"] == f"correlation_{1:032d}"
    assert "partial-private-body" not in active.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_failed_append_rolls_back_to_the_previous_complete_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    active = logs_dir / "application.jsonl"
    active.write_bytes(b"{}\n")

    def fail_after_partial_write(descriptor: int, data: bytes) -> None:
        os.write(descriptor, data[:7])
        raise OSError("simulated diagnostic write failure")

    monkeypatch.setattr(diagnostics_module, "_write_all", fail_after_partial_write)
    writer = ApplicationDiagnosticWriter(logs_dir)
    writer.try_emit(_observation(1))
    await writer.aclose()

    assert active.read_bytes() == b"{}\n"


@pytest.mark.asyncio
async def test_hard_linked_log_file_disables_writes_without_raising(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    active = logs_dir / "application.jsonl"
    active.write_bytes(b"{}\n")
    os.link(active, logs_dir / "linked-copy")
    writer = ApplicationDiagnosticWriter(logs_dir)

    writer.try_emit(_observation(1))
    await writer.aclose()

    assert active.read_bytes() == b"{}\n"


@pytest.mark.asyncio
async def test_linked_log_directory_is_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "logs"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this host.")
    writer = ApplicationDiagnosticWriter(linked)

    writer.try_emit(_observation(1))
    await writer.aclose()

    assert not (target / "application.jsonl").exists()


@pytest.mark.asyncio
async def test_linked_active_log_file_is_never_followed(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    target = tmp_path / "outside.log"
    target.write_bytes(b"outside-must-remain-unchanged\n")
    active = logs_dir / "application.jsonl"
    try:
        active.symlink_to(target)
    except OSError:
        pytest.skip("File symlinks are unavailable on this host.")
    writer = ApplicationDiagnosticWriter(logs_dir)

    writer.try_emit(_observation(1))
    await writer.aclose()

    assert target.read_bytes() == b"outside-must-remain-unchanged\n"


@pytest.mark.asyncio
async def test_directory_replacement_race_never_redirects_an_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    detached = tmp_path / "detached-logs"
    outside = tmp_path / "outside"
    outside.mkdir()
    entered = threading.Event()
    release = threading.Event()
    replaced = threading.Event()
    writer = ApplicationDiagnosticWriter(logs_dir)
    original_lock = exclusive_resource_lock

    @contextmanager
    def pause_after_directory_pin(
        resource_path: Path,
        *,
        timeout_seconds: float,
        directory: DirectoryPin,
    ) -> Iterator[None]:
        entered.set()
        assert release.wait(timeout=2)
        with original_lock(
            resource_path,
            timeout_seconds=timeout_seconds,
            directory=directory,
        ):
            yield

    def replace_directory() -> None:
        assert entered.wait(timeout=2)
        try:
            logs_dir.rename(detached)
        except OSError:
            # Windows holds a non-delete-sharing directory handle, so the
            # replacement itself must fail while the pin is alive.
            pass
        else:
            logs_dir.symlink_to(outside, target_is_directory=True)
            replaced.set()
        finally:
            release.set()

    monkeypatch.setattr(
        diagnostics_module,
        "exclusive_resource_lock",
        pause_after_directory_pin,
    )
    racer = threading.Thread(target=replace_directory)
    racer.start()
    try:
        writer._write_line(_encoded_observation(7))
    finally:
        release.set()
        racer.join(timeout=2)
        await writer.aclose()

    assert racer.is_alive() is False
    assert not (outside / "application.jsonl").exists()
    assert not (outside / ".application.jsonl.lock").exists()
    if os.name == "nt":
        assert replaced.is_set() is False
        written = logs_dir / "application.jsonl"
    else:
        assert replaced.is_set() is True
        assert logs_dir.is_symlink()
        written = detached / "application.jsonl"
    assert json.loads(written.read_text(encoding="utf-8"))["correlation_id"] == (
        f"correlation_{7:032d}"
    )


@pytest.mark.asyncio
async def test_close_is_idempotent_and_post_close_records_are_dropped(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    writer = ApplicationDiagnosticWriter(logs_dir)
    writer.try_emit(_observation(1))

    await writer.aclose()
    before = (logs_dir / "application.jsonl").read_bytes()
    writer.try_emit(_observation(2))
    await writer.aclose()

    assert (logs_dir / "application.jsonl").read_bytes() == before
