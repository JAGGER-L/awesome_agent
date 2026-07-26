from __future__ import annotations


class McpUnavailable(RuntimeError):
    pass


class McpCallUncertain(RuntimeError):
    """The external call may have executed before the connection was lost."""
