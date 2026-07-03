from awesome_agent.tui.command_palette import CommandPaletteState


def test_command_palette_render_window_follows_active_suggestion() -> None:
    state = CommandPaletteState().update("/")

    for _ in range(7):
        state = state.move(1)

    rendered = state.render()

    assert "> /tools" in rendered
    assert "/new" not in rendered
