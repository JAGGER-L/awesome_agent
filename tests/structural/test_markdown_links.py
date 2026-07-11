import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

FINAL_DOCS = {
    "README.md",
    "roadmap.md",
    "getting-started/quickstart.md",
    "getting-started/quickstart.zh-CN.md",
    "user-guide/commands.md",
    "user-guide/configuration.md",
    "user-guide/workspace-and-tools.md",
    "user-guide/memory-skills-mcp.md",
    "user-guide/troubleshooting.md",
    "architecture/README.md",
    "architecture/agent-core.md",
    "architecture/runtime-and-langgraph.md",
    "architecture/protocol-and-ink.md",
    "architecture/persistence.md",
    "architecture/security.md",
    "architecture/decisions/0001-python-langgraph-thin-runtime.md",
    "architecture/decisions/0002-sqlite-and-disposable-development-state.md",
    "architecture/decisions/0003-workspace-trust-and-execution-policy.md",
    "architecture/decisions/0004-tool-kernel-change-journal-and-commands.md",
    "architecture/decisions/0005-dual-layer-memory-with-mem0-cloud.md",
    "architecture/decisions/0006-python-core-and-ink-stdio-boundary.md",
    "development/README.md",
    "development/testing.md",
    "development/release.md",
}

INSTALL_SH = (
    "curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/"
    "download/install.sh | sh"
)
INSTALL_PS1 = (
    "irm https://github.com/JAGGER-L/awesome_agent/releases/latest/"
    "download/install.ps1 | iex"
)


def test_relative_markdown_links_resolve() -> None:
    failures: list[str] = []
    markdown_files = [
        path
        for path in ROOT.rglob("*.md")
        if not any(
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
            relative = target.split("#", 1)[0]
            if not relative:
                continue
            resolved = (source.parent / relative).resolve()
            if not resolved.exists():
                failures.append(f"{source.relative_to(ROOT)} -> {target}")

    assert not failures, "Broken Markdown links:\n" + "\n".join(failures)


def test_final_documentation_inventory_is_exact() -> None:
    actual = {
        path.relative_to(ROOT / "docs").as_posix()
        for path in (ROOT / "docs").rglob("*.md")
    }
    assert actual == FINAL_DOCS


def test_entry_docs_describe_only_the_pilot_product() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    quickstarts = "\n".join(
        (ROOT / "docs" / "getting-started" / name).read_text(encoding="utf-8")
        for name in ("quickstart.md", "quickstart.zh-CN.md")
    )
    combined = "\n".join((english, chinese, quickstarts))

    for entry in (english, chinese):
        assert "████" in entry
        assert INSTALL_SH in entry
        assert INSTALL_PS1 in entry
        assert "Apple Silicon" in entry
        assert "Windows 11 x64" in entry
        assert "WSL2 Ubuntu 24.04 x64" in entry
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

    forbidden = (
        "awesome init",
        "awesome-agent ",
        "awesome-tui",
        "--api-url",
        "Docker API",
        "Local API",
    )
    assert not {value for value in forbidden if value.casefold() in combined.casefold()}


def test_public_command_and_configuration_contract_is_documented() -> None:
    commands = (ROOT / "docs/user-guide/commands.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs/user-guide/configuration.md").read_text(
        encoding="utf-8"
    )

    for command in (
        "new",
        "resume",
        "context",
        "compact",
        "model",
        "thinking",
        "workspace",
        "diff",
        "undo",
        "redo",
        "tools",
        "skills",
        "skill",
        "mcp",
        "memory",
        "status",
        "usage",
        "doctor",
        "config",
        "init",
        "review",
        "debug",
        "test",
        "commit",
        "help",
        "theme",
        "copy",
        "quit",
    ):
        assert f"`/{command}" in commands
    for removed in (
        "/editor",
        "/details",
        "/permissions",
        "/sandbox",
        "/api",
        "/team",
    ):
        assert removed not in commands

    for value in (
        "262,144",
        "model calls: 256",
        "tool calls: 512",
        "active execution: 21,600 seconds",
        "provider retries: 6",
        "compressions: 10",
        "AWESOME_MODEL",
        "AWESOME_THINKING",
        "AWESOME_SKILL",
    ):
        assert value in configuration
