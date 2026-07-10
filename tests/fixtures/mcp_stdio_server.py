import sys

from mcp.server.fastmcp import FastMCP

server = FastMCP("awesome-agent-test-fixture")


@server.tool()
def echo(text: str) -> str:
    """Return the supplied text."""
    return text


@server.tool()
def fail() -> str:
    """Return an SDK tool error for adapter tests."""
    raise ValueError("fixture failure")


if __name__ == "__main__":
    print("fixture diagnostics use stderr", file=sys.stderr, flush=True)
    server.run(transport="stdio")
