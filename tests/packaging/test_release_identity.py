from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release import check_identity as identity_checker
from scripts.release.build_bundle import (
    BundleError,
    read_version,
    validate_license_files,
)
from scripts.release.check_identity import (
    ReleaseIdentityError,
    TagPolicy,
    observe_tag_state,
    validate_required_check_runs,
    validate_tag_state,
)
from scripts.release.contract_versions import (
    ContractVersionsError,
    check_generated_bindings,
    load_contract_versions,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_license_fixture(root: Path) -> None:
    license_content = (ROOT / "LICENSE").read_bytes()
    (root / "LICENSE").write_bytes(license_content)
    tui = root / "tui"
    tui.mkdir()
    (tui / "LICENSE").write_bytes(license_content)
    package = {"license": "MIT"}
    lock = {"packages": {"": {"license": "MIT"}}}
    (tui / "package.json").write_text(f"{json.dumps(package)}\n", encoding="utf-8")
    (tui / "package-lock.json").write_text(f"{json.dumps(lock)}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nlicense = "MIT"\nlicense-files = ["LICENSE"]\n',
        encoding="utf-8",
    )


def test_repository_license_surfaces_are_one_mit_contract() -> None:
    assert validate_license_files(ROOT) == (ROOT / "LICENSE").read_bytes()


def test_repository_release_identity_matches_contract_catalog() -> None:
    version = read_version(ROOT)
    contracts = load_contract_versions(ROOT)

    check_generated_bindings(ROOT, contracts)
    identity_checker._validate_protocol_fixtures(ROOT, version, contracts)
    identity_checker._validate_repository_version_surfaces(
        ROOT,
        version,
        contracts.protocol_version,
    )
    identity_checker._validate_contract_catalog_documentation(ROOT, contracts)


def test_protocol_fixture_gate_rejects_event_catalog_drift() -> None:
    version = read_version(ROOT)
    contracts = load_contract_versions(ROOT)

    with pytest.raises(ReleaseIdentityError, match="event envelopes"):
        identity_checker._validate_protocol_fixtures(
            ROOT,
            version,
            replace(contracts, event_envelope_version=2),
        )


def test_release_identity_uses_catalog_protocol_without_a_hardcoded_constant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(identity_checker, "read_version", lambda _: "1.3.0")
    monkeypatch.setattr(identity_checker, "validate_version_files", lambda *_: None)
    monkeypatch.setattr(identity_checker, "validate_installer_files", lambda *_: {})
    monkeypatch.setattr(identity_checker, "validate_license_files", lambda *_: b"")
    monkeypatch.setattr(
        identity_checker,
        "load_contract_versions",
        lambda *_: SimpleNamespace(protocol_version=7),
    )
    monkeypatch.setattr(identity_checker, "check_generated_bindings", lambda *_: None)
    monkeypatch.setattr(
        identity_checker,
        "_validate_protocol_fixtures",
        lambda *_: None,
    )
    monkeypatch.setattr(
        identity_checker,
        "_validate_repository_version_surfaces",
        lambda root, version, protocol: observed.append((root, version, protocol)),
    )
    monkeypatch.setattr(
        identity_checker,
        "_validate_contract_catalog_documentation",
        lambda *_: None,
    )
    monkeypatch.setattr(
        identity_checker,
        "_git_output",
        lambda *_args, **_kwargs: "a" * 40,
    )
    monkeypatch.setattr(
        identity_checker,
        "observe_tag_state",
        lambda *_: identity_checker.ObservedTagState(exists=False, commit=None),
    )

    assert (
        identity_checker.check_release_identity(tmp_path, TagPolicy.ABSENT) == "1.3.0"
    )
    assert observed == [(tmp_path.resolve(), "1.3.0", 7)]


def test_contract_catalog_documentation_rejects_non_protocol_drift() -> None:
    contracts = load_contract_versions(ROOT)

    with pytest.raises(ReleaseIdentityError, match="version catalog"):
        identity_checker._validate_contract_catalog_documentation(
            ROOT,
            replace(contracts, application_log_version=2),
        )


def test_protocol_fixture_reader_bounds_the_file_read(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(b"x" * 9)

    with pytest.raises(ContractVersionsError, match="size limit"):
        identity_checker._read_bounded_fixture(fixture, 8, "test fixture")


def test_protocol_document_identity_uses_dynamic_compatibility_version(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndynamic = ["version"]\n[tool.hatch.version]\npath = "VERSION"\n',
        encoding="utf-8",
    )
    reference = tmp_path / "docs" / "reference"
    reference.mkdir(parents=True)
    protocol_en = (
        "# Private Core/TUI protocol v7\n"
        "Protocol version **7**; product version is **1.3.0**.\n"
        '```json\n{"protocol_version": 7, "client_version": "1.3.0"}\n```\n'
        "`protocol/fixtures/v7/`\n"
    )
    protocol_zh = (
        "# 私有 Core/TUI protocol v7\n"
        "Protocol 版本 **7**。产品版本是 **1.3.0**。\n"
        '```json\n{"protocol_version": 7, "client_version": "1.3.0"}\n```\n'
        "`protocol/fixtures/v7/`\n"
    )
    (reference / "protocol.md").write_text(protocol_en, encoding="utf-8")
    (reference / "protocol.zh-CN.md").write_text(protocol_zh, encoding="utf-8")
    development = tmp_path / "docs" / "development"
    development.mkdir()
    for relative in (
        development / "release.md",
        development / "release.zh-CN.md",
        reference / "README.md",
        reference / "README.zh-CN.md",
    ):
        relative.write_text("`contract-versions.json`\n", encoding="utf-8")

    identity_checker._validate_repository_version_surfaces(
        tmp_path,
        "1.3.0",
        7,
    )

    (reference / "protocol.md").write_text(
        protocol_en.replace('"protocol_version": 7', '"protocol_version": 4'),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseIdentityError, match="documentation identity"):
        identity_checker._validate_repository_version_surfaces(
            tmp_path,
            "1.3.0",
            7,
        )


@pytest.mark.parametrize(
    ("mutation", "diagnostic"),
    [
        ("tui-text", "TUI license"),
        ("python-metadata", "Python license"),
        ("tui-metadata", "TUI package license"),
        ("lock-metadata", "TUI lock license"),
    ],
)
def test_license_gate_rejects_every_drift(
    tmp_path: Path,
    mutation: str,
    diagnostic: str,
) -> None:
    _write_license_fixture(tmp_path)
    if mutation == "tui-text":
        (tmp_path / "tui" / "LICENSE").write_text("different\n", encoding="utf-8")
    elif mutation == "python-metadata":
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nlicense = "Apache-2.0"\nlicense-files = ["LICENSE"]\n',
            encoding="utf-8",
        )
    elif mutation == "tui-metadata":
        (tmp_path / "tui" / "package.json").write_text(
            '{"license":"UNLICENSED"}\n', encoding="utf-8"
        )
    else:
        (tmp_path / "tui" / "package-lock.json").write_text(
            '{"packages":{"":{"license":"UNLICENSED"}}}\n', encoding="utf-8"
        )

    with pytest.raises(BundleError, match=diagnostic):
        validate_license_files(tmp_path)


def test_license_gate_rejects_additional_terms_in_matching_license_files(
    tmp_path: Path,
) -> None:
    _write_license_fixture(tmp_path)
    restricted = (ROOT / "LICENSE").read_bytes() + b"\nCommercial use is forbidden.\n"
    (tmp_path / "LICENSE").write_bytes(restricted)
    (tmp_path / "tui" / "LICENSE").write_bytes(restricted)

    with pytest.raises(BundleError, match="canonical MIT grant"):
        validate_license_files(tmp_path)


def test_license_gate_allows_a_different_single_copyright_line(tmp_path: Path) -> None:
    _write_license_fixture(tmp_path)
    original = (ROOT / "LICENSE").read_text(encoding="utf-8")
    updated = original.replace(
        "Copyright (c) 2026 Awesome Agent contributors",
        "Copyright (c) 2026 Example contributors",
    ).encode()
    assert updated != original.encode()
    (tmp_path / "LICENSE").write_bytes(updated)
    (tmp_path / "tui" / "LICENSE").write_bytes(updated)

    assert validate_license_files(tmp_path) == updated


def test_tag_policies_distinguish_candidate_tag_and_published_revision() -> None:
    validate_tag_state(
        version="1.3.0",
        policy=TagPolicy.ABSENT,
        head_commit="candidate",
        tag_exists=False,
        tag_commit=None,
    )
    validate_tag_state(
        version="1.3.0",
        policy=TagPolicy.CURRENT,
        head_commit="published",
        tag_exists=True,
        tag_commit="published",
    )
    validate_tag_state(
        version="1.3.0",
        policy=TagPolicy.ABSENT_OR_CURRENT,
        head_commit="published",
        tag_exists=True,
        tag_commit="published",
    )

    with pytest.raises(ReleaseIdentityError, match="already exists"):
        validate_tag_state(
            version="1.3.0",
            policy=TagPolicy.ABSENT,
            head_commit="candidate",
            tag_exists=True,
            tag_commit="published",
        )
    with pytest.raises(ReleaseIdentityError, match="does not exist"):
        validate_tag_state(
            version="1.3.0",
            policy=TagPolicy.CURRENT,
            head_commit="candidate",
            tag_exists=False,
            tag_commit=None,
        )
    with pytest.raises(ReleaseIdentityError, match="different commit"):
        validate_tag_state(
            version="1.3.0",
            policy=TagPolicy.ABSENT_OR_CURRENT,
            head_commit="candidate",
            tag_exists=True,
            tag_commit="published",
        )


@pytest.mark.parametrize(
    ("policy", "diagnostic"),
    [
        (TagPolicy.ABSENT, "already exists"),
        (TagPolicy.CURRENT, "does not resolve to a commit"),
        (TagPolicy.ABSENT_OR_CURRENT, "does not resolve to a commit"),
    ],
)
def test_existing_non_commit_tag_fails_closed_for_every_policy(
    tmp_path: Path,
    policy: TagPolicy,
    diagnostic: str,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=tmp_path,
        input="not a commit\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/tags/v1.3.0", blob],
        cwd=tmp_path,
        check=True,
    )

    observed = observe_tag_state(tmp_path, "v1.3.0")

    assert observed.exists is True
    assert observed.commit is None
    with pytest.raises(ReleaseIdentityError, match=diagnostic):
        validate_tag_state(
            version="1.3.0",
            policy=policy,
            head_commit="candidate",
            tag_exists=observed.exists,
            tag_commit=observed.commit,
        )


