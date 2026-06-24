from typing import Annotated
from uuid import UUID

import httpx
import typer

from awesome_agent import __version__
from awesome_agent.health import collect_health, is_healthy
from awesome_agent.runtime.asyncio import configure_event_loop_policy

configure_event_loop_policy()

app = typer.Typer(
    name="awesome-agent",
    help="Local-first observable coding agent.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the installed package version."""
    typer.echo(__version__)


@app.command()
def doctor(
    docker: Annotated[
        bool,
        typer.Option("--docker/--no-docker", help="Check Docker daemon health."),
    ] = True,
) -> None:
    """Check whether the local development baseline is healthy."""
    checks = collect_health(check_docker=docker)
    for check in checks:
        marker = "PASS" if check.ok else "FAIL"
        typer.echo(f"[{marker}] {check.name}: {check.detail}")
    if not is_healthy(checks):
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    """Start the local FastAPI inspection server."""
    import uvicorn

    try:
        from awesome_agent.observability.setup import configure_observability

        configure_observability()
    except ImportError:
        typer.echo(
            "Observability not installed. Run `uv sync --extra observability` "
            "for structured logging and OpenTelemetry.",
        )
    uvicorn.run("awesome_agent.api.app:app", host=host, port=port, reload=False)


@app.command()
def run(
    goal: Annotated[str, typer.Argument(help="Coding task goal.")],
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Create a run through the local API."""
    response = httpx.post(f"{api_url}/runs", json={"goal": goal}, timeout=30)
    response.raise_for_status()
    typer.echo(response.json()["id"])


@app.command()
def status(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Show run state."""
    response = httpx.get(f"{api_url}/runs/{run_id}", timeout=30)
    response.raise_for_status()
    typer.echo(response.json()["status"])


@app.command()
def agents(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """List agents in a run."""
    response = httpx.get(f"{api_url}/runs/{run_id}/agents", timeout=30)
    response.raise_for_status()
    for agent in response.json():
        typer.echo(f"{agent['id']} {agent['kind']} {agent['profile']}")


@app.command()
def todos(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """List run tasks."""
    response = httpx.get(f"{api_url}/runs/{run_id}/todos", timeout=30)
    response.raise_for_status()
    for todo in response.json():
        typer.echo(f"{todo['status']} {todo['title']}")


@app.command()
def cancel(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Cancel a run."""
    response = httpx.post(f"{api_url}/runs/{run_id}/cancel", timeout=30)
    response.raise_for_status()
    typer.echo(response.json()["status"])


@app.command()
def resume(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Resume a paused or cancelled local run."""
    response = httpx.post(f"{api_url}/runs/{run_id}/resume", timeout=30)
    response.raise_for_status()
    typer.echo(response.json()["status"])


@app.command()
def approve(
    run_id: UUID,
    approval_id: UUID,
    approved: Annotated[bool, typer.Option("--approve/--deny")] = True,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Approve or deny a pending action."""
    response = httpx.post(
        f"{api_url}/runs/{run_id}/approvals/{approval_id}",
        json={"approved": approved},
        timeout=30,
    )
    response.raise_for_status()
    typer.echo("approved" if approved else "denied")


if __name__ == "__main__":
    app()
