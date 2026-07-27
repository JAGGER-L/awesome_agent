from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release.build_bundle import (
    BundleError,
    read_version,
    validate_installer_files,
    validate_license_files,
    validate_version_files,
)
from scripts.release.contract_versions import (
    ContractVersions,
    ContractVersionsError,
    check_generated_bindings,
    decode_json_object,
    exact_object,
    load_contract_versions,
    render_json_object,
)


class ReleaseIdentityError(RuntimeError):
    """A release version is stale, ambiguous, or bound to the wrong revision."""


class TagPolicy(StrEnum):
    ABSENT = "absent"
    CURRENT = "current"
    ABSENT_OR_CURRENT = "absent-or-current"


@dataclass(frozen=True, slots=True)
class ObservedTagState:
    exists: bool
    commit: str | None


_GITHUB_ACTIONS_APP_ID = 15368
_REQUIRED_RELEASE_CHECK_RUNS = ("Required", "Security required")
_MAX_CHECK_RUN_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_CHECK_RUN_PAGES = 10
_CHECK_RUN_PAGE_SIZE = 100
_GITHUB_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9_.-]{1,100}\Z"
)
_GIT_OBJECT_ID = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")
_MAX_FIXTURE_MANIFEST_BYTES = 128 * 1024
_MAX_FIXTURE_FILE_BYTES = 1024 * 1024


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        del request, file_pointer, code, message, headers, new_url
        return None


def _validate_repository_version_surfaces(
    root: Path,
    version: str,
    protocol_version: int,
) -> None:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        protocol_en = (root / "docs" / "reference" / "protocol.md").read_text(
            encoding="utf-8"
        )
        protocol_zh = (root / "docs" / "reference" / "protocol.zh-CN.md").read_text(
            encoding="utf-8"
        )
        catalog_docs = tuple(
            (root / relative).read_text(encoding="utf-8")
            for relative in (
                "docs/development/release.md",
                "docs/development/release.zh-CN.md",
                "docs/reference/README.md",
                "docs/reference/README.zh-CN.md",
            )
        )
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise ReleaseIdentityError(
            "release version surfaces are missing or invalid"
        ) from error

    if (
        project.get("project", {}).get("dynamic") != ["version"]
        or project.get("tool", {}).get("hatch", {}).get("version", {}).get("path")
        != "VERSION"
    ):
        raise ReleaseIdentityError(
            "Python version metadata is not sourced from VERSION"
        )
    expected_docs = (
        (
            protocol_en,
            f"# Private Core/TUI protocol v{protocol_version}",
            f"Protocol version **{protocol_version}**",
            f"product version is **{version}**",
            f'"protocol_version": {protocol_version}',
            f'"client_version": "{version}"',
            f"`protocol/fixtures/v{protocol_version}/`",
        ),
        (
            protocol_zh,
            f"# 私有 Core/TUI protocol v{protocol_version}",
            f"Protocol 版本 **{protocol_version}**",
            f"产品版本是 **{version}**",
            f'"protocol_version": {protocol_version}',
            f'"client_version": "{version}"',
            f"`protocol/fixtures/v{protocol_version}/`",
        ),
    )
    if any(
        any(expected not in document[0] for expected in document[1:])
        for document in expected_docs
    ):
        raise ReleaseIdentityError(
            "Protocol documentation identity does not match release compatibility"
        )
    if any("contract-versions.json" not in document for document in catalog_docs):
        raise ReleaseIdentityError("contract catalog documentation is incomplete")


