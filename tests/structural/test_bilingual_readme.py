from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ENGLISH_SECTIONS = [
    "Choose A Mode",
    "Quick Start",
    "Configuration Basics",
    "Common Commands",
    "Documentation",
    "Safety",
]

EXPECTED_CHINESE_SECTIONS = [
    "选择使用方式",
    "快速开始",
    "配置基础",
    "常用命令",
    "文档",
    "安全提示",
]


def test_bilingual_readmes_have_reciprocal_links_and_matching_structure() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    language_links = "[English](README.md) | [简体中文](README.zh-CN.md)"
    assert language_links in english
    assert language_links in chinese
    assert _headings(english) == EXPECTED_ENGLISH_SECTIONS
    assert _headings(chinese) == EXPECTED_CHINESE_SECTIONS
    assert len(EXPECTED_ENGLISH_SECTIONS) == len(EXPECTED_CHINESE_SECTIONS)

    shared_contracts = [
        "AWESOME_AGENT_DEEPSEEK_API_KEY",
        "awesome init",
        "make install",
        "make dev",
        "make docker-start",
        "macOS/Linux",
        "awesome-agent.yaml",
        "skills/",
        "docs/README.md",
        "docs/getting-started/quickstart.md",
        "docs/getting-started/quickstart.zh-CN.md",
    ]
    for contract in shared_contracts:
        assert contract in english
        assert contract in chinese

    assert "Currently Windows only" in english
    assert "目前只支持 Windows" in chinese


def _headings(markdown: str) -> list[str]:
    return [
        line.removeprefix("## ").strip()
        for line in markdown.splitlines()
        if line.startswith("## ")
    ]


def test_readmes_include_tui_welcome_block_logo() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for text in [english, chinese]:
        assert "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓" in text
        assert "┃  ███  █   █ █████ █████  ███  █   █ █████        ┃" in text
        assert "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛" in text


def test_readme_quickstart_documents_config_deploy_run() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for expected in [
        "## Quick Start",
        "## Choose A Mode",
        "Local CLI",
        "Local API",
        "Docker API",
        "Currently Windows only",
        "macOS/Linux",
        "awesome init",
        "cd <your-project>",
        "Provider keys are not read from your project `.env`.",
    ]:
        assert expected in readme


def test_user_readmes_do_not_contain_engineering_or_operations_detail() -> None:
    forbidden = [
        "PostgreSQL",
        "Worker",
        "migration",
        "migrations",
        "AgentLoop",
        "dispatch",
        "checkpoint",
        "heartbeat",
        "alembic",
        "/health",
        "/ready",
        "awesome-agent serve",
        "awesome-agent worker",
    ]
    for path in [
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "docs" / "getting-started" / "quickstart.md",
        ROOT / "docs" / "getting-started" / "quickstart.zh-CN.md",
    ]:
        text = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in text


def test_quickstarts_document_platform_specific_cli_and_windows_api_scope() -> None:
    english = (ROOT / "docs/getting-started/quickstart.md").read_text(encoding="utf-8")
    chinese = (ROOT / "docs/getting-started/quickstart.zh-CN.md").read_text(
        encoding="utf-8"
    )

    for text in [english, chinese]:
        assert "Windows PowerShell" in text
        assert "macOS/Linux" in text
        assert "make dev" in text
        assert "make docker-start" in text
        assert "cd ~/my-project" in text

    assert "Currently Windows only" in english
    assert "目前只支持 Windows" in chinese
