from pathlib import Path

from awesome_agent.extensions.skills import SkillSource, discover_skills


def _skill(root: Path, name: str, description: str, extra: str = "") -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\nbody secret",
        encoding="utf-8",
    )


def test_discovery_uses_precedence_disable_and_trust(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    workspace = tmp_path / "workspace" / ".agents" / "skills"
    plain = tmp_path / "workspace" / "skills"
    for root in (bundled, user, workspace, plain):
        root.mkdir(parents=True)
    _skill(bundled, "review", "bundled")
    _skill(user, "review", "user")
    _skill(workspace, "review", "workspace")
    _skill(plain, "ignored", "ignored")
    _skill(user, "disabled", "disabled")

    untrusted = discover_skills(
        bundled_root=bundled,
        user_root=user,
        workspace_root=workspace,
        workspace_trusted=False,
        disabled={"disabled"},
    )
    trusted = discover_skills(
        bundled_root=bundled,
        user_root=user,
        workspace_root=workspace,
        workspace_trusted=True,
        disabled={"disabled"},
    )

    assert untrusted.resolve("review").source is SkillSource.USER
    assert trusted.resolve("review").source is SkillSource.WORKSPACE
    assert "ignored" not in {item.name for item in trusted.descriptors()}
    assert "disabled" not in {item.name for item in trusted.descriptors()}
    assert any(item.code == "shadowed" for item in trusted.diagnostics())


def test_invalid_packages_become_diagnostics_not_global_failure(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _skill(user, "good", "valid", "allowed-tools: [read_file]\n")
    _skill(user, "legacy", "invalid", "requested_tools: [execute]\n")
    _skill(user, "mismatch", "invalid", "name: other\n")
    malformed = user / "broken"
    malformed.mkdir()
    (malformed / "SKILL.md").write_text("---\nname: [", encoding="utf-8")

    catalog = discover_skills(
        bundled_root=None,
        user_root=user,
        workspace_root=None,
        workspace_trusted=False,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert catalog.resolve("good").allowed_tools == ("read_file",)
    assert {item.code for item in catalog.diagnostics()} >= {
        "unsupported_legacy_field",
        "invalid_skill",
    }
