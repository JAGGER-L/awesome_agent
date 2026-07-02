from __future__ import annotations

from awesome_agent.surfaces.client import SurfaceThread
from awesome_agent.tui.slash_router import format_thread_list, thread_summaries


def test_thread_list_distinguishes_duplicate_titles_and_marks_current() -> None:
    threads = [
        SurfaceThread(
            id="14b3a667-1111-2222-3333-444444444444",
            title="New conversation",
            short_id="14b3a667",
            context_label="E:\\awesome_agent",
            updated_label="now",
        ),
        SurfaceThread(
            id="d736accd-1111-2222-3333-444444444444",
            title="New conversation",
            short_id="d736accd",
            context_label="E:\\other_project",
            updated_label="8m ago",
        ),
    ]

    rendered = format_thread_list(
        thread_summaries(threads, "14b3a667-1111-2222-3333-444444444444")
    )

    assert "* New conversation  14b3a667  now  E:\\awesome_agent" in rendered
    assert "  New conversation  d736accd  8m ago  E:\\other_project" in rendered


def test_thread_list_hides_container_workspace_paths() -> None:
    rendered = format_thread_list(
        [
            *thread_summaries(
                [
                    SurfaceThread(
                        id="thread-1",
                        title="Snake",
                        short_id="thread-1",
                        context_label="/mnt/user-data/workspace/",
                    )
                ],
                "thread-1",
            )
        ]
    )

    assert "/mnt/user-data/workspace" not in rendered
    assert "workspace" in rendered
