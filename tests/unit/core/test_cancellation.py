from __future__ import annotations

import asyncio
import contextvars
import threading
import time

import pytest

from awesome_agent.core.cancellation import run_cancellation_safe_blocking_call


@pytest.mark.asyncio
async def test_blocking_call_preserves_request_context() -> None:
    request_context = contextvars.ContextVar("request-context", default="missing")
    token = request_context.set("request-42")
    try:
        observed = await run_cancellation_safe_blocking_call(request_context.get)
    finally:
        request_context.reset(token)

    assert observed == "request-42"


def test_late_worker_does_not_delay_event_loop_shutdown() -> None:
    entered = threading.Event()
    release = threading.Event()
    worker_daemon: list[bool] = []

    def block_past_cleanup_deadline() -> None:
        worker_daemon.append(threading.current_thread().daemon)
        entered.set()
        release.wait(1.0)

    async def cancel_operation() -> None:
        operation = asyncio.create_task(
            run_cancellation_safe_blocking_call(
                block_past_cleanup_deadline,
                cleanup_timeout_seconds=0.02,
            )
        )
        deadline = asyncio.get_running_loop().time() + 0.5
        while not entered.is_set():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0)
        operation.cancel("shutdown")
        with pytest.raises(asyncio.CancelledError):
            await operation

    started = time.monotonic()
    try:
        asyncio.run(cancel_operation())
    finally:
        release.set()

    assert time.monotonic() - started < 0.5
    assert worker_daemon == [True]


@pytest.mark.asyncio
async def test_blocking_call_exception_propagates() -> None:
    def fail() -> None:
        raise RuntimeError("worker failed")

    with pytest.raises(RuntimeError, match="worker failed"):
        await run_cancellation_safe_blocking_call(fail)


@pytest.mark.asyncio
async def test_blocking_call_cancellation_finishes_worker_and_main_thread_commit() -> (
    None
):
    entered = threading.Event()
    release = threading.Event()
    worker_completed = threading.Event()
    event_loop_thread = threading.get_ident()
    completion_threads: list[int] = []

    def work() -> int:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("worker release was not scheduled")
        worker_completed.set()
        return 42

    operation = asyncio.create_task(
        run_cancellation_safe_blocking_call(
            work,
            on_completed=lambda _: completion_threads.append(threading.get_ident()),
            cleanup_timeout_seconds=1.1,
        )
    )
    assert await asyncio.to_thread(entered.wait, 1.0)

    operation.cancel("primary-cancellation")
    await asyncio.sleep(0)
    assert not operation.done()
    operation.cancel("later-cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as captured:
        await operation

    assert captured.value.args == ("primary-cancellation",)
    assert worker_completed.is_set()
    assert completion_threads == [event_loop_thread]


@pytest.mark.asyncio
async def test_late_worker_failure_after_cleanup_deadline_is_observed() -> None:
    entered = threading.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: unhandled.append(context))

    def fail_late() -> None:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("worker release was not scheduled")
        raise RuntimeError("late worker failure")

    operation = asyncio.create_task(
        run_cancellation_safe_blocking_call(
            fail_late,
            cleanup_timeout_seconds=0.05,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1.0)
        operation.cancel("shutdown")
        with pytest.raises(asyncio.CancelledError):
            await operation
        release.set()
        await asyncio.sleep(0.1)
    finally:
        release.set()
        loop.set_exception_handler(previous_handler)

    assert unhandled == []


@pytest.mark.asyncio
async def test_late_worker_success_does_not_commit_after_cleanup_deadline() -> None:
    entered = threading.Event()
    release = threading.Event()
    committed: list[int] = []

    def complete_late() -> int:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("worker release was not scheduled")
        return 42

    operation = asyncio.create_task(
        run_cancellation_safe_blocking_call(
            complete_late,
            on_completed=committed.append,
            cleanup_timeout_seconds=0.05,
        )
    )
    assert await asyncio.to_thread(entered.wait, 1.0)
    operation.cancel("shutdown")
    with pytest.raises(asyncio.CancelledError):
        await operation

    release.set()
    await asyncio.sleep(0.1)

    assert committed == []


@pytest.mark.asyncio
async def test_late_worker_result_can_be_released_on_event_loop_thread() -> None:
    entered = threading.Event()
    release = threading.Event()
    late_results: list[tuple[int, int]] = []
    event_loop_thread = threading.get_ident()

    def complete_late() -> int:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("worker release was not scheduled")
        return 42

    operation = asyncio.create_task(
        run_cancellation_safe_blocking_call(
            complete_late,
            on_late_completed=lambda value: late_results.append(
                (value, threading.get_ident())
            ),
            cleanup_timeout_seconds=0.02,
        )
    )
    assert await asyncio.to_thread(entered.wait, 1.0)
    operation.cancel("shutdown")

    with pytest.raises(asyncio.CancelledError):
        await operation

    release.set()
    deadline = asyncio.get_running_loop().time() + 1.0
    while not late_results:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0)

    assert late_results == [(42, event_loop_thread)]


@pytest.mark.asyncio
async def test_cleanup_deadline_calls_abandonment_fence_once_before_late_success() -> (
    None
):
    entered = threading.Event()
    release = threading.Event()
    abandoned: list[int] = []
    event_loop_thread = threading.get_ident()

    def complete_late() -> int:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("worker release was not scheduled")
        return 42

    operation = asyncio.create_task(
        run_cancellation_safe_blocking_call(
            complete_late,
            on_abandoned=lambda: abandoned.append(threading.get_ident()),
            cleanup_timeout_seconds=0.02,
        )
    )
    assert await asyncio.to_thread(entered.wait, 1.0)
    operation.cancel("shutdown")

    with pytest.raises(asyncio.CancelledError):
        await operation

    assert abandoned == [event_loop_thread]
    release.set()
    await asyncio.sleep(0.1)
    assert abandoned == [event_loop_thread]
