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
from awesome_agent.cli.first_run import inspect_first_run_state
from awesome_agent.cli.profile import local_cli_profile
from awesome_agent.cli.repo_context import CliLaunchContext, discover_launch_context
from awesome_agent.cli.slash_commands import slash_command_help


class _ChatTui(Protocol):
    def __init__(
        self,
        *,
        api_url: str,
        run_id: str | None = None,
        launch_context: CliLaunchContext | None = None,
        first_run_summary: ConfigFlowSummary | None = None,
    ) -> None: ...

    def run(self) -> object: ...


AwesomeAgentTui: type[_ChatTui] | None = None

app = typer.Typer(
    name="awesome",
    help="Start the interactive local coding-agent CLI.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def launch(
    ctx: typer.Context,
    api_url: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root", exists=True, file_okay=False),
    ] = None,
) -> None:
    """Start the interactive local coding-agent CLI."""
    if ctx.invoked_subcommand is not None:
        return
    resolved_project_root = project_root or Path.cwd()
    launch_context = discover_launch_context(resolved_project_root)
    profile = local_cli_profile()
    state = inspect_first_run_state(
        project_root=launch_context.project_root,
        home=Path.home(),
    )
    config_summary = inspect_config_flow(
        home=Path.home(),
        project_root=launch_context.project_root,
        environ=environ,
    )
    typer.echo(f"awesome.profile={profile.name}")
    typer.echo(f"awesome.sandbox={profile.default_sandbox_backend}")
    typer.echo(
        f"awesome.context={launch_context.context_kind}:"
        f"{launch_context.display_path}"
    )
    typer.echo(f"awesome.first_run_setup_required={str(state.needs_setup).lower()}")
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
    typer.echo(f"Created or verified {path}")
    typer.echo(
        "Set AWESOME_AGENT_DEEPSEEK_API_KEY in your environment "
        "or project .env."
    )


def main() -> None:
    app()


def _load_tui() -> type[_ChatTui]:
    global AwesomeAgentTui
    if AwesomeAgentTui is None:
        from awesome_agent.tui.app import AwesomeAgentTui as LoadedTui

        AwesomeAgentTui = LoadedTui
    return AwesomeAgentTui
