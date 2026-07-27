from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

from pydantic import SecretStr

from awesome_agent.application.command_results import (
    CommandOutcome,
    WebStatusCommandPayload,
    error,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.config import (
    ApplicationConfig,
    LoadedConfigSources,
    UserConfigDocument,
    UserConfigWriter,
)
from awesome_agent.core.cancellation import (
    finish_cancellation_safe,
    run_cancellation_safe_blocking_call,
)
from awesome_agent.core.tools.permissions import PermissionSession

TAVILY_DISCLOSURE = (
    "Web search sends the query, and Web fetch sends the requested URL, to "
    "Tavily. Tavily processes that data under "
    "https://www.tavily.com/privacy "
    "and https://www.tavily.com/terms."
)

_DIAGNOSTIC_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,127}")

type WebConfigurationSnapshot = tuple[LoadedConfigSources, ApplicationConfig]
type ApplyWebConfiguration = Callable[
    [WebConfigurationSnapshot], Awaitable["WebRuntimeStatus"]
]


@dataclass(frozen=True, slots=True)
class WebRuntimeStatus:
    available: bool
    diagnostic_code: str | None = None


class WebConfigurationControl:
    """Session-wide serialization and recovery fence for Web configuration."""

    def __init__(self) -> None:
        self.mutation_lock = asyncio.Lock()
        self._recovery_required = False

    @property
    def recovery_required(self) -> bool:
        return self._recovery_required

    def fence(self) -> None:
        self._recovery_required = True


