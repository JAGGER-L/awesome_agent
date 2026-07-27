from __future__ import annotations

import asyncio
import contextvars
import logging
import math
import threading
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future as ConcurrentFuture
from typing import Any

logger = logging.getLogger(__name__)

# A credential transaction can wait on two independently locked user-state files.
# Two 10-second lock deadlines plus bounded local persistence remain below this cap.
_BLOCKING_CALL_CLEANUP_TIMEOUT_SECONDS = 22.0


async def finish_cancellation_safe[ResultT](
    operation: Awaitable[ResultT],
) -> tuple[ResultT, asyncio.CancelledError | None]:
    """Observe one caller-owned convergent action before exposing cancellation.

    The action must be fully owned by the caller and guaranteed to converge. This
    helper can wait without a deadline, so it must not wrap arbitrary external I/O.
    A successful action returns its result and the first caller cancellation; if
    the action fails after cancellation, that first cancellation remains primary.
    """

    task = asyncio.ensure_future(operation)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            if cancellation is not None:
                raise cancellation from None
            raise
    try:
        result = task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise
    return result, cancellation


async def run_cancellation_safe_blocking_call[ResultT](
    call: Callable[[], ResultT],
    *,
    on_completed: Callable[[ResultT], None] | None = None,
    on_abandoned: Callable[[], None] | None = None,
    on_late_completed: Callable[[ResultT], None] | None = None,
    cleanup_timeout_seconds: float = _BLOCKING_CALL_CLEANUP_TIMEOUT_SECONDS,
) -> ResultT:
    """Run one indivisible blocking transaction without freezing the event loop.

    Cancellation cannot stop a Python worker thread. Keep the worker shielded, wait
    long enough for the resource-lock deadline, and run the optional in-memory
    commit on the event-loop thread before re-raising the caller's cancellation.
    If cleanup expires, invoke ``on_abandoned`` synchronously so callers can fence
    state that a late worker may still change without an in-memory commit. A
    successful worker result that arrives later is never passed to ``on_completed``;
    when supplied, ``on_late_completed`` receives it on the event-loop thread so
    the caller can release resources that were created after the deadline.
    The commit must be non-blocking and must not perform filesystem, network, lock,
    or database I/O; all such work belongs in ``call`` and its returned value.
    """

    if cleanup_timeout_seconds <= 0 or not math.isfinite(cleanup_timeout_seconds):
        raise ValueError("Blocking-call cleanup timeout must be finite and positive.")

    worker_source, worker_result = _start_daemon_worker(call)
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.shield(worker_result)
    except asyncio.CancelledError:
        await finish_bounded_cancellation_cleanup(
            _await_shielded(worker_result),
            timeout_seconds=cleanup_timeout_seconds,
        )
        if not worker_result.done():
            if on_abandoned is not None:
                try:
                    on_abandoned()
                except BaseException:
                    logger.critical(
                        "Blocking-call abandonment callback failed.",
                        exc_info=True,
                    )
            if on_late_completed is None:
                worker_source.add_done_callback(_consume_concurrent_future)
            else:
                worker_source.add_done_callback(
                    lambda result: _schedule_late_completion(
                        result,
                        loop=loop,
                        callback=on_late_completed,
                    )
                )
            worker_result.cancel()
        elif not worker_result.cancelled() and worker_result.exception() is None:
            try:
                if on_completed is not None:
                    on_completed(worker_result.result())
            except BaseException:
                logger.warning(
                    "Blocking-call completion failed while preserving cancellation.",
                    exc_info=True,
                )
        raise
    if on_completed is not None:
        on_completed(result)
    return result


def _start_daemon_worker[ResultT](
    call: Callable[[], ResultT],
) -> tuple[ConcurrentFuture[ResultT], asyncio.Future[ResultT]]:
    """Run a blocking call without enrolling it in asyncio's shutdown executor."""

    loop = asyncio.get_running_loop()
    source: ConcurrentFuture[ResultT] = ConcurrentFuture()
    result = asyncio.wrap_future(source, loop=loop)
    context = contextvars.copy_context()

    def worker() -> None:
        if not source.set_running_or_notify_cancel():
            return
        try:
            value = context.run(call)
        except BaseException as error:
            source.set_exception(error)
        else:
            source.set_result(value)

    thread = threading.Thread(
        target=worker,
        name="awesome-blocking-state-transaction",
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        source.cancel()
        result.cancel()
        raise
    return source, result


async def _await_shielded[ResultT](result: asyncio.Future[ResultT]) -> None:
    await asyncio.shield(result)


async def finish_bounded_cancellation_cleanup(
    cleanup: Coroutine[Any, Any, object],
    *,
    timeout_seconds: float,
) -> None:
    """Finish cancellation-only cleanup without allowing later cancels to replace it.

    The caller must invoke this only after it has captured the primary
    ``CancelledError``. Cleanup failures are deliberately reported and consumed so
    the caller can re-raise that original cancellation.
    """
    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        cleanup.close()
        raise ValueError("Cancellation cleanup timeout must be finite and positive.")

    cleanup_task = asyncio.create_task(cleanup, name="bounded-cancellation-cleanup")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not cleanup_task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=remaining)
        except asyncio.CancelledError:
            continue
        except TimeoutError:
            break
        except Exception:
            break

    if not cleanup_task.done():
        cleanup_task.cancel()
        cleanup_task.add_done_callback(_consume_cleanup_task)
        logger.warning("Cancellation cleanup exceeded its bounded deadline.")
        return

    if cleanup_task.cancelled():
        logger.warning("Cancellation cleanup was cancelled before completion.")
        return
    error = cleanup_task.exception()
    if error is not None:
        logger.warning(
            "Cancellation cleanup failed.",
            exc_info=(type(error), error, error.__traceback__),
        )


def _consume_cleanup_task(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        return


def _consume_concurrent_future(result: ConcurrentFuture[Any]) -> None:
    if result.cancelled():
        return
    try:
        result.exception()
    except Exception:
        return


def _schedule_late_completion[ResultT](
    result: ConcurrentFuture[ResultT],
    *,
    loop: asyncio.AbstractEventLoop,
    callback: Callable[[ResultT], None],
) -> None:
    if result.cancelled():
        return
    try:
        value = result.result()
    except BaseException:
        return
    try:
        loop.call_soon_threadsafe(_run_late_completion, callback, value)
    except RuntimeError:
        logger.warning("Late blocking-call resource cleanup could not be scheduled.")


def _run_late_completion[ResultT](
    callback: Callable[[ResultT], None],
    value: ResultT,
) -> None:
    try:
        callback(value)
    except BaseException:
        logger.warning("Late blocking-call resource cleanup failed.", exc_info=True)