def test_missing_tag_is_observed_as_absent(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)

    assert observe_tag_state(tmp_path, "v1.3.0") == identity_checker.ObservedTagState(
        exists=False,
        commit=None,
    )


def test_required_check_runs_must_be_latest_successes_for_exact_sha() -> None:
    exact_sha = "a" * 40
    check_runs = [
        {
            "id": 10,
            "name": "Required",
            "head_sha": exact_sha,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 15368},
        },
        {
            "id": 11,
            "name": "Security required",
            "head_sha": exact_sha,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 15368},
        },
    ]

    validate_required_check_runs(check_runs, expected_sha=exact_sha)

    check_runs[0] = {**check_runs[0], "head_sha": "b" * 40}
    with pytest.raises(ReleaseIdentityError, match="exact release revision"):
        validate_required_check_runs(check_runs, expected_sha=exact_sha)


def test_required_check_runs_reject_missing_pending_failed_or_superseded_runs() -> None:
    exact_sha = "a" * 40
    successful = {
        "id": 10,
        "name": "Required",
        "head_sha": exact_sha,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": 15368},
    }
    security = {
        "id": 11,
        "name": "Security required",
        "head_sha": exact_sha,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": 15368},
    }

    invalid_corpora = (
        [successful],
        [successful, {**security, "status": "in_progress", "conclusion": None}],
        [successful, {**security, "conclusion": "failure"}],
        [
            successful,
            security,
            {
                **security,
                "id": 12,
                "status": "completed",
                "conclusion": "failure",
            },
        ],
        [successful, {**security, "app": {"id": 1}}],
        [successful, security, {**security, "id": 12}],
    )
    for check_runs in invalid_corpora:
        with pytest.raises(ReleaseIdentityError, match="Required and Security"):
            validate_required_check_runs(check_runs, expected_sha=exact_sha)


