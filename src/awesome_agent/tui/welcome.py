from __future__ import annotations

from rich.text import Text

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.surfaces.guidance import GuidanceSeverity, first_run_guidance

AWESOME_LOGO_WORD = "AWESOME"
SOLID_BANNER_LINES = (
    "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
    "┃  ███  █   █ █████ █████  ███  █   █ █████        ┃",
    "┃ █   █ █   █ █     █     █   █ ██ ██ █            ┃",
    "┃ █████ █ █ █ ████  █████ █   █ █ █ █ ████         ┃",
    "┃ █   █ ██ ██ █         █ █   █ █   █ █            ┃",
    "┃ █   █ █   █ █████ █████  ███  █   █ █████        ┃",
    "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
)
_GRADIENT = ("cyan", "bright_blue", "magenta", "bright_magenta")


def render_welcome(
    *,
    context_label: str | None,
    first_run_summary: ConfigFlowSummary | None,
) -> Text:
    rendered = Text()
    _append_gradient_banner(rendered)
    rendered.append("\n")
    rendered.append(f"cwd: {context_label or '-'}\n", style="dim")
    rendered.append("Type a message to start. Use /help for commands.\n", style="dim")
    if first_run_summary is not None:
        guidance_items = first_run_guidance(first_run_summary)
        if guidance_items:
            rendered.append("\n")
        for item in guidance_items:
            style = "red" if item.severity is GuidanceSeverity.ERROR else "yellow"
            rendered.append(f"{item.title}: {item.detail}\n", style=style)
            for step in item.next_steps:
                rendered.append(f"  Next: {step}\n", style="dim")
    rendered.rstrip()
    return rendered


def _append_gradient_banner(rendered: Text) -> None:
    for line_index, line in enumerate(SOLID_BANNER_LINES):
        for column_index, character in enumerate(line):
            style = _GRADIENT[(line_index + column_index) % len(_GRADIENT)]
            rendered.append(character, style=style)
        rendered.append("\n")
