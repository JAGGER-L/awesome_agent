from pathlib import Path


def test_runtime_docs_and_scripts_do_not_use_repo_root_output_dirs() -> None:
    checked = [
        Path("scripts/quickstart.ps1"),
        Path("docs/getting-started/quickstart.md"),
        Path("docs/operations/README.md"),
        Path("README.md"),
        Path("README.zh-CN.md"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "output\\quickstart" not in combined
    assert "output/quickstart" not in combined
    assert "e2e-output" not in combined