def test_check_run_request_is_exact_bounded_and_never_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_sha = "a" * 40
    check_run = {
        "id": 10,
        "name": "Security required",
        "head_sha": exact_sha,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": 15368},
    }
    payload = json.dumps({"check_runs": [check_run]}).encode()
    observed: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self, size: int) -> bytes:
            observed["read_size"] = size
            return payload

    class FakeOpener:
        def open(
            self,
            request: urllib.request.Request,
            *,
            timeout: float,
        ) -> FakeResponse:
            observed["request"] = request
            observed["timeout"] = timeout
            return FakeResponse()

    def build_opener(*handlers: object) -> FakeOpener:
        observed["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    result = identity_checker._check_run_page(
        repository="owner/repository",
        expected_sha=exact_sha,
        token="release-token",
        check_name="Security required",
        page=2,
    )

    assert result == (check_run,)
    request = observed["request"]
    assert isinstance(request, urllib.request.Request)
    parsed = urllib.parse.urlparse(request.full_url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "api.github.com",
        f"/repos/owner/repository/commits/{exact_sha}/check-runs",
    )
    assert urllib.parse.parse_qs(parsed.query) == {
        "check_name": ["Security required"],
        "filter": ["latest"],
        "per_page": ["100"],
        "page": ["2"],
    }
    assert "release-token" not in request.full_url
    assert request.get_header("Authorization") == "Bearer release-token"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("User-agent") == "awesome-release-identity"
    assert request.get_header("X-github-api-version") == "2022-11-28"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("User-agent") == "awesome-release-identity"
    assert request.get_header("X-github-api-version") == "2022-11-28"
    assert observed["timeout"] == 20.0
    assert observed["read_size"] == 2 * 1024 * 1024 + 1
    handlers = observed["handlers"]
    assert isinstance(handlers, tuple)
    assert len(handlers) == 1
    redirect_handler = handlers[0]
    assert isinstance(redirect_handler, identity_checker._RejectRedirects)
    assert (
        redirect_handler.redirect_request(
            request,
            None,
            302,
            "redirect",
            {},
            "https://example.invalid/steal",
        )
        is None
    )