def _validate_contract_catalog_documentation(
    root: Path,
    contracts: ContractVersions,
) -> None:
    try:
        reference_en = (root / "docs" / "reference" / "README.md").read_text(
            encoding="utf-8"
        )
        reference_zh = (
            root / "docs" / "reference" / "README.zh-CN.md"
        ).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseIdentityError(
            "contract catalog documentation is missing or invalid"
        ) from error

    def readable(versions: tuple[int, ...]) -> str:
        return "{" + ", ".join(str(version) for version in versions) + "}"

    zh_comma = "\N{FULLWIDTH COMMA}"
    zh_semicolon = "\N{FULLWIDTH SEMICOLON}"
    expected_documents = (
        (
            reference_en,
            f"private Core/TUI Protocol version `{contracts.protocol_version}`;",
            f"event envelope version `{contracts.event_envelope_version}`;",
            "Application diagnostic log record version "
            f"`{contracts.application_log_version}`;",
            "Application SQLite schema version "
            f"`{contracts.application_schema_current}`, with migration floor "
            f"`{contracts.application_schema_migration_floor}`;",
            f"user configuration version `{contracts.user_config_current}`, "
            "with readable versions "
            f"`{readable(contracts.user_config_readable_versions)}`;",
            f"workspace configuration version `{contracts.workspace_config_current}`, "
            "with readable versions "
            f"`{readable(contracts.workspace_config_readable_versions)}`;",
            f"UI preferences schema version `{contracts.ui_preferences_current}`, "
            "with readable versions "
            f"`{readable(contracts.ui_preferences_readable_versions)}`;",
            f"headless JSON result version `{contracts.headless_json_version}`;",
            f"Thread export version `{contracts.thread_export_version}`",
        ),
        (
            reference_zh,
            "私有 Core/TUI Protocol 版本 "
            f"`{contracts.protocol_version}`{zh_semicolon}",
            f"event envelope 版本 `{contracts.event_envelope_version}`{zh_semicolon}",
            "Application diagnostic log record 版本 "
            f"`{contracts.application_log_version}`{zh_semicolon}",
            "Application SQLite schema 版本 "
            f"`{contracts.application_schema_current}`{zh_comma}迁移下限为 "
            f"`{contracts.application_schema_migration_floor}`{zh_semicolon}",
            f"user 配置版本 `{contracts.user_config_current}`{zh_comma}"
            "可读取版本集合为 "
            f"`{readable(contracts.user_config_readable_versions)}`{zh_semicolon}",
            f"workspace 配置版本 `{contracts.workspace_config_current}`{zh_comma}"
            "可读取版本集合为 "
            f"`{readable(contracts.workspace_config_readable_versions)}`{zh_semicolon}",
            f"UI preferences schema 版本 `{contracts.ui_preferences_current}`"
            f"{zh_comma}"
            "可读取版本集合为 "
            f"`{readable(contracts.ui_preferences_readable_versions)}`{zh_semicolon}",
            f"headless JSON result 版本 `{contracts.headless_json_version}`"
            f"{zh_semicolon}",
            f"Thread export 版本 `{contracts.thread_export_version}`",
        ),
    )
    if any(
        any(expected not in document[0] for expected in document[1:])
        for document in expected_documents
    ):
        raise ReleaseIdentityError(
            "contract catalog documentation does not match the version catalog"
        )


