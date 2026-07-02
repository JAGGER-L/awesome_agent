from __future__ import annotations

from dataclasses import dataclass, field, replace

from awesome_agent.surfaces.commands import SlashCommandDefinition, command_suggestions


@dataclass(frozen=True, slots=True)
class CommandPaletteState:
    query: str = ""
    suggestions: list[SlashCommandDefinition] = field(default_factory=list)
    active_index: int = 0

    @property
    def is_open(self) -> bool:
        return bool(self.suggestions)

    @property
    def active(self) -> SlashCommandDefinition | None:
        if not self.suggestions:
            return None
        return self.suggestions[self.active_index]

    def update(self, value: str) -> CommandPaletteState:
        suggestions = command_suggestions(value)
        return CommandPaletteState(
            query=value,
            suggestions=suggestions,
            active_index=0,
        )

    def close(self) -> CommandPaletteState:
        return CommandPaletteState()

    def move(self, delta: int) -> CommandPaletteState:
        if not self.suggestions:
            return self
        return replace(
            self,
            active_index=(self.active_index + delta) % len(self.suggestions),
        )

    def render(self) -> str:
        if not self.suggestions:
            return ""
        lines = []
        for index, suggestion in enumerate(self.suggestions[:6]):
            marker = ">" if index == self.active_index else " "
            hint = f" {suggestion.argument_hint}" if suggestion.argument_hint else ""
            lines.append(
                f"{marker} /{suggestion.name}{hint} - {suggestion.description}"
            )
        return "\n".join(lines)


def is_command_prefix(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("/") and " " not in stripped[1:]
