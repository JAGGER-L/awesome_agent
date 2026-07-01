import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "quickstart.ps1"
BOOTSTRAP = ROOT / "scripts" / "bootstrap.ps1"


def test_quickstart_script_exists_and_has_required_switches() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "param(" in text
    for switch in [
        "[switch]$PlanOnly",
        "[switch]$KeepRuntime",
        "[switch]$UseExistingRuntime",
        "[switch]$RunReadOnly",
    ]:
        assert switch in text
    assert "output\\quickstart" in text
    assert "quickstart.status=completed" in text


def test_quickstart_script_uses_real_project_surfaces() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for expected in [
        "bootstrap.ps1",
        "migrate.ps1",
        "docker",
        "compose",
        "awesome-agent.exe",
        "doctor",
        "config",
        "root",
        "repo",
        "probe",
        "diagnostics",
    ]:
        assert expected in text
    assert "make config" not in text
    assert "awesome-agent quickstart" not in text


def test_quickstart_does_not_embed_secret_values() -> None:
    text = SCRIPT.read_text(encoding="utf-8").lower()
    assert "your-api-key" not in text
    assert "deepseek_api_key=" not in text
    assert "api_key=" not in text


def test_quickstart_plan_only_prints_expected_steps() -> None:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-PlanOnly",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout
    for marker in [
        "quickstart.step=bootstrap",
        "quickstart.step=config",
        "quickstart.step=postgres",
        "quickstart.step=migrate",
        "quickstart.step=doctor",
        "quickstart.step=sample_repo",
        "quickstart.step=runtime",
        "quickstart.step=readiness",
        "quickstart.step=probe",
        "quickstart.status=plan",
    ]:
        assert marker in output


def test_bootstrap_supports_dependency_sync_without_doctor() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "[switch]$RunDoctor" in text
    assert '"postgres"' in text
    assert '"observability"' in text
    assert 'if ($RunDoctor)' in text
