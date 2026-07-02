from pathlib import Path


def test_tui_is_chat_first() -> None:
    source = Path("src/awesome_agent/tui/app.py").read_text(encoding="utf-8")

    assert "Ask awesome_agent, or type /help" in source
    assert "on_input_submitted" in source
    assert "SlashRouter" in source
