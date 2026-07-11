from __future__ import annotations

from awesome_agent.application.contracts import ProductError


class ApplicationFailure(Exception):
    """A validated expected failure at the surface-facing application boundary."""

    __slots__ = ("error",)

    def __init__(self, error: ProductError) -> None:
        self.error = ProductError.model_validate(error)
        Exception.__init__(self)


__all__ = ["ApplicationFailure"]
