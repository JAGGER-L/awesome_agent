from __future__ import annotations

from awesome_agent.tui.pickers import PickerItem, PickerState


def test_picker_moves_selection() -> None:
    picker = PickerState.open(
        kind="model",
        title="Select model",
        items=[
            PickerItem(id="pro", label="DeepSeek V4 Pro"),
            PickerItem(id="flash", label="DeepSeek V4 Flash"),
        ],
        selected_id="pro",
    )

    moved = picker.move(1)

    assert moved.active_item.id == "flash"


def test_picker_applies_selected_item() -> None:
    picker = PickerState.open(
        kind="thinking",
        title="Thinking mode",
        items=[PickerItem(id="on_high", label="On - high")],
    )

    selected = picker.apply()

    assert selected is not None
    assert selected.id == "on_high"


def test_picker_escape_closes_without_change() -> None:
    picker = PickerState.open(
        kind="memory",
        title="Memory",
        items=[PickerItem(id="local", label="Local memory")],
    )

    closed = picker.close()

    assert closed.items == ()
    assert closed.selected_id is None


def test_picker_does_not_apply_disabled_item() -> None:
    picker = PickerState.open(
        kind="model",
        title="Select model",
        items=[PickerItem(id="missing", label="Missing", disabled=True)],
    )

    assert picker.apply() is None


def test_picker_render_shows_active_and_selected_items() -> None:
    picker = PickerState.open(
        kind="model",
        title="Select model for this conversation",
        items=[
            PickerItem(id="pro", label="DeepSeek V4 Pro"),
            PickerItem(id="flash", label="DeepSeek V4 Flash"),
        ],
        selected_id="pro",
    )

    rendered = picker.render()

    assert "Select model for this conversation" in rendered
    assert "> * DeepSeek V4 Pro" in rendered
    assert "    DeepSeek V4 Flash" in rendered
    assert "Up/Down select - Enter apply - Esc cancel" in rendered
