import asyncio
import sys
from collections.abc import Callable


def configure_event_loop_policy() -> None:
    """Use an event loop compatible with psycopg async on Windows."""
    if sys.platform != "win32":
        return

    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        raise RuntimeError("Windows selector event loop policy is unavailable.")

    factory: Callable[[], asyncio.AbstractEventLoopPolicy] = policy_factory
    asyncio.set_event_loop_policy(factory())
