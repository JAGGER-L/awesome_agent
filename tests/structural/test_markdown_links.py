import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

DOCS_SECTIONS = {
    "getting-started",
    "concepts",
    "user-guide",
    "extensions",
    "reference",
    "architecture",
    "development",
}
REQUIRED_PAGES = {
    "getting-started/README.md",
    "getting-started/installation.md",
    "getting-started/quickstart.md",
    "getting-started/quickstart.zh-CN.md",
    "concepts/README.md",
    "concepts/workspace-thread-turn.md",
    "concepts/context-and-instructions.md",
    "concepts/changes-and-recovery.md",
    "user-guide/README.md",
    "user-guide/commands.md",
    "user-guide/permissions.md",
    "user-guide/tools-and-shell.md",
    "user-guide/changes.md",
    "user-guide/configuration.md",
    "user-guide/troubleshooting.md",
    "extensions/README.md",
    "extensions/memory.md",
    "extensions/skills.md",
    "extensions/mcp.md",
    "reference/README.md",
    "reference/cli.md",
    "reference/commands.md",
    "reference/configuration.md",
    "reference/built-in-tools.md",
    "reference/permission-modes.md",
    "reference/files-and-state.md",
    "reference/protocol.md",
    "architecture/README.md",
    "architecture/request-lifecycles.md",
    "architecture/application-and-agent.md",
    "architecture/context-model-and-extensions.md",
    "architecture/tools-and-changes.md",
    "architecture/storage-and-recovery.md",
    "architecture/protocol-and-tui.md",
    "architecture/security-and-dependencies.md",
    "development/README.md",
    "development/setup.md",
    "development/testing.md",
    "development/extending-awesome.md",
    "development/contracts-and-documentation.md",
    "development/release.md",
    "README.md",
    "roadmap.md",
}
ARCHITECTURE_HEADINGS = {
    "## System Overview",
    "## Directory Structure",
    "## Recommended Reading Order",
    "## Data Flow",
    "## Major Subsystems",
    "## Design Principles",
    "## File Dependency Chain",
    "## State Ownership",
    "## Error, Cancellation, and Recovery",
    "## Extension Points",
}
ARCHITECTURE_DIAGRAM_LABELS = {
    "Entry & Presentation",
    "Python Application Host",
    "Agent Core",
    "Local State",
    "Model Providers",
    "Tool System",
    "Workspace & Host",
}
INSTALL_SH = (
    "curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/"
    "download/install.sh | sh"
)
INSTALL_PS1 = (
    "irm https://github.com/JAGGER-L/awesome_agent/releases/latest/"
    "download/install.ps1 | iex"
)
COMMANDS = (
    "new",
    "rename",
    "resume",
    "context",
    "compact",
    "model",
    "auth",
    "thinking",
    "workspace",
    "diff",
    "undo",
    "redo",
    "tools",
    "skills",
    "mcp",
    "memory",
    "status",
    "usage",
    "doctor",
    "config",
    "permissions",
    "help",
    "theme",
    "copy",
    "quit",
)


def _markdown_shape(content: str) -> tuple[list[int], int]:
    headings: list[int] = []
    fence: str | None = None
    fence_markers = 0
    for line in content.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            candidate = marker.group(1)
            if fence is None:
                fence = candidate
                fence_markers += 1
            elif candidate[0] == fence[0] and len(candidate) >= len(fence):
                fence = None
                fence_markers += 1
            continue
        if fence is None and (heading := re.match(r"^(#{1,6})\s+", line)):
            headings.append(len(heading.group(1)))
    return headings, fence_markers


