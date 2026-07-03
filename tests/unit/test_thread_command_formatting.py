from __future__ import annotations

from awesome_agent.surfaces.client import ChangedFileSummary, SurfaceThread
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

    assert "* New conversation         modified now       no file changes" in rendered
    assert "  New conversation         modified 8m ago    no file changes" in rendered
    assert "14b3a667-1111-2222-3333-444444444444" not in rendered


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
    assert "Snake" in rendered


def test_thread_list_shows_changed_file_counts() -> None:
    threads = [
        SurfaceThread(
            id="thread-new",
            title="Snake game",
            short_id="thread-n",
            updated_label="2m ago",
            changed_file_count=1,
            latest_changed_files=(
                ChangedFileSummary(path="snake.html", status="created"),
            ),
        ),
        SurfaceThread(
            id="thread-old",
            title="Landing page",
            short_id="thread-o",
            updated_label="1h ago",
            changed_file_count=3,
        ),
    ]

    rendered = format_thread_list(thread_summaries(threads, "thread-new"))

    assert "* Snake game               modified 2m ago    1 changed file" in rendered
    assert "  Landing page             modified 1h ago    3 changed files" in rendered
