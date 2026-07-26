from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_contribution_and_security_entrypoints_are_bilingual_and_discoverable() -> None:
    english_contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    chinese_contributing = (ROOT / "CONTRIBUTING.zh-CN.md").read_text(encoding="utf-8")
    english_security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    chinese_security = (ROOT / "SECURITY.zh-CN.md").read_text(encoding="utf-8")

    assert "CONTRIBUTING.zh-CN.md" in english_contributing
    assert "CONTRIBUTING.md" in chinese_contributing
    assert "SECURITY.zh-CN.md" in english_security
    assert "SECURITY.md" in chinese_security
    assert "/security/advisories/new" in english_security
    assert "/security/advisories/new" in chinese_security
    assert "/blob/main/SECURITY.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/blob/main/SECURITY.zh-CN.md" in (ROOT / "README.zh-CN.md").read_text(
        encoding="utf-8"
    )


def test_issue_forms_and_pull_request_template_cover_release_risk() -> None:
    template_root = ROOT / ".github" / "ISSUE_TEMPLATE"
    bug = yaml.safe_load((template_root / "bug_report.yml").read_text(encoding="utf-8"))
    feature = yaml.safe_load(
        (template_root / "feature_request.yml").read_text(encoding="utf-8")
    )
    configuration = yaml.safe_load(
        (template_root / "config.yml").read_text(encoding="utf-8")
    )
    pull_request = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )

    for form in (bug, feature):
        assert isinstance(form, dict)
        body = form.get("body")
        assert isinstance(body, list) and body
        identifiers = [item.get("id") for item in body if isinstance(item, dict)]
        identifiers = [item for item in identifiers if isinstance(item, str)]
        assert len(identifiers) == len(set(identifiers))
    assert configuration == {
        "blank_issues_enabled": False,
        "contact_links": [
            {
                "name": "Report a security vulnerability privately",
                "url": (
                    "https://github.com/JAGGER-L/awesome_agent/security/advisories/new"
                ),
                "about": (
                    "Do not disclose security-sensitive details in a public issue."
                ),
            }
        ],
    }
    for heading in (
        "## Summary",
        "## First-principles reasoning",
        "## Verification",
        "## Documentation and release impact",
        "## Risk and recovery",
    ):
        assert heading in pull_request