def _is_non_source_markdown(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith("site/src/content/docs/"):
        return True
    return any(
        (parent / "pyvenv.cfg").is_file()
        for parent in path.parents
        if parent != ROOT and ROOT in parent.parents
    )


def test_relative_markdown_links_resolve() -> None:
    failures: list[str] = []
    markdown_files = [
        path
        for path in ROOT.rglob("*.md")
        if not _is_non_source_markdown(path)
        and not any(
            part
            in {
                ".codex",
                ".git",
                ".mypy_cache",
                ".ruff_cache",
                ".venv",
                "node_modules",
            }
            for part in path.parts
        )
    ]

    for source in markdown_files:
        content = source.read_text(encoding="utf-8")
        for target in LINK.findall(content):
            if (
                target.startswith(("http://", "https://", "#", "mailto:"))
                or "://" in target
            ):
                continue
            relative = target.split("#", 1)[0].strip("<>")
            if not relative:
                continue
            resolved = (source.parent / relative).resolve()
            if not resolved.exists():
                failures.append(f"{source.relative_to(ROOT)} -> {target}")

    assert not failures, "Broken Markdown links:\n" + "\n".join(failures)


def test_documentation_has_intent_based_sections_and_required_pages() -> None:
    actual_sections = {path.name for path in DOCS.iterdir() if path.is_dir()}
    actual_pages = {path.relative_to(DOCS).as_posix() for path in DOCS.rglob("*.md")}

    assert actual_sections == DOCS_SECTIONS
    assert not (REQUIRED_PAGES - actual_pages)
    assert (DOCS / "README.md").is_file()
    assert (DOCS / "roadmap.md").is_file()


def test_every_documentation_source_has_exactly_one_chinese_peer() -> None:
    english_sources = {
        path.relative_to(DOCS).as_posix()
        for path in DOCS.rglob("*.md")
        if not path.name.endswith(".zh-CN.md")
    }
    translated_sources = {
        path.relative_to(DOCS).as_posix() for path in DOCS.rglob("*.zh-CN.md")
    }
    expected_translations = {
        path.removesuffix(".md") + ".zh-CN.md" for path in english_sources
    }

    assert translated_sources == expected_translations
    assert (ROOT / "ARCHITECTURE.zh-CN.md").is_file()
    translated_pairs = [
        (
            translated.with_name(translated.name.replace(".zh-CN.md", ".md")),
            translated,
        )
        for translated in DOCS.rglob("*.zh-CN.md")
    ]
    translated_pairs.append((ROOT / "ARCHITECTURE.md", ROOT / "ARCHITECTURE.zh-CN.md"))
    for english, translated in translated_pairs:
        english_content = english.read_text(encoding="utf-8")
        content = translated.read_text(encoding="utf-8")
        assert re.search(r"[\u3400-\u9fff]", content), translated.relative_to(ROOT)
        assert len(content) >= len(english_content) * 0.35, translated.relative_to(ROOT)
        assert _markdown_shape(content) == _markdown_shape(english_content), (
            translated.relative_to(ROOT)
        )


def test_documentation_site_has_no_redirect_compatibility_layer() -> None:
    navigation = (ROOT / "site/docs-navigation.mjs").read_text(encoding="utf-8")
    astro_config = (ROOT / "site/astro.config.mjs").read_text(encoding="utf-8")

    assert "redirect" not in navigation.casefold()
    assert not re.search(r"^\s*redirects\s*:", astro_config, re.MULTILINE)


def test_architecture_is_the_complete_technical_entrypoint() -> None:
    content = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert not {heading for heading in ARCHITECTURE_HEADINGS if heading not in content}
    assert not {label for label in ARCHITECTURE_DIAGRAM_LABELS if label not in content}


def test_entry_docs_present_the_current_product() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    quickstarts = "\n".join(
        (DOCS / "getting-started" / name).read_text(encoding="utf-8")
        for name in ("quickstart.md", "quickstart.zh-CN.md")
    )
    combined = "\n".join((english, chinese, quickstarts))

    assert "Awesome is an AI coding assistant that runs in your terminal." in english
    assert "Awesome 是一个运行在终端中的 AI 编程助手。" in chinese
    assert (
        "Analyze this project's structure and tell me where I should start reading."
        in english
    )
    assert "分析这个项目的结构，并告诉我应该从哪里开始阅读。" in chinese  # noqa: RUF001

    for entry in (english, chinese):
        assert "████" in entry
        assert INSTALL_SH in entry
        assert INSTALL_PS1 in entry
        assert "macOS" in entry
        assert "Windows" in entry
        assert "WSL2 Ubuntu" in entry
        assert "https://git-scm.com/downloads" in entry
        assert "DeepSeek" in entry and "Kimi" in entry
        assert "--continue" in entry and "--resume" in entry

    for tool in (
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    ):
        assert f"`{tool}`" in combined
    assert "not limited to eight" in english
    assert "不限制为八个" in chinese
    assert "default off" in english
    assert "默认关闭" in chinese

    chinese_local_doc_targets = {
        target.split("#", 1)[0]
        for target in LINK.findall(chinese)
        if target.startswith("docs/")
    }
    assert chinese_local_doc_targets
    assert all(target.endswith(".zh-CN.md") for target in chinese_local_doc_targets)
    assert "(ARCHITECTURE.zh-CN.md)" in chinese
    assert "英文回退" not in chinese


def test_quickstarts_are_five_step_product_tutorials() -> None:
    english = (DOCS / "getting-started/quickstart.md").read_text(encoding="utf-8")
    chinese = (DOCS / "getting-started/quickstart.zh-CN.md").read_text(encoding="utf-8")

    for content in (english, chinese):
        steps = re.findall(r"^## (\d+)\..+$", content, re.MULTILINE)
        assert steps == ["1", "2", "3", "4", "5"]
        assert "/model" in content
        assert "/permissions" in content
        assert "--version" in content
        assert ".env" not in content
        assert "uv run" not in content
        assert "rm -rf" not in content
        assert "Remove-Item" not in content


def test_state_recovery_docs_use_the_product_flow() -> None:
    storage = (DOCS / "architecture/storage-and-recovery.md").read_text(
        encoding="utf-8"
    )
    troubleshooting = (DOCS / "user-guide/troubleshooting.md").read_text(
        encoding="utf-8"
    )
    files_and_state = (DOCS / "reference/files-and-state.md").read_text(
        encoding="utf-8"
    )

    assert "Schema 7" in storage
    assert "exclusive" in storage and "lease" in storage
    assert "Reset local state and continue" in troubleshooting
    assert "API keys" in troubleshooting
    assert "state/application.db" in files_and_state
    assert "state/checkpoints.db" in files_and_state


def test_roadmap_separates_current_behavior_from_future_directions() -> None:
    roadmap = (DOCS / "roadmap.md").read_text(encoding="utf-8")

    current = roadmap.index("## Current foundation")
    near_term = roadmap.index("## Near-term product directions")
    later = roadmap.index("## Later directions")
    assert current < near_term < later
    assert "GitHub Pages documentation site" in roadmap[current:near_term]
    for direction in (
        "One-command Skills installation",
        "Multi-Agent delegation",
        "More model providers",
        "Search tools",
        "More memory providers",
        "Scheduled tasks",
        "Gateway messaging",
        "Optional isolated tool backend",
    ):
        assert direction in roadmap[near_term:]


def test_public_reference_covers_runtime_contracts() -> None:
    commands = (DOCS / "reference/commands.md").read_text(encoding="utf-8")
    configuration = (DOCS / "reference/configuration.md").read_text(encoding="utf-8")
    tools = (DOCS / "reference/built-in-tools.md").read_text(encoding="utf-8")
    permissions = (DOCS / "reference/permission-modes.md").read_text(encoding="utf-8")
    protocol = (DOCS / "reference/protocol.md").read_text(encoding="utf-8")

    for command in COMMANDS:
        assert f"`/{command}" in commands
    for field in (
        "default_model",
        "kimi_region",
        "model_calls",
        "tool_calls",
        "provider_retries",
        "compressions",
        "active_execution_seconds",
        "total_context_tokens",
        "mcp_servers",
        "AWESOME_MODEL",
        "AWESOME_HOME",
        "AWESOME_INSTALL_DIR",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "MEM0_API_KEY",
    ):
        assert field in configuration
    for tool in (
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    ):
        assert f"`{tool}`" in tools
    for value in (
        "Request approval",
        "Accept edits",
        "Full access",
        "workspace.read",
        "workspace.write",
        "workspace.delete",
        "shell.execute",
        "MCP",
    ):
        assert value in permissions
    for method in (
        "initialize",
        "application.getState",
        "thread.list",
        "thread.read",
        "turn.submit",
        "direct.execute",
        "command.execute",
        "provider.credential.set",
        "interaction.respond",
        "operation.cancel",
        "shutdown",
    ):
        assert f"`{method}`" in protocol
    assert "protocol v4" in protocol.casefold()


def test_documentation_explains_ownership_and_validation() -> None:
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    development = (DOCS / "development/contracts-and-documentation.md").read_text(
        encoding="utf-8"
    )

    assert "## Canonical ownership" in index
    assert "## Language policy" in index
    for command in (
        "npm --prefix site run check:navigation",
        "npm --prefix site run check",
        "npm --prefix site run build",
        "npm --prefix site run check:links",
    ):
        assert command in development
    assert "llms.txt" in development
