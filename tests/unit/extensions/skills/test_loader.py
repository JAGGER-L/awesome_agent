import os
import subprocess
from pathlib import Path

import pytest

import awesome_agent.core.filesystem as core_filesystem_module
import awesome_agent.core.safe_files as safe_files_module
from awesome_agent.core.filesystem import (
    DirectoryPin,
    ReadRegularFile,
)
from awesome_agent.core.filesystem import (
    FileIdentity as CoreFileIdentity,
)
from awesome_agent.extensions.skills import (
    SkillCatalog,
    SkillLoader,
    SkillResourceError,
    SkillSource,
    discover_skills,
)
from awesome_agent.extensions.skills.loader import SkillResourceErrorKind


def _catalog(tmp_path: Path) -> SkillCatalog:
    root = tmp_path / "skills"
    skill = root / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\nallowed-tools: [execute]\n---\n"
        + "instruction\n" * 10_000,
        encoding="utf-8",
    )
    (skill / "guide.md").write_text("guide\n" * 100, encoding="utf-8")
    return discover_skills(
        bundled_root=None,
        user_root=root,
        workspace_root=None,
        workspace_trusted=False,
    )


def _workspace_catalog(tmp_path: Path) -> SkillCatalog:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    skill = root / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\ninstruction\n",
        encoding="utf-8",
    )
    (skill / "guide.md").write_text("safe guide", encoding="utf-8")
    return discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )


def _identity(loader: SkillLoader, name: str = "review") -> str:
    return loader.identity_snapshot(name).identity


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


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def test_loader_is_lazy_bounded_and_allowed_tools_are_diagnostic(
    tmp_path: Path,
) -> None:
    loader = SkillLoader(_catalog(tmp_path))

    expected_identity = _identity(loader)
    loaded = loader.load(
        "review",
        expected_identity=expected_identity,
        token_limit=5_000,
    )
    resource = loader.read_resource(
        "review",
        "guide.md",
        expected_identity=expected_identity,
        token_limit=10,
    )

    assert loaded.truncated is True
    assert loaded.estimated_tokens <= 5_000
    assert loaded.descriptor.allowed_tools == ("execute",)
    assert resource.truncated is True
    assert resource.estimated_tokens <= 10


def test_loader_rejects_an_identity_outside_the_frozen_turn_snapshot(
    tmp_path: Path,
) -> None:
    loader = SkillLoader(_catalog(tmp_path))
    wrong_identity = f"skill-v1-sha256:{'0' * 64}"

    with pytest.raises(SkillResourceError) as admitted_load:
        loader.admit_load("review", expected_identity=wrong_identity)
    with pytest.raises(SkillResourceError) as admitted_resource:
        loader.admit_resource(
            "review",
            "guide.md",
            expected_identity=wrong_identity,
        )
    with pytest.raises(SkillResourceError) as loaded:
        loader.load("review", expected_identity=wrong_identity)
    with pytest.raises(SkillResourceError) as read:
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=wrong_identity,
            token_limit=100,
        )

    assert admitted_load.value.kind is SkillResourceErrorKind.CONFLICT
    assert admitted_resource.value.kind is SkillResourceErrorKind.CONFLICT
    assert loaded.value.kind is SkillResourceErrorKind.CONFLICT
    assert read.value.kind is SkillResourceErrorKind.CONFLICT


@pytest.mark.parametrize("source", [SkillSource.USER, SkillSource.BUNDLED])
def test_standard_skill_root_is_identity_pinned_after_discovery(
    tmp_path: Path,
    source: SkillSource,
) -> None:
    root = tmp_path / source.value
    root.mkdir()
    package = root / "review"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: review\ndescription: original\n---\ninstruction",
        encoding="utf-8",
    )
    catalog = discover_skills(
        bundled_root=root if source is SkillSource.BUNDLED else None,
        user_root=root if source is SkillSource.USER else None,
        workspace_root=None,
        workspace_trusted=False,
    )
    loader = SkillLoader(catalog)
    original = root.with_name(f"{root.name}.original")
    root.rename(original)
    replacement = root / "review"
    replacement.mkdir(parents=True)
    (replacement / "SKILL.md").write_text(
        "---\nname: review\ndescription: replacement\n---\nexternal",
        encoding="utf-8",
    )

    with pytest.raises(SkillResourceError) as captured:
        loader.load("review", expected_identity=_identity(loader))

    assert captured.value.kind is SkillResourceErrorKind.CONFLICT


def test_resource_read_revalidates_the_discovered_skill_file(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    loader = SkillLoader(catalog)
    skill_file = catalog.resolve("review").root / "SKILL.md"
    original = skill_file.read_text(encoding="utf-8")
    skill_file.write_text(original.replace("execute", "read_file"), encoding="utf-8")

    with pytest.raises(SkillResourceError) as captured:
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )

    assert captured.value.kind is SkillResourceErrorKind.CONFLICT


