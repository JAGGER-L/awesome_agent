import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID

import httpx
import typer

from awesome_agent import __version__
from awesome_agent.domain.models import Repository
from awesome_agent.health import (
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    ReadinessReport,
    collect_readiness,
)
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.repository_registry import (
    PostgresRepositoryRegistry,
)
from awesome_agent.persistence.worker_heartbeats import (
    PostgresWorkerHeartbeatRepository,
)
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.service import RepositoryService
from awesome_agent.runtime.asyncio import configure_event_loop_policy
from awesome_agent.runtime.supervisor import run_supervisor
from awesome_agent.runtime.worker_app import run_worker
from awesome_agent.settings import Settings

configure_event_loop_policy()

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class _TuiApp(Protocol):
    def __init__(
        self,
        *,
        api_url: str,
        run_id: str | None,
        refresh_interval: float,
    ) -> None: ...

    def run(self) -> object: ...


AwesomeAgentTui: type[_TuiApp] | None = None

app = typer.Typer(
    name="awesome-agent",
    help="Local-first observable coding agent.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Manage local configuration.")
root_app = typer.Typer(help="Manage allowed repository roots.")
repo_app = typer.Typer(help="Manage registered Git repositories.")
workspace_app = typer.Typer(help="Inspect and clean managed run workspaces.")
extensions_app = typer.Typer(help="Inspect extension catalogs.")
config_app.add_typer(root_app, name="root")
app.add_typer(config_app, name="config")
app.add_typer(repo_app, name="repo")
app.add_typer(workspace_app, name="workspace")
app.add_typer(extensions_app, name="extensions")


@app.command()
def version() -> None:
    """Print the installed package version."""
    typer.echo(__version__)


