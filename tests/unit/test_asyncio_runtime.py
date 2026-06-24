from unittest.mock import patch

from awesome_agent.runtime.asyncio import configure_event_loop_policy


def test_non_windows_does_not_change_policy() -> None:
    with (
        patch("awesome_agent.runtime.asyncio.sys.platform", "linux"),
        patch("awesome_agent.runtime.asyncio.asyncio.set_event_loop_policy") as setter,
    ):
        configure_event_loop_policy()

    setter.assert_not_called()
