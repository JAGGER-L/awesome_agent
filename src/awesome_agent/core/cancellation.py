from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


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
    if timeout_seconds <= 0:
        cleanup.close()
        raise ValueError("Cancellation cleanup timeout must be positive.")

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


def _consume_cleanup_task(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        return
