from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class PickerItem:
    id: str
    label: str
    description: str = ""
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class PickerState:
    kind: str
    title: str
    items: tuple[PickerItem, ...]
    active_index: int = 0
    selected_id: str | None = None

    @classmethod
    def open(
        cls,
        *,
        kind: str,
        title: str,
        items: list[PickerItem] | tuple[PickerItem, ...],
        selected_id: str | None = None,
    ) -> PickerState:
        item_tuple = tuple(items)
        active_index = 0
        if selected_id is not None:
            for index, item in enumerate(item_tuple):
                if item.id == selected_id:
                    active_index = index
                    break
        return cls(
            kind=kind,
            title=title,
            items=item_tuple,
            active_index=active_index,
            selected_id=selected_id,
        )

    @property
    def active_item(self) -> PickerItem:
        if not self.items:
            raise IndexError("Picker has no items.")
        return self.items[self.active_index]

    def move(self, delta: int) -> PickerState:
        if not self.items:
            return self
        return replace(self, active_index=(self.active_index + delta) % len(self.items))

    def apply(self) -> PickerItem | None:
        if not self.items:
            return None
        item = self.active_item
        if item.disabled:
            return None
        return item

    def close(self) -> PickerState:
        return replace(self, items=(), active_index=0)

    def render(self) -> str:
        if not self.items:
            return ""
        lines = [self.title, ""]
        for index, item in enumerate(self.items):
            marker = ">" if index == self.active_index else " "
            selected = "*" if item.id == self.selected_id else " "
            disabled = " (unavailable)" if item.disabled else ""
            description = f" - {item.description}" if item.description else ""
            lines.append(f"{marker} {selected} {item.label}{disabled}{description}")
        lines.extend(["", "Up/Down select - Enter apply - Esc cancel"])
        return "\n".join(lines)
