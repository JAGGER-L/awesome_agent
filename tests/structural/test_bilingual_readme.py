from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bilingual_readmes_have_reciprocal_links_and_matching_structure() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    language_links = "[English](README.md) | [简体中文](README.zh-CN.md)"
    assert language_links in english
    assert language_links in chinese
    assert english.count("\n## ") == chinese.count("\n## ")

    shared_contracts = [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "AWESOME_AGENT_DEEPSEEK_API_KEY",
        "AWESOME_AGENT_MEM0_API_KEY",
        r".\scripts\check.ps1",
        r".\scripts\system-test.ps1",
    ]
    for contract in shared_contracts:
        assert contract in english
        assert contract in chinese
