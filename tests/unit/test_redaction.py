from awesome_agent.safety.redaction import redact_text, redact_value


def test_redact_text_replaces_api_keys_and_auth_headers() -> None:
    text = (
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature"
    )

    result = redact_text(text)

    assert result.redacted is True
    assert "sk-proj-" not in result.text
    assert "eyJhbGci" not in result.text
    assert "OPENAI_API_KEY=[REDACTED:api_key]" in result.text
    assert "Authorization: [REDACTED:auth_header]" in result.text
    assert result.counts["api_key"] == 1
    assert result.counts["auth_header"] == 1


def test_redact_text_replaces_private_key_and_database_url_password() -> None:
    text = (
        "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----\n"
        "postgresql://user:pass123@example.test/db"
    )

    result = redact_text(text)

    assert "abc123" not in result.text
    assert "[REDACTED:private_key]" in result.text
    assert "postgresql://user:[REDACTED:password]@example.test/db" in result.text


def test_redact_text_replaces_label_aware_high_entropy_only() -> None:
    sha = "e3b0c44298fc1c149afbf4c8996fb924"
    token = "TOKEN=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"

    result = redact_text(f"sha={sha} {token}")

    assert sha in result.text
    assert "TOKEN=[REDACTED:token]" in result.text


def test_redact_value_recurses_and_reports_counts() -> None:
    value = {
        "stdout": "password=hunter2",
        "nested": [{"url": "https://user:secret@example.test/path"}],
    }

    redacted, report = redact_value(value)

    assert redacted["stdout"] == "password=[REDACTED:password]"
    assert redacted["nested"][0]["url"] == (
        "https://user:[REDACTED:password]@example.test/path"
    )
    assert report.applied is True
    assert report.counts["password"] == 2


def test_redact_value_preserves_guardrail_schema() -> None:
    payload = {
        "guardrails": {
            "version": 1,
            "assessments": [
                {
                    "subject": "command",
                    "operation": "execute",
                    "decision": "ask",
                    "severity": "medium",
                    "reason": "Command references token=secret-value",
                    "rule_ids": ["guard.command.sensitive_target"],
                    "targets": [
                        {
                            "kind": "command",
                            "value": "echo token=secret-value",
                            "sensitivity": "sensitive",
                        }
                    ],
                    "approval_scope": None,
                    "bypass_used": False,
                    "stats": {},
                }
            ],
        }
    }

    redacted, report = redact_value(payload)
    assessment = redacted["guardrails"]["assessments"][0]

    assert assessment["decision"] == "ask"
    assert assessment["rule_ids"] == ["guard.command.sensitive_target"]
    assert assessment["reason"] == "Command references token=[REDACTED:token]"
    assert assessment["targets"][0]["value"] == "echo token=[REDACTED:token]"
    assert report.applied is True
