from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from awesome_agent.config import (
    CredentialSource,
    ProviderCredentialTransactionJournal,
    ProviderCredentialTransactionPhase,
    UserConfigWriter,
    UserSecretStore,
    read_user_config_document,
)
from awesome_agent.config.model_transaction import (
    ProviderModelTransactionJournal,
    ProviderModelTransactionPhase,
)
from awesome_agent.conversation import ConversationService
from awesome_agent.memory.local_file import LocalMemoryFile
from awesome_agent.memory.models import MemoryScope
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.conversations import SQLiteConversationRepositories

_WORKER = Path(__file__).parents[2] / "fixtures" / "user_state_concurrency_worker.py"


def _wait_for(paths: tuple[Path, ...], *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not all(path.exists() for path in paths):
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for worker processes.")
        time.sleep(0.01)


def _run_workers(
    tmp_path: Path,
    *,
    kind: str,
    path: Path,
    actions: tuple[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = tmp_path / "start"
    ready = (tmp_path / "ready-a", tmp_path / "ready-b")
    markers = (tmp_path / "read-a", tmp_path / "read-b")
    results = (tmp_path / "result-a", tmp_path / "result-b")
    processes = tuple(
        subprocess.Popen(
            [
                sys.executable,
                str(_WORKER),
                kind,
                str(path),
                action,
                str(ready[index]),
                str(start),
                str(markers[index]),
                str(markers[1 - index]),
                str(results[index]),
            ],
            cwd=Path(__file__).parents[3],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index, action in enumerate(actions)
    )
    try:
        _wait_for(ready)
        start.write_text("start", encoding="utf-8")
        completed = tuple(process.communicate(timeout=15.0) for process in processes)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert [process.returncode for process in processes] == [0, 0], completed
    assert all(path.is_file() for path in results)
    return tuple(json.loads(path.read_text(encoding="utf-8")) for path in results)


def _spawn_worker(
    *,
    kind: str,
    path: Path,
    action: str,
    ready: Path,
    start: Path,
    marker: Path,
    coordination_marker: Path,
    result: Path,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            str(_WORKER),
            kind,
            str(path),
            action,
            str(ready),
            str(start),
            str(marker),
            str(coordination_marker),
            str(result),
        ],
        cwd=Path(__file__).parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_user_config_updates_are_serialized_across_processes(tmp_path: Path) -> None:
    config = tmp_path / "home" / "config.yaml"
    _run_workers(
        tmp_path,
        kind="config",
        path=config,
        actions=("model", "memory"),
    )

    document = read_user_config_document(config)
    assert document.providers.default_model == "deepseek/deepseek-v4-pro"
    assert document.memory.local_file_memory is True


def test_user_credentials_are_serialized_across_processes(tmp_path: Path) -> None:
    credentials = tmp_path / "home" / ".env"
    _run_workers(
        tmp_path,
        kind="credentials",
        path=credentials,
        actions=("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY"),
    )

    values = dotenv_values(credentials)
    assert values["DEEPSEEK_API_KEY"] == "secret-deepseek_api_key"
    assert values["MOONSHOT_API_KEY"] == "secret-moonshot_api_key"


def test_user_memory_compare_and_replace_is_atomic_across_processes(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / "home" / "USER.md"
    results = _run_workers(
        tmp_path,
        kind="memory",
        path=memory_path,
        actions=("1", "2"),
    )

    assert sorted(result["status"] for result in results) == [
        "added",
        "memory_conflict",
    ]
    document = LocalMemoryFile(
        path=memory_path,
        scope=MemoryScope.USER,
    ).snapshot()
    assert len(document.entries) == 1


def test_provider_credential_transaction_is_serialized_across_processes(
    tmp_path: Path,
) -> None:
    config = tmp_path / "home" / "config.yaml"
    add_ready = tmp_path / "add-ready"
    add_start = tmp_path / "add-start"
    secret_written = tmp_path / "secret-written"
    release_add = tmp_path / "release-add"
    add_result = tmp_path / "add-result"
    delete_ready = tmp_path / "delete-ready"
    delete_start = tmp_path / "delete-start"
    delete_entered = tmp_path / "delete-entered"
    delete_attempting = tmp_path / "delete-attempting"
    delete_result = tmp_path / "delete-result"
    processes: list[subprocess.Popen[str]] = []
    completed: list[tuple[str, str]] = []

    try:
        add_process = _spawn_worker(
            kind="provider_transaction",
            path=config,
            action="add",
            ready=add_ready,
            start=add_start,
            marker=secret_written,
            coordination_marker=release_add,
            result=add_result,
        )
        processes.append(add_process)
        _wait_for((add_ready,))
        add_start.write_text("start", encoding="utf-8")
        _wait_for((secret_written,))

        delete_process = _spawn_worker(
            kind="provider_transaction",
            path=config,
            action="delete",
            ready=delete_ready,
            start=delete_start,
            marker=delete_entered,
            coordination_marker=delete_attempting,
            result=delete_result,
        )
        processes.append(delete_process)
        _wait_for((delete_ready,))
        delete_start.write_text("start", encoding="utf-8")
        _wait_for((delete_attempting,))

        deadline = time.monotonic() + 0.25
        while not delete_entered.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not delete_entered.exists()

        release_add.write_text("release", encoding="utf-8")
        completed = [process.communicate(timeout=15.0) for process in processes]
    finally:
        release_add.write_text("release", encoding="utf-8")
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert [process.returncode for process in processes] == [0, 0], completed
    assert add_result.is_file()
    assert delete_result.is_file()
    assert delete_entered.is_file()
    assert "MEM0_API_KEY" not in dotenv_values(config.with_name(".env"))
    document = read_user_config_document(config)
    assert document.credentials.mem0 is CredentialSource.AWESOME


def _run_provider_configuration_race(
    tmp_path: Path,
    *,
    kind: str,
    first_action: str,
    second_action: str,
) -> tuple[dict[str, str], dict[str, str], str]:
    home = tmp_path / "home"
    config = home / "config.yaml"
    database = home / "application.db"
    home.mkdir(parents=True)
    conversation = ConversationService(store=SQLiteConversationRepositories(database))
    thread = conversation.create_thread("workspace_1")
    (home / "thread-id").write_text(thread.id, encoding="utf-8")
    secrets = UserSecretStore(home / ".env")
    secrets.set("DEEPSEEK_API_KEY", SecretStr("deepseek-secret"))
    secrets.set("MOONSHOT_API_KEY", SecretStr("kimi-secret"))

    first_ready = tmp_path / "first-ready"
    first_start = tmp_path / "first-start"
    first_load_entered = tmp_path / "first-load-entered"
    release_first = tmp_path / "release-first"
    first_result = tmp_path / "first-result"
    second_ready = tmp_path / "second-ready"
    second_start = tmp_path / "second-start"
    second_update_entered = tmp_path / "second-update-entered"
    second_result = tmp_path / "second-result"
    processes: list[subprocess.Popen[str]] = []
    completed: list[tuple[str, str]] = []

    try:
        first = _spawn_worker(
            kind=kind,
            path=config,
            action=f"first:{first_action}",
            ready=first_ready,
            start=first_start,
            marker=first_load_entered,
            coordination_marker=release_first,
            result=first_result,
        )
        processes.append(first)
        _wait_for((first_ready,))
        first_start.write_text("start", encoding="utf-8")
        _wait_for((first_load_entered,))

        second = _spawn_worker(
            kind=kind,
            path=config,
            action=f"second:{second_action}",
            ready=second_ready,
            start=second_start,
            marker=second_update_entered,
            coordination_marker=release_first,
            result=second_result,
        )
        processes.append(second)
        _wait_for((second_ready,))
        second_start.write_text("start", encoding="utf-8")

        deadline = time.monotonic() + 1.0
        while not second_update_entered.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not second_update_entered.exists(), (
            "A second process entered the config update while the first process "
            "was still reloading its committed snapshot."
        )

        release_first.write_text("release", encoding="utf-8")
        completed = [process.communicate(timeout=15.0) for process in processes]
    finally:
        release_first.write_text("release", encoding="utf-8")
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert [process.returncode for process in processes] == [0, 0], completed
    first_observed = json.loads(first_result.read_text(encoding="utf-8"))
    second_observed = json.loads(second_result.read_text(encoding="utf-8"))
    final_thread_model = conversation.read_thread(thread.id).thread.current_model or ""
    return first_observed, second_observed, final_thread_model


def test_provider_model_reload_and_thread_update_share_cross_process_transaction(
    tmp_path: Path,
) -> None:
    first_model = "deepseek/deepseek-v4-pro"
    second_model = "kimi/kimi-k2.6"
    first, second, final_thread_model = _run_provider_configuration_race(
        tmp_path,
        kind="provider_model",
        first_action=first_model,
        second_action=second_model,
    )

    assert first == {
        "status": "completed",
        "snapshot_model": first_model,
        "result_model": first_model,
        "thread_model": first_model,
    }
    assert second == {
        "status": "completed",
        "snapshot_model": second_model,
        "result_model": second_model,
        "thread_model": second_model,
    }
    document = read_user_config_document(tmp_path / "home" / "config.yaml")
    assert document.providers.default_model == second_model
    assert final_thread_model == second_model


def test_provider_source_update_and_reload_share_cross_process_transaction(
    tmp_path: Path,
) -> None:
    first, second, _ = _run_provider_configuration_race(
        tmp_path,
        kind="provider_source",
        first_action=CredentialSource.ENVIRONMENT.value,
        second_action=CredentialSource.AWESOME.value,
    )

    assert first == {
        "status": "completed",
        "snapshot_source": CredentialSource.ENVIRONMENT.value,
    }
    assert second == {
        "status": "completed",
        "snapshot_source": CredentialSource.AWESOME.value,
    }
    document = read_user_config_document(tmp_path / "home" / "config.yaml")
    assert document.credentials.deepseek is CredentialSource.AWESOME


@pytest.mark.parametrize(
    ("actions", "expected_title", "expected_thinking"),
    (
        (
            ("first:rename:Concurrent title", "last:model:kimi/kimi-k2.6"),
            "Concurrent title",
            True,
        ),
        (
            ("first:model:kimi/kimi-k2.6", "last:rename:Concurrent title"),
            "Concurrent title",
            True,
        ),
        (
            ("first:thinking:false", "last:model:kimi/kimi-k2.6"),
            "Original title",
            False,
        ),
        (
            ("first:model:kimi/kimi-k2.6", "last:thinking:false"),
            "Original title",
            False,
        ),
    ),
    ids=(
        "model-preserves-concurrent-rename",
        "rename-preserves-concurrent-model",
        "model-preserves-concurrent-thinking",
        "thinking-preserves-concurrent-model",
    ),
)
def test_thread_field_mutations_do_not_overwrite_concurrent_peer_fields(
    tmp_path: Path,
    actions: tuple[str, str],
    expected_title: str,
    expected_thinking: bool,
) -> None:
    home = tmp_path / "home"
    database = home / "application.db"
    conversation = ConversationService(store=SQLiteConversationRepositories(database))
    thread = conversation.create_thread(
        "workspace_1",
        "Original title",
        current_model="deepseek/deepseek-v4-flash",
    )
    (home / "thread-id").write_text(thread.id, encoding="utf-8")

    _run_workers(
        tmp_path,
        kind="thread_mutation",
        path=database,
        actions=actions,
    )

    final = conversation.read_thread(thread.id).thread
    assert final.current_model == "kimi/kimi-k2.6"
    assert final.title == expected_title
    assert final.thinking_enabled is expected_thinking


@pytest.mark.parametrize(
    ("phase", "expected_model", "expected_journal_phase"),
    (
        (
            "prepared",
            "deepseek/deepseek-v4-flash",
            ProviderModelTransactionPhase.PREPARED,
        ),
        (
            "config",
            "deepseek/deepseek-v4-flash",
            ProviderModelTransactionPhase.PREPARED,
        ),
        (
            "thread",
            "deepseek/deepseek-v4-flash",
            ProviderModelTransactionPhase.PREPARED,
        ),
        (
            "committed",
            "deepseek/deepseek-v4-pro",
            ProviderModelTransactionPhase.COMMITTED,
        ),
    ),
)
def test_provider_model_journal_recovers_after_process_kill(
    tmp_path: Path,
    phase: str,
    expected_model: str,
    expected_journal_phase: ProviderModelTransactionPhase,
) -> None:
    home = tmp_path / "home"
    config = home / "config.yaml"
    database = home / "application.db"
    writer = UserConfigWriter(config)
    writer.update(
        lambda current: current.model_copy(
            update={
                "providers": current.providers.model_copy(
                    update={"default_model": "deepseek/deepseek-v4-flash"}
                )
            }
        )
    )
    conversation = ConversationService(store=SQLiteConversationRepositories(database))
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    (home / "thread-id").write_text(thread.id, encoding="utf-8")
    ready = tmp_path / "crash-ready"
    start = tmp_path / "crash-start"
    phase_reached = tmp_path / "phase-reached"
    never_release = tmp_path / "never-release"
    crash_result = tmp_path / "crash-result"
    process = _spawn_worker(
        kind="provider_model_crash",
        path=config,
        action=phase,
        ready=ready,
        start=start,
        marker=phase_reached,
        coordination_marker=never_release,
        result=crash_result,
    )
    completed: tuple[str, str] | None = None
    try:
        _wait_for((ready,))
        start.write_text("start", encoding="utf-8")
        _wait_for((phase_reached,))
        process.kill()
        completed = process.communicate(timeout=15.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode != 0, completed
    assert not crash_result.exists()
    journal = ProviderModelTransactionJournal(
        AwesomePaths.from_home(home).provider_model_transaction_file
    )
    pending = journal.read()
    assert pending is not None
    assert pending.phase is expected_journal_phase

    reconcile_ready = tmp_path / "reconcile-ready"
    reconcile_start = tmp_path / "reconcile-start"
    reconcile_result = tmp_path / "reconcile-result"
    reconciler = _spawn_worker(
        kind="provider_model_reconcile",
        path=config,
        action="reconcile",
        ready=reconcile_ready,
        start=reconcile_start,
        marker=tmp_path / "unused-marker",
        coordination_marker=tmp_path / "unused-peer",
        result=reconcile_result,
    )
    try:
        _wait_for((reconcile_ready,))
        reconcile_start.write_text("start", encoding="utf-8")
        reconciler_output = reconciler.communicate(timeout=15.0)
    finally:
        if reconciler.poll() is None:
            reconciler.kill()
            reconciler.wait()

    assert reconciler.returncode == 0, reconciler_output
    observed = json.loads(reconcile_result.read_text(encoding="utf-8"))
    assert observed == {
        "status": "reconciled",
        "default_model": expected_model,
        "thread_model": expected_model,
    }
    assert journal.read() is None
    assert writer.read().providers.default_model == expected_model
    assert conversation.read_thread(thread.id).thread.current_model == expected_model


@pytest.mark.parametrize(
    ("phase", "expected_journal_phase", "committed"),
    (
        ("prepared", ProviderCredentialTransactionPhase.PREPARED, False),
        ("secret_write", ProviderCredentialTransactionPhase.PREPARED, False),
        (
            "secret_committed",
            ProviderCredentialTransactionPhase.SECRET_COMMITTED,
            False,
        ),
        ("config", ProviderCredentialTransactionPhase.SECRET_COMMITTED, False),
        ("committed", ProviderCredentialTransactionPhase.COMMITTED, True),
    ),
)
def test_provider_credential_journal_recovers_after_process_kill(
    tmp_path: Path,
    phase: str,
    expected_journal_phase: ProviderCredentialTransactionPhase,
    committed: bool,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    paths = AwesomePaths.from_home(home)
    previous_env = (
        b"# preserve this comment\n"
        b"DEEPSEEK_API_KEY=unrelated-secret\n"
        b"MEM0_API_KEY=old-secret\n"
    )
    paths.env_file.write_bytes(previous_env)
    target_env = (
        UserSecretStore(paths.env_file)
        .plan_set(
            "MEM0_API_KEY",
            SecretStr("new-secret"),
        )
        .content
    )
    writer = UserConfigWriter(paths.config_file)
    writer.update(
        lambda current: current.model_copy(
            update={
                "credentials": current.credentials.model_copy(
                    update={"mem0": CredentialSource.ENVIRONMENT}
                )
            }
        )
    )
    ready = tmp_path / "credential-crash-ready"
    start = tmp_path / "credential-crash-start"
    phase_reached = tmp_path / "credential-phase-reached"
    never_release = tmp_path / "credential-never-release"
    crash_result = tmp_path / "credential-crash-result"
    process = _spawn_worker(
        kind="provider_credential_crash",
        path=paths.config_file,
        action=phase,
        ready=ready,
        start=start,
        marker=phase_reached,
        coordination_marker=never_release,
        result=crash_result,
    )
    completed: tuple[str, str] | None = None
    try:
        _wait_for((ready,))
        start.write_text("start", encoding="utf-8")
        _wait_for((phase_reached,))
        process.kill()
        completed = process.communicate(timeout=15.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode != 0, completed
    assert not crash_result.exists()
    journal = ProviderCredentialTransactionJournal(
        paths.provider_credential_transaction_file,
        paths.provider_credential_backup_file,
    )
    pending = journal.read()
    assert pending is not None
    assert pending.phase is expected_journal_phase

    reconcile_ready = tmp_path / "credential-reconcile-ready"
    reconcile_start = tmp_path / "credential-reconcile-start"
    reconcile_result = tmp_path / "credential-reconcile-result"
    reconciler = _spawn_worker(
        kind="provider_credential_reconcile",
        path=paths.config_file,
        action="reconcile",
        ready=reconcile_ready,
        start=reconcile_start,
        marker=tmp_path / "credential-unused-marker",
        coordination_marker=tmp_path / "credential-unused-peer",
        result=reconcile_result,
    )
    try:
        _wait_for((reconcile_ready,))
        reconcile_start.write_text("start", encoding="utf-8")
        reconciler_output = reconciler.communicate(timeout=15.0)
    finally:
        if reconciler.poll() is None:
            reconciler.kill()
            reconciler.wait()

    assert reconciler.returncode == 0, reconciler_output
    observed = json.loads(reconcile_result.read_text(encoding="utf-8"))
    assert observed == {
        "status": "reconciled",
        "source": "awesome" if committed else "environment",
    }
    assert paths.env_file.read_bytes() == (target_env if committed else previous_env)
    assert writer.read().credentials.mem0 is (
        CredentialSource.AWESOME if committed else CredentialSource.ENVIRONMENT
    )
    journal.require_clean()
