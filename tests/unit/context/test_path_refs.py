import os
from pathlib import Path

import pytest

from awesome_agent.context import (
    ExplicitPathError,
    parse_explicit_paths,
    snapshot_explicit_paths,
)
from awesome_agent.core.workspace import resolve_workspace


def test_parser_preserves_order_deduplicates_and_keeps_natural_text() -> None:
    parsed = parse_explicit_paths(
        r'inspect @src/main.py "@docs/design notes.md" @src/main.py literal \@owner'
    )

    assert parsed.references == ("src/main.py", "docs/design notes.md")
    assert parsed.text == "inspect literal @owner"


@pytest.mark.parametrize(
    "reference",
    [
        "../outside.txt",
        "C:\\outside.txt",
        "\\\\server\\share\\file.txt",
        "https://example.com/file.txt",
        "src/*.py",
        "src/**",
    ],
)
def test_parser_or_snapshot_rejects_non_relative_non_literal_reference(
    tmp_path: Path,
    reference: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ExplicitPathError):
        snapshot_explicit_paths(
            resolve_workspace(workspace),
            parse_explicit_paths(f"inspect @{reference}").references,
            token_budget=10_000,
        )


def test_parser_rejects_more_than_32_references() -> None:
    text = " ".join(f"@file-{index}.txt" for index in range(33))

    with pytest.raises(ExplicitPathError, match="32"):
        parse_explicit_paths(text)


@pytest.mark.parametrize(
    "name",
    [".env", "id_rsa", "private.pem", "credentials.json", "image.png"],
)
def test_snapshot_rejects_sensitive_and_non_text_paths(
    tmp_path: Path,
    name: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / name).write_bytes(b"value")

    with pytest.raises(ExplicitPathError):
        snapshot_explicit_paths(
            resolve_workspace(workspace),
            (name,),
            token_budget=10_000,
        )


def test_snapshot_rejects_binary_missing_and_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "binary.txt").write_bytes(b"a\x00b")
    identity = resolve_workspace(workspace)

    for reference in ("binary.txt", "missing.txt"):
        with pytest.raises(ExplicitPathError):
            snapshot_explicit_paths(identity, (reference,), token_budget=10_000)

    target = workspace / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ExplicitPathError, match="symlink"):
        snapshot_explicit_paths(identity, ("link.txt",), token_budget=10_000)


def test_file_and_directory_snapshots_are_bounded_and_frozen(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "long.txt").write_text(
        "\n".join(f"line {index}" for index in range(600)),
        encoding="utf-8",
    )
    directory = workspace / "dir"
    directory.mkdir()
    for index in range(205):
        (directory / f"item-{index:03}.txt").write_text("x", encoding="utf-8")

    snapshots = snapshot_explicit_paths(
        resolve_workspace(workspace),
        ("long.txt", "dir"),
        token_budget=100_000,
    )

    assert len(snapshots) == 2
    assert snapshots[0].truncated is True
    assert snapshots[0].content.count("\n") <= 500
    assert snapshots[1].truncated is True
    assert "item-204.txt" not in snapshots[1].content
    frozen = snapshots[0].content
    (workspace / "long.txt").write_text("changed", encoding="utf-8")
    assert snapshots[0].content == frozen


def test_path_sub_budget_truncates_in_user_order(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "first.txt").write_text("a\n" * 100, encoding="utf-8")
    (workspace / "second.txt").write_text("b\n" * 100, encoding="utf-8")

    snapshots = snapshot_explicit_paths(
        resolve_workspace(workspace),
        ("first.txt", "second.txt"),
        token_budget=30,
    )

    assert snapshots
    assert snapshots[0].truncated is True
    assert sum(snapshot.estimated_tokens for snapshot in snapshots) <= 30