@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [
        (b"x" * (2 * 1024 * 1024 + 1), "response is too large"),
        (b"{", "verification failed"),
        (b"[]", "response is invalid"),
        (b'{"check_runs":{}}', "response is invalid"),
    ],
    ids=("oversized", "invalid-json", "invalid-document", "invalid-runs"),
)
def test_check_run_request_rejects_invalid_responses(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    diagnostic: str,
) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self, size: int) -> bytes:
            del size
            return payload

    class FakeOpener:
        def open(self, *args: object, **kwargs: object) -> FakeResponse:
            del args, kwargs
            return FakeResponse()

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: FakeOpener(),
    )

    with pytest.raises(ReleaseIdentityError, match=diagnostic):
        identity_checker._check_run_page(
            repository="owner/repository",
            expected_sha="a" * 40,
            token="release-token",
            check_name="Required",
            page=1,
        )


def test_check_run_request_accepts_exact_response_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    padding = 2 * 1024 * 1024 - len(b'{"check_runs":[],"padding":""}')
    payload = b'{"check_runs":[],"padding":"' + (b"x" * padding) + b'"}'
    assert len(payload) == 2 * 1024 * 1024

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self, size: int) -> bytes:
            assert size == len(payload) + 1
            return payload

    class FakeOpener:
        def open(self, *args: object, **kwargs: object) -> FakeResponse:
            del args, kwargs
            return FakeResponse()

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: FakeOpener(),
    )

    assert (
        identity_checker._check_run_page(
            repository="owner/repository",
            expected_sha="a" * 40,
            token="release-token",
            check_name="Required",
            page=1,
        )
        == ()
    )


