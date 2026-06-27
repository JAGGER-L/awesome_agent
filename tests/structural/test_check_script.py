from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_check_script_sets_integration_database_defaults() -> None:
    script = (ROOT / "scripts" / "check.ps1").read_text(encoding="utf-8")

    assert "AWESOME_AGENT_TEST_DATABASE_URL" in script
    assert "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" in script
    assert "$env:AWESOME_AGENT_TEST_DATABASE_URL = $DefaultDatabaseUrl" in script
    assert (
        "$env:AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL = "
        "$DefaultCheckpointDatabaseUrl"
    ) in script
