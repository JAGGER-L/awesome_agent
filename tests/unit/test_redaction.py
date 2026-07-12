import logging

from awesome_agent.safety.redaction import RedactingLogFilter, redact_text, redact_value


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


def test_redacting_log_filter_redacts_formatted_message_arguments() -> None:
    record = logging.LogRecord(
        name="awesome_agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider token=%s",
        args=("abcdefghijklmnopqrstuvwxyz0123456789",),
        exc_info=None,
    )

    assert RedactingLogFilter().filter(record) is True

    message = record.getMessage()
    assert "abcdefghijklmnopqrstuvwxyz" not in message
    assert message == "provider token=[REDACTED:token]"
