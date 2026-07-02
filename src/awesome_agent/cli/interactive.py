from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from awesome_agent.cli.first_run import inspect_first_run_state
from awesome_agent.cli.profile import local_cli_profile
from awesome_agent.cli.slash_commands import slash_command_help

app = typer.Typer(
    name="awesome",
    help="Start the interactive local coding-agent CLI.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def launch(
    ctx: typer.Context,
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root", exists=True, file_okay=False),
    ] = None,
) -> None:
    """Start the interactive local coding-agent CLI."""
    if ctx.invoked_subcommand is not None:
        return
    resolved_project_root = project_root or Path.cwd()
    profile = local_cli_profile()
    state = inspect_first_run_state(
        project_root=resolved_project_root,
        home=Path.home(),
    )
    typer.echo(f"awesome.profile={profile.name}")
    typer.echo(f"awesome.sandbox={profile.default_sandbox_backend}")
    typer.echo(f"awesome.first_run_setup_required={str(state.needs_setup).lower()}")
    typer.echo("awesome.next=Task 61 chat-first TUI")


@app.command()
def commands() -> None:
    """Print slash commands supported by the interactive CLI."""
    typer.echo(slash_command_help())


def main() -> None:
    app()
