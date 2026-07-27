from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from awesome_agent.application.command_results import CommandError, CommandResult
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.web_commands import (
    WebCommandService,
    WebRuntimeStatus,
)
from awesome_agent.config import (
    ApplicationConfig,
    LoadedConfigSources,
    SecretStatus,
    SecretValues,
    UserConfigDocument,
    UserConfigWriter,
    missing_provider_credential_statuses,
    resolve_application_config,
)
from awesome_agent.core.tools.permissions import PermissionSession

type ApplyHook = Callable[
    [tuple[LoadedConfigSources, ApplicationConfig]], Awaitable[WebRuntimeStatus]
]


class _WebHarness:
    def __init__(
        self,
        config_path: Path,
        *,
        api_key: str | None = "tavily-secret-token",
        proxy: str | None = None,
        diagnostic_code: str | None = None,
    ) -> None:
        self.writer = UserConfigWriter(config_path)
        self.writer.replace(UserConfigDocument())
        self.api_key = SecretStr(api_key) if api_key is not None else None
        self.proxy = SecretStr(proxy) if proxy is not None else None
        self.thread_id: str | None = "thread_1"
        self.permissions = PermissionSession()
        self.apply_calls: list[bool] = []
        self.apply_hook: ApplyHook | None = None
        self.snapshot = self.load()
        self.runtime_status = WebRuntimeStatus(
            available=False,
            diagnostic_code=diagnostic_code,
        )
        self.service = WebCommandService(
            config_writer=self.writer,
            current_configuration=lambda: self.snapshot,
            load_configuration=self.load,
            apply_configuration=self.apply,
            runtime_status=lambda: self.runtime_status,
            permission_session=self.permissions,
            current_thread_id=lambda: self.thread_id,
            validate_proxy=self.validate_proxy,
        )

    def load(self) -> tuple[LoadedConfigSources, ApplicationConfig]:
        sources = LoadedConfigSources(
            user=self.writer.read(),
            workspace=None,
            secrets=SecretValues(
                tavily_api_key=self.api_key,
                web_proxy_url=self.proxy,
            ),
            secret_status=SecretStatus(),
            provider_credentials=missing_provider_credential_statuses(),
        )
        return sources, resolve_application_config(sources)

    async def apply(
        self,
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> WebRuntimeStatus:
        self.apply_calls.append(snapshot[0].user.web.enabled)
        if self.apply_hook is not None:
            return await self.apply_hook(snapshot)
        self.snapshot = snapshot
        self.runtime_status = WebRuntimeStatus(
            available=snapshot[0].user.web.enabled
        )
        return self.runtime_status

    def validate_proxy(self, proxy: SecretStr | None) -> None:
        if proxy is not None and proxy.get_secret_value().startswith("invalid"):
            raise ValueError(f"invalid proxy: {proxy.get_secret_value()}")


def _intent(*arguments: str) -> CommandIntent:
    return CommandIntent(name=CommandName.WEB, arguments=arguments)


def _error(outcome: object) -> CommandError:
    assert isinstance(outcome, CommandError)
    return outcome


def _result(outcome: object) -> CommandResult:
    assert isinstance(outcome, CommandResult)
    return outcome


@pytest.mark.asyncio
async def test_status_reports_only_bounded_non_secret_diagnostics(
    tmp_path: Path,
) -> None:
    secret = "tavily-secret-token"
    harness = _WebHarness(tmp_path / "config.yaml", api_key=secret)
    harness.runtime_status = WebRuntimeStatus(
        available=False,
        diagnostic_code=f"provider rejected {secret}",
    )

    outcome = _result(await harness.service.web(_intent()))

    rendered = outcome.model_dump_json()
    assert outcome.payload.kind == "web_status"
    assert outcome.payload.diagnostic_code == "web_runtime_diagnostic_invalid"
    assert outcome.payload.credential_configured is True
    assert secret not in rendered


@pytest.mark.asyncio
async def test_on_requires_credential_without_persisting(tmp_path: Path) -> None:
    harness = _WebHarness(tmp_path / "config.yaml", api_key=None)

    outcome = _error(await harness.service.web(_intent("on")))

    assert outcome.code == "web_credential_missing"
    assert harness.writer.read().web.enabled is False
    assert harness.apply_calls == []


@pytest.mark.asyncio
async def test_invalid_proxy_error_does_not_expose_proxy(tmp_path: Path) -> None:
    proxy = "invalid://proxy-user:proxy-secret@example.test"
    harness = _WebHarness(tmp_path / "config.yaml", proxy=proxy)

    outcome = _error(await harness.service.web(_intent("on")))

    assert outcome.code == "web_proxy_invalid"
    assert proxy not in outcome.model_dump_json()
    assert harness.writer.read().web.enabled is False


@pytest.mark.asyncio
async def test_on_and_off_preserve_unrelated_config_and_revoke_network_grants(
    tmp_path: Path,
) -> None:
    harness = _WebHarness(tmp_path / "config.yaml")
    harness.writer.update(
        lambda document: document.model_copy(
            update={
                "memory": document.memory.model_copy(
                    update={"local_file_memory": True}
                )
            }
        )
    )

    enabled = _result(await harness.service.web(_intent("on")))
    harness.permissions.grant_thread_network("thread_1")
    harness.permissions.grant_thread_network("thread_2")
    disabled = _result(await harness.service.web(_intent("off")))

    assert enabled.payload.kind == "web_status"
    assert enabled.payload.enabled is True
    assert disabled.payload.kind == "web_status"
    assert disabled.payload.enabled is False
    assert harness.writer.read().memory.local_file_memory is True
    assert harness.permissions.thread_granted_capabilities == frozenset()
    assert harness.apply_calls == [True, False]


@pytest.mark.asyncio
async def test_runtime_failure_restores_config_and_previous_runtime(
    tmp_path: Path,
) -> None:
    secret = "tavily-secret-token"
    harness = _WebHarness(tmp_path / "config.yaml", api_key=secret)

    async def fail_after_publication(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> WebRuntimeStatus:
        harness.snapshot = snapshot
        harness.runtime_status = WebRuntimeStatus(
            available=snapshot[0].user.web.enabled
        )
        if snapshot[0].user.web.enabled:
            raise RuntimeError(f"provider rejected {secret}")
        return harness.runtime_status

    harness.apply_hook = fail_after_publication

    outcome = _error(await harness.service.web(_intent("on")))

    assert outcome.code == "web_configuration_failed"
    assert secret not in outcome.model_dump_json()
    assert harness.writer.read().web.enabled is False
    assert harness.snapshot[0].user.web.enabled is False
    assert harness.runtime_status.available is False
    assert harness.apply_calls == [True, False]


@pytest.mark.asyncio
async def test_recovery_conflict_preserves_external_update_and_fences_mutations(
    tmp_path: Path,
) -> None:
    harness = _WebHarness(tmp_path / "config.yaml")

    async def fail_after_external_update(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> WebRuntimeStatus:
        harness.writer.update(
            lambda document: document.model_copy(
                update={
                    "memory": document.memory.model_copy(
                        update={"local_file_memory": True}
                    )
                }
            )
        )
        raise RuntimeError("runtime publication failed")

    harness.apply_hook = fail_after_external_update

    failed = _error(await harness.service.web(_intent("on")))
    blocked = _error(await harness.service.web(_intent("off")))

    assert failed.code == "web_configuration_recovery_required"
    assert blocked.code == "web_configuration_recovery_required"
    persisted = harness.writer.read()
    assert persisted.web.enabled is True
    assert persisted.memory.local_file_memory is True
    assert harness.apply_calls == [True]


@pytest.mark.asyncio
async def test_runtime_recovery_failure_is_fenced_without_exposing_details(
    tmp_path: Path,
) -> None:
    secret = "private-runtime-detail"
    harness = _WebHarness(tmp_path / "config.yaml")

    async def always_fail(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> WebRuntimeStatus:
        harness.snapshot = snapshot
        harness.runtime_status = WebRuntimeStatus(
            available=snapshot[0].user.web.enabled
        )
        raise RuntimeError(secret)

    harness.apply_hook = always_fail

    failed = _error(await harness.service.web(_intent("on")))
    blocked = _error(await harness.service.web(_intent("off")))

    assert failed.code == "web_configuration_recovery_required"
    assert blocked.code == "web_configuration_recovery_required"
    assert secret not in failed.model_dump_json()
    assert harness.writer.read().web.enabled is False
    assert harness.snapshot[0].user.web.enabled is False
    assert harness.apply_calls == [True, False]


@pytest.mark.asyncio
async def test_cancellation_during_persistence_rolls_back_committed_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _WebHarness(tmp_path / "config.yaml")
    entered = threading.Event()
    release = threading.Event()
    original_update = harness.writer.update

    def blocked_update(
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        entered.set()
        assert release.wait(timeout=5)
        return original_update(transform)

    monkeypatch.setattr(harness.writer, "update", blocked_update)
    running = asyncio.create_task(harness.service.web(_intent("on")))
    while not entered.is_set():
        await asyncio.sleep(0)

    running.cancel("cancel web persistence")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("cancel web persistence",)
    assert harness.writer.read().web.enabled is False
    assert harness.snapshot[0].user.web.enabled is False
    assert harness.apply_calls == [False]


@pytest.mark.asyncio
async def test_cancellation_after_runtime_publication_restores_both_states(
    tmp_path: Path,
) -> None:
    harness = _WebHarness(tmp_path / "config.yaml")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block_after_publication(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> WebRuntimeStatus:
        harness.snapshot = snapshot
        harness.runtime_status = WebRuntimeStatus(
            available=snapshot[0].user.web.enabled
        )
        if snapshot[0].user.web.enabled:
            entered.set()
            await release.wait()
        return harness.runtime_status

    harness.apply_hook = block_after_publication
    running = asyncio.create_task(harness.service.web(_intent("on")))
    await entered.wait()

    running.cancel("cancel web runtime")
    await asyncio.sleep(0)
    running.cancel("later cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("cancel web runtime",)
    assert harness.writer.read().web.enabled is False
    assert harness.snapshot[0].user.web.enabled is False
    assert harness.apply_calls == [True, False]


@pytest.mark.asyncio
async def test_runtime_failure_after_cancellation_preserves_first_cancellation(
    tmp_path: Path,
) -> None:
    harness = _WebHarness(tmp_path / "config.yaml")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fail_late(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> WebRuntimeStatus:
        if snapshot[0].user.web.enabled:
            entered.set()
            await release.wait()
            raise RuntimeError("late runtime failure with private diagnostics")
        harness.snapshot = snapshot
        harness.runtime_status = WebRuntimeStatus(available=False)
        return harness.runtime_status

    harness.apply_hook = fail_late
    running = asyncio.create_task(harness.service.web(_intent("on")))
    await entered.wait()
    running.cancel("first cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("first cancellation",)
    assert harness.writer.read().web.enabled is False
    assert harness.snapshot[0].user.web.enabled is False
    assert harness.apply_calls == [True, False]


@pytest.mark.asyncio
async def test_revoke_without_current_thread_does_not_revoke_other_threads(
    tmp_path: Path,
) -> None:
    harness = _WebHarness(tmp_path / "config.yaml")
    harness.permissions.grant_thread_network("thread_1")
    harness.thread_id = None

    _result(await harness.service.web(_intent("revoke")))

    assert harness.permissions.thread_granted_capabilities == frozenset(
        {("thread_1", "network.read")}
    )
