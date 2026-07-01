from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ENGLISH_SECTIONS = [
    "What It Is",
    "Why It Exists",
    "Core Capabilities",
    "Quick Start",
    "First Run",
    "Extensions",
    "Operations",
    "Architecture At A Glance",
    "Current Maturity",
    "Documentation",
    "Security Note",
]

EXPECTED_CHINESE_SECTIONS = [
    "项目是什么",
    "为什么存在",
    "核心能力",
    "快速开始",
    "第一次运行",
    "扩展",
    "运维",
    "架构概览",
    "当前成熟度",
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
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "AWESOME_AGENT_DEEPSEEK_API_KEY",
        r".\scripts\bootstrap.ps1",
        r".\scripts\migrate.ps1",
        "awesome-agent.yaml",
        "skills/",
        "docs/README.md",
        "docs/getting-started/quickstart.md",
    ]
    for contract in shared_contracts:
        assert contract in english
        assert contract in chinese


def _headings(markdown: str) -> list[str]:
    return [
        line.removeprefix("## ").strip()
        for line in markdown.splitlines()
        if line.startswith("## ")
    ]