def test_check_run_request_redacts_http_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOpener:
        def open(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise urllib.error.HTTPError(
                "https://api.github.com/redacted",
                403,
                "forbidden",
                Message(),
                None,
            )

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: FakeOpener(),
    )

    with pytest.raises(
        ReleaseIdentityError,
        match=r"^GitHub check-run verification failed$",
    ):
        identity_checker._check_run_page(
            repository="owner/repository",
            expected_sha="a" * 40,
            token="release-token",
            check_name="Required",
            page=1,
        )


def test_check_run_fetch_is_paginated_and_resource_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def page(**arguments: object) -> tuple[object, ...]:
        check_name = arguments["check_name"]
        page_number = arguments["page"]
        assert isinstance(check_name, str)
        assert isinstance(page_number, int)
        calls.append((check_name, page_number))
        count = 100 if page_number == 1 else 1
        return tuple({"page": page_number} for _ in range(count))

    monkeypatch.setattr(identity_checker, "_check_run_page", page)

    observed = identity_checker.fetch_required_check_runs(
        repository="owner/repository",
        expected_sha="a" * 40,
        token="release-token",
    )

    assert len(observed) == 202
    assert calls == [
        ("Required", 1),
        ("Required", 2),
        ("Security required", 1),
        ("Security required", 2),
    ]

    monkeypatch.setattr(
        identity_checker,
        "_check_run_page",
        lambda **arguments: tuple(object() for _ in range(100)),
    )
    with pytest.raises(ReleaseIdentityError, match="response is too large"):
        identity_checker.fetch_required_check_runs(
            repository="owner/repository",
            expected_sha="a" * 40,
            token="release-token",
        )


@pytest.mark.parametrize(
    ("repository", "sha", "token"),
    [
        ("invalid", "a" * 40, "token"),
        ("owner/repository", "not-a-sha", "token"),
        ("owner/repository", "a" * 40, " "),
    ],
)
def test_check_run_fetch_rejects_invalid_identity_before_network(
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    sha: str,
    token: str,
) -> None:
    monkeypatch.setattr(
        identity_checker,
        "_check_run_page",
        lambda **arguments: pytest.fail(f"unexpected network call: {arguments}"),
    )

    with pytest.raises(ReleaseIdentityError, match="identity is invalid"):
        identity_checker.fetch_required_check_runs(
            repository=repository,
            expected_sha=sha,
            token=token,
        )


def test_cli_rejects_github_sha_that_is_not_checked_out_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "release-token")
    monkeypatch.setattr(
        identity_checker,
        "check_release_identity",
        lambda root, policy: "1.3.0",
    )
    monkeypatch.setattr(
        identity_checker,
        "_git_output",
        lambda *args, **kwargs: "a" * 40,
    )
    monkeypatch.setattr(
        identity_checker,
        "fetch_required_check_runs",
        lambda **arguments: pytest.fail(f"unexpected network call: {arguments}"),
    )

    with pytest.raises(SystemExit) as error:
        identity_checker.main(
            [
                "--tag-policy",
                "absent",
                "--require-check-runs",
                "--github-repository",
                "owner/repository",
                "--github-sha",
                "b" * 40,
            ]
        )

    assert error.value.code == 1
