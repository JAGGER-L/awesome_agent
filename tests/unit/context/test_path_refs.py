import os
import subprocess
from pathlib import Path

import pytest

import awesome_agent.context.path_refs as path_refs_module
from awesome_agent.context import (
    ExplicitPathError,
    parse_explicit_paths,
    snapshot_explicit_paths,
)
from awesome_agent.core.tools.policy import (
    ExpectedPathKind,
    SafeWorkspacePath,
    resolve_workspace_path,
)
from awesome_agent.core.workspace import WorkspaceIdentity, resolve_workspace


def _directory_link(target: Path, link: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    os.symlink(target, link, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


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


def test_snapshot_rejects_file_replaced_by_external_hardlink_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("EXTERNAL-HARDLINK-SENTINEL", encoding="utf-8")

    def resolve_then_replace(
        identity: WorkspaceIdentity,
        requested: str,
        *,
        must_exist: bool,
        expected_kind: ExpectedPathKind | None = None,
        allow_sensitive: bool = False,
    ) -> SafeWorkspacePath:
        safe = resolve_workspace_path(
            identity,
            requested,
            must_exist=must_exist,
            expected_kind=expected_kind,
            allow_sensitive=allow_sensitive,
        )
        target.unlink()
        os.link(outside, target)
        return safe

    monkeypatch.setattr(
        path_refs_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )

    with pytest.raises(ExplicitPathError):
        snapshot_explicit_paths(
            resolve_workspace(workspace),
            ("target.txt",),
            token_budget=10_000,
        )


def test_snapshot_rejects_regular_file_generation_replacement_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("old generation", encoding="utf-8")
    old_target = workspace / "target.old"

    def resolve_then_replace(
        identity: WorkspaceIdentity,
        requested: str,
        *,
        must_exist: bool,
        expected_kind: ExpectedPathKind | None = None,
        allow_sensitive: bool = False,
    ) -> SafeWorkspacePath:
        safe = resolve_workspace_path(
            identity,
            requested,
            must_exist=must_exist,
            expected_kind=expected_kind,
            allow_sensitive=allow_sensitive,
        )
        target.rename(old_target)
        target.write_text("new generation sentinel", encoding="utf-8")
        return safe

    monkeypatch.setattr(
        path_refs_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )

    with pytest.raises(ExplicitPathError, match="changed"):
        snapshot_explicit_paths(
            resolve_workspace(workspace),
            ("target.txt",),
            token_budget=10_000,
        )


def test_snapshot_rejects_parent_replaced_by_directory_link_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    parent = workspace / "parent"
    parent.mkdir(parents=True)
    (parent / "target.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.txt").write_text(
        "EXTERNAL-DIRECTORY-SENTINEL",
        encoding="utf-8",
    )
    original_parent = workspace / "parent.original"
    replaced = False

    def resolve_then_replace(
        identity: WorkspaceIdentity,
        requested: str,
        *,
        must_exist: bool,
        expected_kind: ExpectedPathKind | None = None,
        allow_sensitive: bool = False,
    ) -> SafeWorkspacePath:
        nonlocal replaced
        safe = resolve_workspace_path(
            identity,
            requested,
            must_exist=must_exist,
            expected_kind=expected_kind,
            allow_sensitive=allow_sensitive,
        )
        parent.rename(original_parent)
        _directory_link(outside, parent)
        replaced = True
        return safe

    monkeypatch.setattr(
        path_refs_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )

    try:
        with pytest.raises(ExplicitPathError):
            snapshot_explicit_paths(
                resolve_workspace(workspace),
                ("parent/target.txt",),
                token_budget=10_000,
            )
    finally:
        if replaced:
            _remove_directory_link(parent)
            original_parent.rename(parent)


def test_directory_snapshot_skips_preexisting_hardlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    directory = workspace / "directory"
    directory.mkdir(parents=True)
    (directory / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("EXTERNAL-DIRECTORY-HARDLINK-SENTINEL", encoding="utf-8")
    os.link(outside, directory / "linked.txt")

    snapshots = snapshot_explicit_paths(
        resolve_workspace(workspace),
        ("directory",),
        token_budget=10_000,
    )

    assert len(snapshots) == 1
    assert "inside.txt\tfile" in snapshots[0].content
    assert "linked.txt" not in snapshots[0].content
    assert "EXTERNAL-DIRECTORY-HARDLINK-SENTINEL" not in snapshots[0].content
