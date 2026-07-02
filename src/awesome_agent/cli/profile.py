from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CliProfile:
    name: str
    default_sandbox_backend: Literal["local", "aio-docker"]
    requires_api_before_launch: bool


def local_cli_profile() -> CliProfile:
    return CliProfile(
        name="local-cli",
        default_sandbox_backend="local",
        requires_api_before_launch=False,
    )
