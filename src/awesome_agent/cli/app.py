import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
import typer

from awesome_agent import __version__
from awesome_agent.domain.models import Repository
from awesome_agent.health import collect_health, is_healthy
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.repository_registry import (
    PostgresRepositoryRegistry,
)
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.service import RepositoryService
from awesome_agent.runtime.asyncio import configure_event_loop_policy
from awesome_agent.settings import Settings

configure_event_loop_policy()

app = typer.Typer(
    name="awesome-agent",
    help="Local-first observable coding agent.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Manage local configuration.")
root_app = typer.Typer(help="Manage allowed repository roots.")
repo_app = typer.Typer(help="Manage registered Git repositories.")
config_app.add_typer(root_app, name="root")
app.add_typer(config_app, name="config")
app.add_typer(repo_app, name="repo")


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


@root_app.command("add")
def config_root_add(path: Path) -> None:
    """Allow repository registration under PATH."""
    if not path.is_dir():
        raise typer.BadParameter(f"Directory does not exist: {path}")
    settings = Settings()
    config = LocalRepositoryConfigStore(settings.local_config_path)
    updated = config.add_root(path)
    typer.echo(str(updated.allowed_roots[-1]))


@root_app.command("list")
def config_root_list() -> None:
    """List allowed repository roots."""
    settings = Settings()
    config = LocalRepositoryConfigStore(settings.local_config_path).load()
    for root in config.allowed_roots:
        typer.echo(str(root))


@root_app.command("remove")
def config_root_remove(
    path: Path,
    force: Annotated[
        bool,
        typer.Option(help="Disable dependent repositories before removal."),
    ] = False,
) -> None:
    """Remove an allowed root."""

    async def remove(service: RepositoryService) -> int:
        return len(await service.remove_allowed_root(path, force=force))

    try:
        disabled = _run_with_repository_service(remove)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"removed {path.resolve()} (disabled {disabled} repositories)")


@repo_app.command("add")
def repo_add(path: Path) -> None:
    """Register or refresh a local Git repository."""
    repository = _run_with_repository_service(lambda service: service.register(path))
    typer.echo(str(repository.id))


@repo_app.command("list")
def repo_list(
    all_repositories: Annotated[
        bool,
        typer.Option("--all", help="Include disabled repositories."),
    ] = False,
) -> None:
    """List registered repositories."""

    async def list_registered(
        service: RepositoryService,
    ) -> list[Repository]:
        return list(await service.registry.list(enabled_only=not all_repositories))

    repositories = _run_with_repository_service(list_registered)
    for repository in repositories:
        typer.echo(
            f"{repository.id} "
            f"{'enabled' if repository.enabled else 'disabled'} "
            f"{repository.root}"
        )


@repo_app.command("show")
def repo_show(repository_id: UUID) -> None:
    """Show one registered repository."""

    async def get(service: RepositoryService) -> Repository:
        return await service.registry.get(repository_id)

    try:
        repository = _run_with_repository_service(get)
    except KeyError as error:
        raise typer.BadParameter("Repository not found.") from error
    typer.echo(repository.model_dump_json(indent=2))


@repo_app.command("disable")
def repo_disable(repository_id: UUID) -> None:
    """Disable a registered repository."""

    async def disable(service: RepositoryService) -> Repository:
        return await service.registry.disable(repository_id)

    try:
        repository = _run_with_repository_service(disable)
    except KeyError as error:
        raise typer.BadParameter("Repository not found.") from error
    typer.echo(f"disabled {repository.id}")


@repo_app.command("relocate")
def repo_relocate(repository_id: UUID, path: Path) -> None:
    """Explicitly relocate a repository identity to PATH."""
    try:
        repository = _run_with_repository_service(
            lambda service: service.relocate(repository_id, path)
        )
    except (KeyError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"{repository.id} {repository.root}")


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
    repo: Annotated[
        Path,
        typer.Option(
            "--repo",
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Registered Git repository path.",
        ),
    ],
    read_only: Annotated[
        bool,
        typer.Option("--read-only", help="Deny repository mutation tools."),
    ] = False,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Create a run through the local API."""
    repository = _run_with_repository_service(lambda service: service.register(repo))
    response = httpx.post(
        f"{api_url}/runs",
        json={
            "repository_id": str(repository.id),
            "goal": goal,
            "intent": "read_only" if read_only else "modifying",
        },
        timeout=30,
    )
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


def _run_with_repository_service[T](
    operation: Callable[[RepositoryService], Awaitable[T]],
) -> T:
    async def execute() -> T:
        settings = Settings()
        engine = create_engine(settings.database_url)
        service = RepositoryService(
            registry=PostgresRepositoryRegistry(create_session_factory(engine)),
            config=LocalRepositoryConfigStore(settings.local_config_path),
        )
        try:
            return await operation(service)
        finally:
            await engine.dispose()

    return asyncio.run(execute())


if __name__ == "__main__":
    app()
