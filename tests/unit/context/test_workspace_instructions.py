import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import awesome_agent.context.workspace_instructions as workspace_instruction_module
from awesome_agent.context import (
    WorkspaceInstructionDiagnostic,
    WorkspaceInstructionDiagnosticCode,
    load_workspace_instructions,
)
from awesome_agent.context._safe_files import (
    BoundedFile,
    FileFingerprint,
    read_bounded_file,
)


def _directory_link(target: Path, link: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    os.symlink(target, link, target_is_directory=True)


def test_untrusted_or_missing_workspace_has_no_instruction_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("do not read", encoding="utf-8")

    untrusted = load_workspace_instructions(
        workspace_root=tmp_path,
        workspace_trusted=False,
        effective_input_limit=100_000,
    )
    missing = load_workspace_instructions(
        workspace_root=tmp_path / "missing",
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    assert untrusted.content is None
    assert untrusted.diagnostic is None
    assert missing.content is None
    assert missing.diagnostic is None


def test_valid_agents_md_returns_frozen_bounded_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# Project instructions\nKeep changes focused.", encoding="utf-8")

    snapshot = load_workspace_instructions(
        workspace_root=tmp_path,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )
    path.write_text("changed later", encoding="utf-8")

    assert snapshot.source_id == "AGENTS.md"
    assert snapshot.content == "# Project instructions\nKeep changes focused."
    assert snapshot.estimated_tokens > 0
    assert snapshot.token_limit == 8_192
    assert snapshot.diagnostic is None
    assert snapshot.model_config["frozen"] is True


@pytest.mark.parametrize(
    "field",
    [
        {"code": "workspace_instructions_future"},
        {"source_id": "PROJECT.md"},
    ],
)
def test_workspace_instruction_diagnostic_contract_rejects_unknown_values(
    field: dict[str, str],
) -> None:
    raw = {
        "code": WorkspaceInstructionDiagnosticCode.TOO_LARGE.value,
        "source_id": "AGENTS.md",
        "message": "AGENTS.md was ignored.",
        **field,
    }

    with pytest.raises(ValidationError):
        WorkspaceInstructionDiagnostic.model_validate(raw)


def test_agents_md_over_byte_limit_is_ignored_with_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"a" * (32 * 1024 + 1))

    snapshot = load_workspace_instructions(
        workspace_root=tmp_path,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    assert snapshot.content is None
    assert snapshot.diagnostic is not None
    assert snapshot.diagnostic.code is WorkspaceInstructionDiagnosticCode.TOO_LARGE


def test_agents_md_over_token_allocation_is_ignored_not_truncated(
    tmp_path: Path,
) -> None:
    original = "instruction " * 200
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")

    snapshot = load_workspace_instructions(
        workspace_root=tmp_path,
        workspace_trusted=True,
        effective_input_limit=1_000,
    )

    assert snapshot.token_limit == 100
    assert snapshot.content is None
    assert snapshot.diagnostic is not None
    assert snapshot.diagnostic.code is WorkspaceInstructionDiagnosticCode.TOKEN_LIMIT


def test_agents_md_rejects_binary_and_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    for data, expected in (
        (b"rules\x00hidden", WorkspaceInstructionDiagnosticCode.BINARY),
        (b"rules\xff", WorkspaceInstructionDiagnosticCode.NOT_UTF8),
    ):
        path.write_bytes(data)
        snapshot = load_workspace_instructions(
            workspace_root=tmp_path,
            workspace_trusted=True,
            effective_input_limit=100_000,
        )
        assert snapshot.content is None
        assert snapshot.diagnostic is not None
        assert snapshot.diagnostic.code is expected


def test_agents_md_rejects_workspace_or_file_reparse_point(tmp_path: Path) -> None:
    outside_workspace = tmp_path / "outside-workspace"
    outside_workspace.mkdir()
    (outside_workspace / "AGENTS.md").write_text("outside", encoding="utf-8")
    linked_workspace = tmp_path / "linked-workspace"
    _directory_link(outside_workspace, linked_workspace)

    workspace_result = load_workspace_instructions(
        workspace_root=linked_workspace,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    normal_workspace = tmp_path / "normal-workspace"
    normal_workspace.mkdir()
    linked_file = normal_workspace / "AGENTS.md"
    os.link(outside_workspace / "AGENTS.md", linked_file)
    file_result = load_workspace_instructions(
        workspace_root=normal_workspace,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    assert workspace_result.content is None
    assert workspace_result.diagnostic is not None
    assert (
        workspace_result.diagnostic.code
        is WorkspaceInstructionDiagnosticCode.UNSAFE_PATH
    )
    assert file_result.content is None
    assert file_result.diagnostic is not None
    assert file_result.diagnostic.code is WorkspaceInstructionDiagnosticCode.UNSAFE_PATH


def test_agents_md_rejects_workspace_root_replaced_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("trusted instructions", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "AGENTS.md").write_text("replacement instructions", encoding="utf-8")
    original = tmp_path / "original"
    real_read = read_bounded_file

    def replace_workspace_before_open(
        path: Path,
        *,
        max_bytes: int,
        expected: FileFingerprint | None = None,
    ) -> BoundedFile:
        workspace.rename(original)
        replacement.rename(workspace)
        return real_read(path, max_bytes=max_bytes, expected=expected)

    monkeypatch.setattr(
        workspace_instruction_module,
        "read_bounded_file",
        replace_workspace_before_open,
    )

    snapshot = load_workspace_instructions(
        workspace_root=workspace,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    assert snapshot.content is None
    assert snapshot.diagnostic is not None
    assert snapshot.diagnostic.code is WorkspaceInstructionDiagnosticCode.CHANGED


def test_agents_md_rejects_in_place_mutation_after_trusted_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("trusted instructions", encoding="utf-8")
    real_read = read_bounded_file

    def mutate_file_before_open(
        path: Path,
        *,
        max_bytes: int,
        expected: FileFingerprint | None = None,
    ) -> BoundedFile:
        agents_file.write_text(
            "replacement instructions are not trusted", encoding="utf-8"
        )
        return real_read(path, max_bytes=max_bytes, expected=expected)

    monkeypatch.setattr(
        workspace_instruction_module,
        "read_bounded_file",
        mutate_file_before_open,
    )

    snapshot = load_workspace_instructions(
        workspace_root=tmp_path,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    assert snapshot.content is None
    assert snapshot.diagnostic is not None
    assert snapshot.diagnostic.code is WorkspaceInstructionDiagnosticCode.CHANGED


def test_agents_md_rejects_file_replaced_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("trusted instructions", encoding="utf-8")
    replacement = agents_file.with_suffix(".replacement")
    replacement.write_text("replacement instructions", encoding="utf-8")
    original = agents_file.with_suffix(".original")
    real_open = os.open

    def replace_file_before_open(path: Path, flags: int) -> int:
        agents_file.rename(original)
        replacement.rename(agents_file)
        return real_open(path, flags)

    monkeypatch.setattr(os, "open", replace_file_before_open)

    snapshot = load_workspace_instructions(
        workspace_root=tmp_path,
        workspace_trusted=True,
        effective_input_limit=100_000,
    )

    assert snapshot.content is None
    assert snapshot.diagnostic is not None
    assert snapshot.diagnostic.code is WorkspaceInstructionDiagnosticCode.CHANGED