class WebCommandService:
    """Own user-visible Web enablement without owning the Web runtime itself."""

    def __init__(
        self,
        *,
        config_writer: UserConfigWriter,
        current_configuration: Callable[[], WebConfigurationSnapshot],
        load_configuration: Callable[[], WebConfigurationSnapshot],
        apply_configuration: ApplyWebConfiguration,
        runtime_status: Callable[[], WebRuntimeStatus],
        permission_session: PermissionSession,
        current_thread_id: Callable[[], str | None],
        validate_proxy: Callable[[SecretStr | None], None],
        configuration_control: WebConfigurationControl | None = None,
    ) -> None:
        self._config_writer = config_writer
        self._current_configuration = current_configuration
        self._load_configuration = load_configuration
        self._apply_configuration = apply_configuration
        self._runtime_status = runtime_status
        self._permission_session = permission_session
        self._current_thread_id = current_thread_id
        self._validate_proxy = validate_proxy
        self._configuration_control = configuration_control or WebConfigurationControl()

    async def web(self, intent: CommandIntent) -> CommandOutcome:
        arguments = intent.arguments
        action = "status" if not arguments else arguments[0]
        if len(arguments) > 1 or action not in {"on", "off", "status", "revoke"}:
            return error(
                "invalid_arguments",
                "Usage: /web on|off|status|revoke",
            )
        if action == "status":
            try:
                return result(self._status())
            except Exception:
                return error(
                    "web_configuration_failed",
                    "Web configuration status could not be read.",
                )
        if action == "revoke":
            thread_id = self._current_thread_id()
            if thread_id is not None:
                self._permission_session.revoke_thread_network(thread_id)
            try:
                return result(self._status())
            except Exception:
                return error(
                    "web_configuration_failed",
                    "Web configuration status could not be read.",
                )
        return await self._set_enabled(action == "on")

    def _status(
        self,
        *,
        snapshot: WebConfigurationSnapshot | None = None,
        runtime_status: WebRuntimeStatus | None = None,
    ) -> WebStatusCommandPayload:
        sources, application = snapshot or self._current_configuration()
        observed = runtime_status or self._runtime_status()
        thread_id = self._current_thread_id()
        return WebStatusCommandPayload(
            enabled=application.web.enabled,
            available=observed.available,
            credential_configured=sources.secrets.tavily_api_key is not None,
            proxy_configured=sources.secrets.web_proxy_url is not None,
            thread_authorized=(
                thread_id is not None
                and (
                    thread_id,
                    "network.read",
                )
                in self._permission_session.thread_granted_capabilities
            ),
            requests_per_turn=application.budgets.web_requests,
            diagnostic_code=_safe_diagnostic_code(
                observed.diagnostic_code,
                sensitive_values=(
                    sources.secrets.tavily_api_key,
                    sources.secrets.web_proxy_url,
                ),
            ),
            disclosure=TAVILY_DISCLOSURE,
        )

    async def _set_enabled(self, enabled: bool) -> CommandOutcome:
        async with self._configuration_control.mutation_lock:
            if self._configuration_control.recovery_required:
                return error(
                    "web_configuration_recovery_required",
                    "Web configuration could not be restored safely.",
                )

            if enabled:
                try:
                    fresh = await run_cancellation_safe_blocking_call(
                        self._load_configuration
                    )
                    if fresh[0].secrets.tavily_api_key is None:
                        return error(
                            "web_credential_missing",
                            "TAVILY_API_KEY is required before Web can be enabled.",
                        )
                    self._validate_proxy(fresh[0].secrets.web_proxy_url)
                except asyncio.CancelledError:
                    raise
                except ValueError:
                    return error(
                        "web_proxy_invalid",
                        "The explicit Web proxy configuration is invalid.",
                    )
                except Exception:
                    return error(
                        "web_configuration_failed",
                        "Web configuration could not be read safely.",
                    )

            committed: list[tuple[UserConfigDocument, UserConfigDocument]] = []
            try:
                previous, candidate = await run_cancellation_safe_blocking_call(
                    lambda: self._persist_enabled(enabled),
                    on_completed=committed.append,
                    on_abandoned=self._fence_recovery_required,
                )
                snapshot = await run_cancellation_safe_blocking_call(
                    self._load_configuration
                )
                runtime_status, cancellation = await finish_cancellation_safe(
                    self._apply_configuration(snapshot)
                )
            except asyncio.CancelledError:
                if committed:
                    await self._recover_after_cancellation(*committed[-1])
                raise
            except Exception:
                if committed and not await self._finish_recovery(*committed[-1]):
                    return self._recovery_required_outcome()
                return error(
                    "web_configuration_failed",
                    "Web configuration could not be applied.",
                )

            if cancellation is not None:
                await self._recover_after_cancellation(previous, candidate)
                raise cancellation
            if not enabled:
                self._permission_session.revoke_thread_network()
            return result(
                self._status(snapshot=snapshot, runtime_status=runtime_status)
            )

    def _persist_enabled(
        self,
        enabled: bool,
    ) -> tuple[UserConfigDocument, UserConfigDocument]:
        captured: list[UserConfigDocument] = []

        def transform(current: UserConfigDocument) -> UserConfigDocument:
            candidate = UserConfigDocument.model_validate(
                current.model_copy(
                    update={"web": current.web.model_copy(update={"enabled": enabled})}
                ).model_dump(mode="python")
            )
            captured.append(current)
            return candidate

        candidate = self._config_writer.update(transform)
        return captured[0], candidate

    def _rollback_if_current(
        self,
        previous: UserConfigDocument,
        candidate: UserConfigDocument,
    ) -> None:
        def restore(current: UserConfigDocument) -> UserConfigDocument:
            if current != candidate:
                raise RuntimeError("Web configuration changed before rollback.")
            return previous

        self._config_writer.update(restore)

    async def _recover_configuration(
        self,
        previous: UserConfigDocument,
        candidate: UserConfigDocument,
    ) -> None:
        await run_cancellation_safe_blocking_call(
            lambda: self._rollback_if_current(previous, candidate)
        )
        snapshot = await run_cancellation_safe_blocking_call(self._load_configuration)
        await self._apply_configuration(snapshot)

    async def _finish_recovery(
        self,
        previous: UserConfigDocument,
        candidate: UserConfigDocument,
    ) -> bool:
        task = asyncio.create_task(
            self._recover_configuration(previous, candidate),
            name="web-configuration-recovery",
        )
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                if task.done():
                    break
                if cancellation is None:
                    cancellation = error
            except Exception:
                break
        recovered = not task.cancelled() and task.exception() is None
        if not recovered:
            self._configuration_control.fence()
        if cancellation is not None:
            raise cancellation
        return recovered

    async def _recover_after_cancellation(
        self,
        previous: UserConfigDocument,
        candidate: UserConfigDocument,
    ) -> None:
        with suppress(asyncio.CancelledError):
            await self._finish_recovery(previous, candidate)
        # The caller's first cancellation remains authoritative. A failed
        # recovery fences later mutations across every Runtime generation.

    def _recovery_required_outcome(self) -> CommandOutcome:
        self._fence_recovery_required()
        return error(
            "web_configuration_recovery_required",
            "Web configuration could not be restored safely.",
        )

    def _fence_recovery_required(self) -> None:
        self._configuration_control.fence()


def _safe_diagnostic_code(
    code: str | None,
    *,
    sensitive_values: tuple[SecretStr | None, ...],
) -> str | None:
    if code is None:
        return None
    if _DIAGNOSTIC_CODE_PATTERN.fullmatch(code) is None:
        return "web_runtime_diagnostic_invalid"
    for secret in sensitive_values:
        if secret is not None and secret.get_secret_value() in code:
            return "web_runtime_diagnostic_invalid"
    return code


__all__ = [
    "TAVILY_DISCLOSURE",
    "WebCommandService",
    "WebConfigurationControl",
    "WebRuntimeStatus",
]