def _read_bounded_fixture(path: Path, maximum_bytes: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
    except OSError as error:
        raise ContractVersionsError(f"{label} is unavailable") from error
    if len(content) > maximum_bytes:
        raise ContractVersionsError(f"{label} exceeds its size limit")
    return content


def _validate_protocol_fixtures(
    root: Path,
    product_version: str,
    contracts: ContractVersions,
) -> None:
    directory = (
        root
        / "protocol"
        / "fixtures"
        / f"v{contracts.protocol_version}"
    )
    manifest_path = directory / "manifest.json"
    try:
        manifest_content = _read_bounded_fixture(
            manifest_path,
            _MAX_FIXTURE_MANIFEST_BYTES,
            "Protocol fixture manifest",
        )
        manifest = exact_object(
            decode_json_object(
                manifest_content,
                maximum_bytes=_MAX_FIXTURE_MANIFEST_BYTES,
                label="Protocol fixture manifest",
            ),
            {
                "command_owners",
                "event_types",
                "files",
                "fixture_version",
                "methods",
                "product_version",
                "protocol_version",
            },
            "Protocol fixture manifest",
        )
        event_content = _read_bounded_fixture(
            directory / "events.valid.json",
            _MAX_FIXTURE_FILE_BYTES,
            "Protocol event fixture",
        )
        events = decode_json_object(
            event_content,
            maximum_bytes=_MAX_FIXTURE_FILE_BYTES,
            label="Protocol event fixture",
        ).get("events")
    except (OSError, ContractVersionsError) as error:
        raise ReleaseIdentityError("Protocol fixture manifest is invalid") from error
    if manifest_content != render_json_object(manifest):
        raise ReleaseIdentityError("Protocol fixture manifest is not canonical")
    if (
        manifest["product_version"] != product_version
        or manifest["protocol_version"] != contracts.protocol_version
    ):
        raise ReleaseIdentityError("Protocol fixture identity does not match catalog")
    if (
        not isinstance(events, list)
        or not events
        or any(
            not isinstance(event, dict)
            or event.get("version") != contracts.event_envelope_version
            for event in events
        )
    ):
        raise ReleaseIdentityError(
            "Protocol event envelopes do not match the contract catalog"
        )


def validate_tag_state(
    *,
    version: str,
    policy: TagPolicy,
    head_commit: str,
    tag_exists: bool,
    tag_commit: str | None,
) -> None:
    tag = f"v{version}"
    if not tag_exists:
        if tag_commit is not None:
            raise ReleaseIdentityError("release tag observation is inconsistent")
        if policy is TagPolicy.CURRENT:
            raise ReleaseIdentityError(f"release tag {tag} does not exist")
        return
    if policy is TagPolicy.ABSENT:
        raise ReleaseIdentityError(f"release tag {tag} already exists")
    if tag_commit is None:
        raise ReleaseIdentityError(f"release tag {tag} does not resolve to a commit")
    if policy is TagPolicy.CURRENT:
        if tag_commit != head_commit:
            raise ReleaseIdentityError(
                f"release tag {tag} points to a different commit"
            )
        return
    if tag_commit is not None and tag_commit != head_commit:
        raise ReleaseIdentityError(f"release tag {tag} points to a different commit")


def validate_required_check_runs(
    check_runs: Sequence[object],
    *,
    expected_sha: str,
) -> None:
    latest: dict[str, list[Mapping[str, object]]] = {
        name: [] for name in _REQUIRED_RELEASE_CHECK_RUNS
    }
    for candidate in check_runs:
        if not isinstance(candidate, Mapping):
            continue
        name = candidate.get("name")
        if name not in _REQUIRED_RELEASE_CHECK_RUNS:
            continue
        app = candidate.get("app")
        identifier = candidate.get("id")
        if (
            not isinstance(app, Mapping)
            or app.get("id") != _GITHUB_ACTIONS_APP_ID
            or type(identifier) is not int
            or identifier < 1
        ):
            continue
        latest[name].append(candidate)

    for name in _REQUIRED_RELEASE_CHECK_RUNS:
        candidates = latest[name]
        if len(candidates) != 1:
            raise ReleaseIdentityError(
                "Required and Security required latest check-runs are not unique"
            )
        check_run = candidates[0]
        head_sha = check_run.get("head_sha")
        if (
            not isinstance(head_sha, str)
            or head_sha.casefold() != expected_sha.casefold()
        ):
            raise ReleaseIdentityError(
                "required check-run does not belong to the exact release revision"
            )
        if (
            check_run.get("status") != "completed"
            or check_run.get("conclusion") != "success"
        ):
            raise ReleaseIdentityError(
                "Required and Security required check-runs are not both successful"
            )


def _git_output(root: Path, *arguments: str, missing_ok: bool = False) -> str | None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if missing_ok and result.returncode == 1:
        return None
    raise ReleaseIdentityError("release identity requires a complete Git checkout")


def observe_tag_state(root: Path, tag: str) -> ObservedTagState:
    ref = f"refs/tags/{tag}"
    existing = _git_output(
        root,
        "show-ref",
        "--verify",
        "--quiet",
        ref,
        missing_ok=True,
    )
    if existing is None:
        return ObservedTagState(exists=False, commit=None)
    commit = _git_output(
        root,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{ref}^{{commit}}",
        missing_ok=True,
    )
    return ObservedTagState(exists=True, commit=commit)


def _check_run_page(
    *,
    repository: str,
    expected_sha: str,
    token: str,
    check_name: str,
    page: int,
) -> tuple[object, ...]:
    query = urllib.parse.urlencode(
        {
            "check_name": check_name,
            "filter": "latest",
            "per_page": _CHECK_RUN_PAGE_SIZE,
            "page": page,
        }
    )
    url = (
        f"https://api.github.com/repos/{repository}/commits/"
        f"{expected_sha}/check-runs?{query}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "awesome-release-identity",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        opener = urllib.request.build_opener(_RejectRedirects())
        with opener.open(request, timeout=20.0) as response:
            content = response.read(_MAX_CHECK_RUN_RESPONSE_BYTES + 1)
        if len(content) > _MAX_CHECK_RUN_RESPONSE_BYTES:
            raise ReleaseIdentityError("GitHub check-run response is too large")
        document: Any = json.loads(content)
    except ReleaseIdentityError:
        raise
    except (
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        urllib.error.HTTPError,
        urllib.error.URLError,
    ) as error:
        raise ReleaseIdentityError("GitHub check-run verification failed") from error
    if not isinstance(document, dict):
        raise ReleaseIdentityError("GitHub check-run response is invalid")
    check_runs = document.get("check_runs")
    if not isinstance(check_runs, list):
        raise ReleaseIdentityError("GitHub check-run response is invalid")
    return tuple(check_runs)


def fetch_required_check_runs(
    *,
    repository: str,
    expected_sha: str,
    token: str,
) -> tuple[object, ...]:
    if (
        _GITHUB_REPOSITORY.fullmatch(repository) is None
        or _GIT_OBJECT_ID.fullmatch(expected_sha) is None
        or not token.strip()
    ):
        raise ReleaseIdentityError("GitHub check-run identity is invalid")

    observed: list[object] = []
    for check_name in _REQUIRED_RELEASE_CHECK_RUNS:
        for page in range(1, _MAX_CHECK_RUN_PAGES + 1):
            check_runs = _check_run_page(
                repository=repository,
                expected_sha=expected_sha,
                token=token,
                check_name=check_name,
                page=page,
            )
            observed.extend(check_runs)
            if len(check_runs) < _CHECK_RUN_PAGE_SIZE:
                break
        else:
            raise ReleaseIdentityError("GitHub check-run response is too large")
    return tuple(observed)


def check_release_identity(root: Path, policy: TagPolicy) -> str:
    root = root.resolve()
    try:
        version = read_version(root)
        validate_version_files(root, version)
        validate_installer_files(root, version)
        validate_license_files(root)
        contracts = load_contract_versions(root)
        check_generated_bindings(root, contracts)
    except (BundleError, ContractVersionsError) as error:
        raise ReleaseIdentityError(str(error)) from error
    _validate_protocol_fixtures(root, version, contracts)
    _validate_repository_version_surfaces(
        root,
        version,
        contracts.protocol_version,
    )
    _validate_contract_catalog_documentation(root, contracts)

    head_commit = _git_output(root, "rev-parse", "--verify", "HEAD")
    assert head_commit is not None
    tag_state = observe_tag_state(root, f"v{version}")
    validate_tag_state(
        version=version,
        policy=policy,
        head_commit=head_commit,
        tag_exists=tag_state.exists,
        tag_commit=tag_state.commit,
    )
    return version


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate release version, license, and local Git tag identity."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--tag-policy",
        choices=[policy.value for policy in TagPolicy],
        required=True,
    )
    parser.add_argument(
        "--require-check-runs",
        action="store_true",
        help="Require successful Required and Security required runs for GitHub SHA.",
    )
    parser.add_argument("--github-repository")
    parser.add_argument("--github-sha")
    arguments = parser.parse_args(argv)
    try:
        version = check_release_identity(
            arguments.root,
            TagPolicy(arguments.tag_policy),
        )
        if arguments.require_check_runs:
            repository = arguments.github_repository
            expected_sha = arguments.github_sha
            token = os.environ.get("GITHUB_TOKEN")
            if (
                not isinstance(repository, str)
                or not isinstance(expected_sha, str)
                or token is None
            ):
                raise ReleaseIdentityError("GitHub check-run identity is unavailable")
            head_commit = _git_output(
                arguments.root.resolve(), "rev-parse", "--verify", "HEAD"
            )
            assert head_commit is not None
            if head_commit.casefold() != expected_sha.casefold():
                raise ReleaseIdentityError(
                    "GITHUB_SHA is not the exact checked-out release revision"
                )
            check_runs = fetch_required_check_runs(
                repository=repository,
                expected_sha=expected_sha,
                token=token,
            )
            validate_required_check_runs(check_runs, expected_sha=expected_sha)
    except ReleaseIdentityError as error:
        parser.exit(1, f"release identity failed: {error}\n")
    print(f"release identity valid for {version} ({arguments.tag_policy})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
