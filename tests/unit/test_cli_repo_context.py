from pathlib import Path

from awesome_agent.cli.repo_context import discover_launch_context


def test_discover_launch_context_uses_nearest_git_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "pkg" / "module"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    context = discover_launch_context(nested)

    assert context.context_kind == "repo"
    assert context.project_root == nested.resolve()
    assert context.git_root == repo.resolve()
    assert context.display_path == str(repo.resolve())


def test_discover_launch_context_allows_workspace_without_git(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "notes"
    workspace.mkdir()

    context = discover_launch_context(workspace)

    assert context.context_kind == "workspace"
    assert context.project_root == workspace.resolve()
    assert context.git_root is None
    assert context.display_path == str(workspace.resolve())