@app.command()
def budget(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print token and active-time budget totals for a Run."""
    response = httpx.get(f"{api_url}/runs/{run_id}/budget", timeout=30)
    response.raise_for_status()
    body = response.json()
    typer.echo(
        " ".join(
            [
                f"run_id={body['run_id']}",
                f"total_tokens={body['total_tokens']}",
                f"input={body['input_tokens']}",
                f"output={body['output_tokens']}",
                f"reasoning={body['reasoning_tokens']}",
                f"active_seconds={body['active_seconds']}",
                f"model_calls={body['model_call_count']}",
                f"threshold={body['threshold_status']}",
            ]
        )
    )


@app.command()
def diagnostics(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print a redacted operational diagnostic summary for a Run."""
    response = httpx.get(f"{api_url}/runs/{run_id}/diagnostics", timeout=30)
    response.raise_for_status()
    body = response.json()
    dispatch = body["dispatch"] or {}
    team = body.get("team") or {}
    typer.echo(
        " ".join(
            [
                f"run_id={body['run_id']}",
                f"status={body['status']['status']}",
                f"mode={body['status']['mode']}",
                f"dispatch={dispatch.get('status')}",
                f"agents={body['agents']['total']}",
                f"total_tokens={body['budgets']['total_tokens']}",
                f"threshold={body['budgets']['threshold_status']}",
                f"model_calls={body['models']['total']}",
                f"model_failures={body['models']['failed']}",
                f"tools={body['tools']['total']}",
                f"validation_reports={body['validation']['reports_total']}",
                f"failed_gates={body['validation']['failed_gates']}",
                f"children={team.get('child_runs_total', 0)}",
            ]
        )
    )
    for warning in body.get("warnings", []):
        typer.echo(f"warning={warning['kind']} {warning['message']}")


@app.command("recovery-metrics")
def recovery_metrics(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print read-only recovery and team tuning evidence for a Run."""
    response = httpx.get(f"{api_url}/runs/{run_id}/recovery-metrics", timeout=30)
    response.raise_for_status()
    body = response.json()
    actions = ",".join(
        f"{item['key']}:{item['count']}" for item in body.get("by_action", [])
    )
    typer.echo(
        " ".join(
            [
                f"run_id={body['run_id']}",
                f"actions={body['totals']['actions']}",
                f"model_calls={body['totals']['model_calls']}",
                f"failed_model_calls={body['totals']['failed_model_calls']}",
                f"total_tokens={body['budgets']['total_tokens']}",
                f"threshold={body['budgets']['threshold_status']}",
                f"verifier_rework={body['verifier']['rework_requests']}",
                f"actions_by_kind={actions}",
            ]
        )
    )
    for warning in body.get("warnings", []):
        typer.echo(f"warning={warning['kind']} {warning['message']}")


@extensions_app.command("catalog")
def extensions_catalog(
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print the active extension catalog inventory summary."""
    response = httpx.get(f"{api_url}/extensions/catalog", timeout=30)
    response.raise_for_status()
    body = response.json()
    sources = body.get("sources", [])
    tools = body.get("tools", [])
    skills = body.get("skills", [])
    typer.echo(
        " ".join(
            [
                f"version={body['version']}",
                f"sources={len(sources)}",
                f"tools={len(tools)}",
                f"skills={len(skills)}",
            ]
        )
    )
    for source in sources:
        health = source.get("health") or {}
        typer.echo(f"source={source['id']} status={health.get('status')}")


@extensions_app.command("diagnostics")
def extensions_diagnostics(
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print redacted extension operational diagnostics."""
    response = httpx.get(f"{api_url}/extensions/diagnostics", timeout=30)
    response.raise_for_status()
    body = response.json()
    catalog = body.get("catalog") or {}
    typer.echo(
        " ".join(
            [
                f"version={body['active_catalog_version']}",
                f"sources={catalog.get('sources', 0)}",
                f"tools={catalog.get('tools', 0)}",
                f"skills={catalog.get('skills', 0)}",
                f"unhealthy_sources={len(body.get('unhealthy_sources', []))}",
                f"denials={len(body.get('denials', []))}",
                f"invocation_denials={len(body.get('invocation_denials', []))}",
                f"execution_errors={len(body.get('execution_errors', []))}",
            ]
        )
    )
    for warning in body.get("warnings", []):
        typer.echo(f"warning={warning['kind']} {warning['message']}")


@extensions_app.command("catalog-diff")
def extensions_catalog_diff(
    from_version: str,
    to_version: str,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print a redacted diff between two extension catalog versions."""
    response = httpx.get(
        (
            f"{api_url}/extensions/catalog-diff?"
            f"from_version={from_version}&to_version={to_version}"
        ),
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    typer.echo(
        " ".join(
            [
                f"from={body['from_version']}",
                f"to={body['to_version']}",
                f"added_tools={len(body.get('added_tools', []))}",
                f"removed_tools={len(body.get('removed_tools', []))}",
                f"changed_tools={len(body.get('changed_tools', []))}",
                f"added_sources={len(body.get('added_sources', []))}",
                f"removed_sources={len(body.get('removed_sources', []))}",
                f"changed_sources={len(body.get('changed_sources', []))}",
            ]
        )
    )
    for tool_name in body.get("added_tools", []):
        typer.echo(f"added_tool={tool_name}")
    for tool_name in body.get("removed_tools", []):
        typer.echo(f"removed_tool={tool_name}")


@app.command("context-compactions")
def context_compactions(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Print context compaction history for a Run."""
    response = httpx.get(
        f"{api_url}/runs/{run_id}/context-compactions",
        timeout=30,
    )
    response.raise_for_status()
    for compaction in response.json():
        artifact_refs = ", ".join(compaction.get("artifact_refs", []))
        typer.echo(
            " ".join(
                [
                    f"{compaction['runtime_route']}",
                    f"before={compaction['before_estimated_tokens']}",
                    f"after={compaction['after_estimated_tokens']}",
                    f"artifacts={artifact_refs}",
                    f"summary={compaction['summary']}",
                ]
            )
        )


@app.command()
def doctor(
    profile: Annotated[
        ReadinessProfile,
        typer.Option("--profile", help="Readiness profile to evaluate."),
    ] = ReadinessProfile.API,
    docker: Annotated[
        bool,
        typer.Option("--docker/--no-docker", help="Check Docker daemon health."),
    ] = True,
) -> None:
    """Check whether local API or runtime dependencies are ready."""
    report = asyncio.run(_collect_doctor_readiness(profile, check_docker=docker))
    for check in report.checks:
        _print_health_check(check)
    if report.status is HealthStatus.UNHEALTHY:
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


@workspace_app.command("list")
def workspace_list(
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """List managed execution workspaces."""
    response = httpx.get(f"{api_url}/workspaces", timeout=30)
    response.raise_for_status()
    _print_workspace_candidates(response.json())


@workspace_app.command("cleanup")
def workspace_cleanup(
    run_id: Annotated[
        UUID | None,
        typer.Option("--run-id", help="Clean one Run workspace."),
    ] = None,
    older_than: Annotated[
        str | None,
        typer.Option("--older-than", help="Clean workspaces older than a duration."),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply cleanup. Without this, only preview."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow failed or dirty workspace cleanup."),
    ] = False,
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Required when --force is used."),
    ] = None,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Preview or apply managed workspace cleanup."""
    if force and not (reason and reason.strip()):
        raise typer.BadParameter("--force requires --reason.")
    endpoint = "cleanup" if apply else "cleanup-preview"
    response = httpx.post(
        f"{api_url}/workspaces/{endpoint}",
        json={
            "run_id": str(run_id) if run_id is not None else None,
            "older_than": older_than,
            "force": force,
            "reason": reason,
        },
        timeout=30,
    )
    response.raise_for_status()
    _print_workspace_candidates(response.json())


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
    unsafe_bind_public: Annotated[
        bool,
        typer.Option(
            "--unsafe-bind-public",
            help=(
                "Allow binding the unauthenticated local API to a non-loopback host."
            ),
        ),
    ] = False,
) -> None:
    """Start the local FastAPI inspection server."""
    import uvicorn

    _reject_public_bind_without_consent(host, unsafe_bind_public)
    _set_api_bind_environment(host, unsafe_bind_public)
    uvicorn.run(
        "awesome_agent.api.app:app",
        host=host,
        port=port,
        reload=False,
        loop="none",
    )


@app.command()
def worker(
    once: Annotated[
        bool,
        typer.Option("--once", help="Process at most one runtime probe."),
    ] = False,
) -> None:
    """Run one durable background Worker."""
    processed = asyncio.run(run_worker(once=once))
    if once and not processed:
        typer.echo("no eligible runtime probe")


@app.command()
def start(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
    unsafe_bind_public: Annotated[
        bool,
        typer.Option(
            "--unsafe-bind-public",
            help=(
                "Allow binding the unauthenticated local API to a non-loopback host."
            ),
        ),
    ] = False,
) -> None:
    """Start independent local API and Worker child processes."""
    _reject_public_bind_without_consent(host, unsafe_bind_public)
    _set_api_bind_environment(host, unsafe_bind_public)
    result = run_supervisor(
        host=host,
        port=port,
        shutdown_timeout=Settings().worker_shutdown_grace_seconds,
        unsafe_bind_public=unsafe_bind_public,
    )
    if result.return_code != 0:
        typer.echo(
            f"{result.service} exited with code {result.return_code}",
            err=True,
        )
        raise typer.Exit(result.return_code)


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
    team: Annotated[
        bool,
        typer.Option("--team", help="Run through the explicit team runtime."),
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
            "mode": "team" if team else "solo",
        },
        timeout=30,
    )
    response.raise_for_status()
    typer.echo(response.json()["id"])


@app.command()
def probe(
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
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """Create a diagnostic durable-runtime probe Run."""
    repository = _run_with_repository_service(lambda service: service.register(repo))
    response = httpx.post(
        f"{api_url}/runtime/probes",
        json={"repository_id": str(repository.id)},
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
def children(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """List direct child runs."""
    response = httpx.get(f"{api_url}/runs/{run_id}/children", timeout=30)
    response.raise_for_status()
    for child in response.json():
        typer.echo(
            f"{child['id']} depth={child['depth']} "
            f"role={child['child_role']} status={child['status']}"
        )


@app.command("team-assignments")
def team_assignments(
    run_id: UUID,
    include_all: Annotated[bool, typer.Option("--all")] = False,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """List team assignments for a root run."""
    response = httpx.get(
        f"{api_url}/runs/{run_id}/team/assignments",
        params={"all": include_all},
        timeout=30,
    )
    response.raise_for_status()
    for assignment in response.json():
        typer.echo(
            f"{assignment['id']} {assignment['kind']} "
            f"{assignment['status']} child={assignment['child_run_id']}"
        )


@app.command("team-mailbox")
def team_mailbox(
    run_id: UUID,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
) -> None:
    """List team mailbox messages for a run."""
    response = httpx.get(f"{api_url}/runs/{run_id}/team/mailbox", timeout=30)
    response.raise_for_status()
    for message in response.json():
        typer.echo(
            f"{message['id']} {message['route']} "
            f"{message['status']} {message['subject']}"
        )


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


@app.command()
def tui(
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
    run_id: Annotated[str | None, typer.Option()] = None,
    refresh_interval: Annotated[
        float,
        typer.Option(min=0.5, max=60.0),
    ] = 2.0,
) -> None:
    """Open the local terminal operator console."""
    app_type = _load_tui_app()
    tui_app = app_type(
        api_url=api_url,
        run_id=run_id,
        refresh_interval=refresh_interval,
    )
    tui_app.run()


def _print_workspace_candidates(candidates: list[dict[str, Any]]) -> None:
    typer.echo("run_id status can_cleanup dirty reason")
    for candidate in candidates:
        typer.echo(
            f"{candidate['run_id']} "
            f"{candidate['status']} "
            f"{candidate['can_cleanup']} "
            f"{candidate['dirty']} "
            f"{candidate['reason']}"
        )


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


def _load_tui_app() -> type[_TuiApp]:
    global AwesomeAgentTui
    if AwesomeAgentTui is None:
        from awesome_agent.tui.app import AwesomeAgentTui as LoadedTui

        AwesomeAgentTui = LoadedTui
    return AwesomeAgentTui


def _reject_public_bind_without_consent(
    host: str,
    unsafe_bind_public: bool,
) -> None:
    if host in _LOOPBACK_HOSTS or unsafe_bind_public:
        return
    raise typer.BadParameter(
        "The local API is unauthenticated. Use a loopback host such as "
        "127.0.0.1, or pass --unsafe-bind-public to expose it explicitly."
    )


async def _collect_doctor_readiness(
    profile: ReadinessProfile,
    *,
    check_docker: bool,
) -> ReadinessReport:
    settings = Settings()
    if profile is not ReadinessProfile.RUNTIME:
        return await collect_readiness(
            settings,
            profile,
            check_docker=check_docker,
        )

    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    try:
        return await collect_readiness(
            settings,
            profile,
            check_docker=check_docker,
            worker_heartbeat_repository=PostgresWorkerHeartbeatRepository(sessions),
        )
    finally:
        await engine.dispose()


def _print_health_check(check: HealthCheck) -> None:
    marker = {
        HealthStatus.HEALTHY: "PASS",
        HealthStatus.DEGRADED: "WARN",
        HealthStatus.UNHEALTHY: "FAIL",
    }[check.status]
    typer.echo(f"[{marker}] {check.name}: {check.detail}")
    if check.remediation:
        typer.echo(f"       fix: {check.remediation}")


def _set_api_bind_environment(host: str, unsafe_bind_public: bool) -> None:
    os.environ["AWESOME_AGENT_API_HOST"] = host
    os.environ["AWESOME_AGENT_UNSAFE_BIND_PUBLIC"] = (
        "true" if unsafe_bind_public else "false"
    )


if __name__ == "__main__":
    app()
