from __future__ import annotations

from os import environ
from pathlib import Path
from typing import Annotated, Protocol

import typer

from awesome_agent.cli.config_flow import (
    ConfigFlowSummary,
    create_default_user_config,
    inspect_config_flow,
)
from awesome_agent.cli.repo_context import CliLaunchContext, discover_launch_context
from awesome_agent.cli.slash_commands import slash_command_help
from awesome_agent.settings import Settings
from awesome_agent.surfaces.guidance import build_cli_doctor_report


class _ChatTui(Protocol):
    def __init__(
        self,
        *,
        api_url: str | None = None,
        run_id: str | None = None,
        launch_context: CliLaunchContext | None = None,
        first_run_summary: ConfigFlowSummary | None = None,
    ) -> None: ...

    def run(self) -> object: ...


AwesomeAgentTui: type[_ChatTui] | None = None

app = typer.Typer(
    name="awesome",
    help=(
        "Start the interactive local coding-agent CLI as the local full-screen "
        "TUI. Uses embedded local runtime by default; pass --api-url to connect "
        "to an API server."
    ),
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def launch(
    ctx: typer.Context,
    api_url: Annotated[
        str | None,
        typer.Option(
            "--api-url",
            help="Connect to an API server instead of embedded local runtime mode.",
        ),
    ] = None,
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root", exists=True, file_okay=False),
    ] = None,
) -> None:
    """Start the local full-screen TUI."""
    if ctx.invoked_subcommand is not None:
        return
    resolved_project_root = project_root or Path.cwd()
    launch_context = discover_launch_context(resolved_project_root)
    settings = Settings()
    config_summary = inspect_config_flow(
        home=Path.home(),
        project_root=launch_context.project_root,
        environ=environ,
        settings_api_key_configured=settings.deepseek_api_key is not None,
    )
    _load_tui()(
        api_url=api_url,
        run_id=None,
        launch_context=launch_context,
        first_run_summary=config_summary,
    ).run()


@app.command()
def commands() -> None:
    """Print slash commands supported by the interactive CLI."""
    typer.echo(slash_command_help())


@app.command("init")
def init_config() -> None:
    """Create the default user config without storing secrets."""
    path = create_default_user_config(Path.home())
    typer.echo("Awesome Agent initialized")
    typer.echo("")
    typer.echo(f"OK    User config: {path}")
    typer.echo("")
    typer.echo("Next:")
    typer.echo("  Set AWESOME_AGENT_DEEPSEEK_API_KEY in your environment.")
    typer.echo("  Run: awesome doctor")
    typer.echo("  Start: cd <project>; awesome")


@app.command("doctor")
def doctor(
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root", exists=True, file_okay=False),
    ] = None,
) -> None:
    """Check local CLI first-run setup."""
    resolved_project_root = project_root or Path.cwd()
    settings = Settings()
    summary = inspect_config_flow(
        home=Path.home(),
        project_root=resolved_project_root,
        environ=environ,
        settings_api_key_configured=settings.deepseek_api_key is not None,
    )
    report = build_cli_doctor_report(
        summary,
        deepseek_base_url=settings.deepseek_base_url,
    )
    typer.echo(report.render())
    raise typer.Exit(report.exit_code)


def main() -> None:
    app()


def _load_tui() -> type[_ChatTui]:
    global AwesomeAgentTui
    if AwesomeAgentTui is None:
        from awesome_agent.tui.app import AwesomeAgentTui as LoadedTui

        AwesomeAgentTui = LoadedTui
    return AwesomeAgentTui