def test_resource_rejects_escape_binary_symlink_and_missing(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    loader = SkillLoader(catalog)
    root = catalog.resolve("review").root
    (root / "binary.bin").write_bytes(b"a\x00b")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    for path in ("../outside.md", "binary.bin", "missing.md"):
        with pytest.raises(SkillResourceError):
            loader.read_resource(
                "review",
                path,
                expected_identity=_identity(loader),
                token_limit=100,
            )

    link = root / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(SkillResourceError):
        loader.read_resource(
            "review",
            "link.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )


def test_workspace_loader_rejects_package_replaced_after_discovery(
    tmp_path: Path,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    replacement = package.parent / "replacement"
    replacement.mkdir()
    (replacement / "SKILL.md").write_text(
        "---\nname: review\ndescription: Replaced\n---\noutside",
        encoding="utf-8",
    )
    original = package.parent / "original"
    package.rename(original)
    replacement.rename(package)

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.load("review", expected_identity=_identity(loader))


def test_workspace_loader_rejects_skills_root_replaced_after_discovery(
    tmp_path: Path,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    root = catalog.resolve("review").root.parent
    original = root.parent / "skills.original"
    root.rename(original)
    replacement = root / "review"
    replacement.mkdir(parents=True)
    (replacement / "SKILL.md").write_text(
        "---\nname: review\ndescription: Replaced\n---\nexternal sentinel",
        encoding="utf-8",
    )
    (replacement / "guide.md").write_text(
        "EXTERNAL-SKILLS-ROOT-SENTINEL",
        encoding="utf-8",
    )

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.load("review", expected_identity=_identity(loader))
    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )


def test_workspace_loader_rejects_workspace_anchor_replaced_after_discovery(
    tmp_path: Path,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    workspace = catalog.resolve("review").root.parents[2]
    original = workspace.with_name("workspace.original")
    workspace.rename(original)
    replacement = workspace / ".awesome" / "skills" / "review"
    replacement.mkdir(parents=True)
    (replacement / "SKILL.md").write_text(
        "---\nname: review\ndescription: Replaced\n---\nexternal sentinel",
        encoding="utf-8",
    )
    (replacement / "guide.md").write_text(
        "EXTERNAL-WORKSPACE-ANCHOR-SENTINEL",
        encoding="utf-8",
    )

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.load("review", expected_identity=_identity(loader))
    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )


def test_workspace_loader_rejects_skill_md_replaced_after_discovery(
    tmp_path: Path,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    skill_md = catalog.resolve("review").root / "SKILL.md"
    replacement = skill_md.with_suffix(".new")
    replacement.write_text(
        "---\nname: review\ndescription: Replaced\n---\noutside",
        encoding="utf-8",
    )
    skill_md.unlink()
    replacement.rename(skill_md)

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.load("review", expected_identity=_identity(loader))


def test_workspace_resource_revalidates_package_boundary(tmp_path: Path) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    moved = package.parent / "moved"
    package.rename(moved)
    package.mkdir()
    (package / "guide.md").write_text("replacement", encoding="utf-8")

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )


def test_workspace_resource_maps_permission_error_as_permission_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = SkillLoader(_workspace_catalog(tmp_path))

    def deny_read(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise PermissionError("private operating-system detail")

    monkeypatch.setattr(safe_files_module.PinnedPlainDirectory, "read_file", deny_read)

    with pytest.raises(SkillResourceError) as captured:
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )

    assert captured.value.kind is SkillResourceErrorKind.PERMISSION_DENIED
    assert "private operating-system detail" not in str(captured.value)


def test_workspace_resource_rejects_nested_reparse_point(tmp_path: Path) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    outside = tmp_path / "outside-resources"
    outside.mkdir()
    (outside / "secret.md").write_text("external sentinel", encoding="utf-8")
    _directory_link(outside, package / "references")

    with pytest.raises(SkillResourceError, match="links or reparse points"):
        loader.read_resource(
            "review",
            "references/secret.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"x" * (1024 * 1024 + 1), "exceeds the 1 MiB limit"),
        (b"body\x00hidden", "Binary Skill resources"),
        (b"body\xff", "not UTF-8 text"),
    ],
    ids=["oversized", "binary", "invalid-utf8"],
)
def test_workspace_resource_preserves_size_and_text_boundaries(
    tmp_path: Path,
    data: bytes,
    message: str,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    resource = catalog.resolve("review").root / "bounded.md"
    resource.write_bytes(data)

    with pytest.raises(SkillResourceError, match=message):
        loader.read_resource(
            "review",
            "bounded.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )


def test_workspace_resource_rejects_file_replaced_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    guide = catalog.resolve("review").root / "guide.md"
    replacement = guide.with_suffix(".replacement")
    replacement.write_text("external sentinel", encoding="utf-8")
    original = guide.with_suffix(".original")

    real_lstat = core_filesystem_module.lstat_child
    replaced = False

    def replace_resource_between_lstat_and_open(
        parent: DirectoryPin,
        name: str,
    ) -> os.stat_result:
        nonlocal replaced
        result = real_lstat(parent, name)
        if not replaced and parent.path == guide.parent and name == guide.name:
            replaced = True
            guide.rename(original)
            replacement.rename(guide)
        return result

    monkeypatch.setattr(
        core_filesystem_module,
        "lstat_child",
        replace_resource_between_lstat_and_open,
    )

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )
    assert replaced is True


def test_workspace_resource_rejects_package_aba_through_directory_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    moved = package.parent / "review.original"
    outside = tmp_path / "outside-package"
    outside.mkdir()
    (outside / "guide.md").write_text(
        "EXTERNAL-PACKAGE-ABA-SENTINEL",
        encoding="utf-8",
    )
    original_read = safe_files_module._read_pinned_regular_child
    attempted = False
    observed: list[bytes] = []

    def read_during_package_aba(
        parent: DirectoryPin,
        name: str,
        *,
        max_bytes: int | None,
        expected_identity: CoreFileIdentity | None = None,
    ) -> ReadRegularFile:
        nonlocal attempted
        attempted = True
        linked = False
        try:
            package.rename(moved)
        except OSError:
            result = original_read(
                parent,
                name,
                max_bytes=max_bytes,
                expected_identity=expected_identity,
            )
            observed.append(result.data)
            return result
        try:
            _directory_link(outside, package)
            linked = True
            result = original_read(
                parent,
                name,
                max_bytes=max_bytes,
                expected_identity=expected_identity,
            )
            observed.append(result.data)
            return result
        finally:
            if linked:
                _remove_directory_link(package)
            moved.rename(package)

    monkeypatch.setattr(
        safe_files_module,
        "_read_pinned_regular_child",
        read_during_package_aba,
    )

    try:
        resource = loader.read_resource(
            "review",
            "guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )
    except SkillResourceError:
        resource = None

    assert attempted is True
    assert all(b"EXTERNAL-PACKAGE-ABA-SENTINEL" not in data for data in observed)
    if resource is not None:
        assert resource.content == "safe guide"


def test_workspace_resource_rejects_parent_aba_through_directory_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    parent = package / "references"
    parent.mkdir()
    (parent / "guide.md").write_text("safe nested guide", encoding="utf-8")
    moved = package / "references.original"
    outside = tmp_path / "outside-parent"
    outside.mkdir()
    (outside / "guide.md").write_text(
        "EXTERNAL-PARENT-ABA-SENTINEL",
        encoding="utf-8",
    )
    original_open = safe_files_module._open_pinned_directory
    attacked = False

    def open_during_parent_aba(
        path: Path,
        *,
        parent: DirectoryPin | None = None,
        name: str | None = None,
        expected_identity: CoreFileIdentity | None = None,
    ) -> DirectoryPin:
        nonlocal attacked
        if not attacked and parent is not None and name == "references":
            attacked = True
            nested = parent.path / name
            nested.rename(moved)
            _directory_link(outside, nested)
            try:
                return original_open(
                    path,
                    parent=parent,
                    name=name,
                    expected_identity=expected_identity,
                )
            finally:
                _remove_directory_link(nested)
                moved.rename(nested)
        return original_open(
            path,
            parent=parent,
            name=name,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        safe_files_module,
        "_open_pinned_directory",
        open_during_parent_aba,
    )

    with pytest.raises(SkillResourceError):
        loader.read_resource(
            "review",
            "references/guide.md",
            expected_identity=_identity(loader),
            token_limit=100,
        )
    assert attacked is True


@pytest.mark.parametrize("source", [SkillSource.USER, SkillSource.BUNDLED])
def test_non_workspace_package_directory_links_are_rejected(
    tmp_path: Path,
    source: SkillSource,
) -> None:
    root = tmp_path / source.value
    root.mkdir()
    outside = tmp_path / f"{source.value}-outside"
    package = outside / "review"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: review\ndescription: linked package\n---\ninstruction",
        encoding="utf-8",
    )
    (package / "guide.md").write_text("linked guide", encoding="utf-8")
    _directory_link(package, root / "review")

    catalog = discover_skills(
        bundled_root=root if source is SkillSource.BUNDLED else None,
        user_root=root if source is SkillSource.USER else None,
        workspace_root=None,
        workspace_trusted=False,
    )
    assert catalog.descriptors() == ()
    assert [(item.code, item.source) for item in catalog.diagnostics()] == [
        ("unsafe_skill_path", source)
    ]
