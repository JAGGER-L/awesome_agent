from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
PRIMARY_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "security.yml",
    ROOT / ".github" / "workflows" / "nightly.yml",
    ROOT / ".github" / "workflows" / "release-gate.yml",
    ROOT / ".github" / "workflows" / "docs-pages.yml",
)


def _tracked_workflows() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", ".github/workflows/*.yml", ".github/workflows/*.yaml"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(ROOT / line for line in completed.stdout.splitlines() if line)


WORKFLOWS = tuple(dict.fromkeys((*PRIMARY_WORKFLOWS, *_tracked_workflows())))
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
UPLOAD_ARTIFACT_V7 = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT_V8 = (
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
)
ATTEST_V4 = "actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"
WORKFLOW_PERMISSION_OVERRIDES = {
    "release-gate.yml": {"checks": "read", "contents": "read"},
}
JOB_PERMISSION_OVERRIDES = {
    ("security.yml", "dependency-review"): {"contents": "read"},
    ("security.yml", "codeql"): {
        "contents": "read",
        "security-events": "write",
    },
    ("release-gate.yml", "attest-release"): {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    },
    ("docs-pages.yml", "build"): {
        "contents": "read",
        "pages": "read",
    },
    ("docs-pages.yml", "deploy"): {
        "pages": "write",
        "id-token": "write",
    },
}


def _workflow(path: Path) -> dict[str, object]:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return document


def test_required_ci_covers_every_product_boundary() -> None:
    workflow = _workflow(PRIMARY_WORKFLOWS[0])
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert isinstance(triggers, dict)
    assert {"pull_request", "push", "merge_group", "workflow_dispatch"} <= set(triggers)
    assert isinstance(jobs, dict)
    assert {
        "python-quality",
        "python-tests",
        "windows-contracts",
        "python-contracts",
        "tui",
        "docs-site",
        "required",
    } == set(jobs)

    required = jobs["required"]
    assert isinstance(required, dict)
    assert required["name"] == "Required"
    assert set(required["needs"]) == set(jobs) - {"required"}
    assert required["if"] == "always()"

    quality = jobs["python-quality"]
    assert isinstance(quality, dict)
    steps = quality["steps"]
    assert isinstance(steps, list)
    quality_by_name = {
        step.get("name"): step for step in steps if isinstance(step, dict)
    }
    assert quality_by_name["Checkout"]["with"] == {
        "fetch-depth": "0",
        "persist-credentials": "false",
    }
    assert quality_by_name["Check release identity"]["run"] == (
        "uv run python scripts/release/check_identity.py --tag-policy absent-or-current"
    )
    assert any(
        isinstance(step, dict) and step.get("name") == "Lint GitHub Actions workflows"
        for step in steps
    )

    windows = jobs["windows-contracts"]
    assert isinstance(windows, dict)
    windows_steps = windows["steps"]
    assert isinstance(windows_steps, list)
    windows_by_name = {
        step.get("name"): step for step in windows_steps if isinstance(step, dict)
    }
    assert windows_by_name["Run Windows installer guard contracts"] == {
        "name": "Run Windows installer guard contracts",
        "shell": "pwsh",
        "run": "./tests/packaging/install_windows_contract.ps1",
    }
    windows_test = next(
        step
        for step in windows_steps
        if isinstance(step, dict)
        and step.get("name") == "Run Windows-sensitive contracts"
    )
    run = windows_test["run"]
    assert isinstance(run, str)
    assert "tests/unit/core" in run
    assert "tests/unit/protocol" in run
    assert "tests/unit/config/test_resource_lock.py" in run
    assert "tests/unit/config/test_user_state_concurrency.py" in run
    assert "tests/unit/memory/test_tools.py" in run
    assert "tests/integration/test_local_memory.py" in run
    assert "tests/integration/test_read_tools.py" in run
    assert "tests/unit/context/test_workspace_instructions.py" in run
    assert "tests/unit/storage/test_state_recovery.py" in run
    assert "tests/integration/test_change_journal.py" in run
    assert "tests/packaging/test_install_contract.py" in run

    contracts = jobs["python-contracts"]
    assert isinstance(contracts, dict)
    contract_steps = contracts["steps"]
    assert isinstance(contract_steps, list)
    contract_by_name = {
        step.get("name"): step for step in contract_steps if isinstance(step, dict)
    }
    assert contract_by_name["Run POSIX installer guard contracts"] == {
        "name": "Run POSIX installer guard contracts",
        "shell": "bash",
        "run": "sh tests/packaging/install_shell_contract.sh",
    }

    tui = jobs["tui"]
    assert isinstance(tui, dict)
    strategy = tui["strategy"]
    assert isinstance(strategy, dict)
    matrix = strategy["matrix"]
    assert isinstance(matrix, dict)
    included = matrix["include"]
    assert isinstance(included, list)
    assert any(
        isinstance(entry, dict) and entry.get("node") == "22.23.1" for entry in included
    )
    assert any(
        isinstance(entry, dict) and entry.get("os") == "windows-latest"
        for entry in included
    )
    assert any(
        isinstance(entry, dict)
        and entry.get("os") == "macos-latest"
        and entry.get("node") == "22.23.1"
        for entry in included
    )

    docs_site = jobs["docs-site"]
    assert isinstance(docs_site, dict)
    docs_steps = docs_site["steps"]
    assert isinstance(docs_steps, list)
    docs_by_name = {
        step.get("name"): step for step in docs_steps if isinstance(step, dict)
    }
    assert docs_by_name["Install locked dependencies"]["run"] == "npm ci"
    assert docs_by_name["Check site"]["run"] == "npm run check"
    assert docs_by_name["Build site"]["run"] == "npm run build"
    assert docs_by_name["Check built links"]["run"] == "npm run check:links"


def test_tui_job_provisions_locked_python_core_before_e2e_tests() -> None:
    workflow = _workflow(PRIMARY_WORKFLOWS[0])
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    tui = jobs["tui"]
    assert isinstance(tui, dict)
    steps = tui["steps"]
    assert isinstance(steps, list)
    named_steps = {step.get("name"): step for step in steps if isinstance(step, dict)}
    required = {
        "Setup Python",
        "Setup uv",
        "Install locked Core test dependencies",
        "Test",
    }
    assert required <= set(named_steps)

    python_setup = named_steps["Setup Python"]
    uv_setup = named_steps["Setup uv"]
    core_install = named_steps["Install locked Core test dependencies"]
    assert python_setup["with"] == {"python-version": "${{ env.PYTHON_VERSION }}"}
    assert uv_setup["with"] == {
        "version": "${{ env.UV_VERSION }}",
        "enable-cache": "true",
    }
    assert core_install["run"] == "uv sync --locked --dev"
    assert "working-directory" not in core_install

    names = [step.get("name") for step in steps if isinstance(step, dict)]
    assert names.index("Setup Python") < names.index("Setup uv")
    assert names.index("Setup uv") < names.index(
        "Install locked Core test dependencies"
    )
    assert names.index("Install locked Core test dependencies") < names.index("Test")


def test_tui_package_installation_runs_once_outside_the_parallel_suite() -> None:
    workflows = (
        (PRIMARY_WORKFLOWS[0], "tui", "Build"),
        (PRIMARY_WORKFLOWS[2], "full-suite", "Build TUI"),
    )
    parallel_command = "npm test -- --exclude tests/packaging/package.test.ts"
    package_command = "npm test -- tests/packaging/package.test.ts"

    for path, job_name, build_name in workflows:
        workflow = _workflow(path)
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        job = jobs[job_name]
        assert isinstance(job, dict)
        steps = job["steps"]
        assert isinstance(steps, list)
        named_steps = {
            step.get("name"): step for step in steps if isinstance(step, dict)
        }
        commands = [
            step.get("run")
            for step in steps
            if isinstance(step, dict) and "run" in step
        ]
        assert commands.count(parallel_command) == 1
        assert commands.count(package_command) == 1
        assert named_steps["Test"]["run"] == parallel_command
        assert named_steps["Test installed TUI package"]["run"] == package_command

        names = [step.get("name") for step in steps if isinstance(step, dict)]
        assert names.index("Test") < names.index("Test installed TUI package")
        assert names.index("Test installed TUI package") < names.index(build_name)


def test_workflows_use_bounded_jobs_and_immutable_action_references() -> None:
    for path in WORKFLOWS:
        workflow = _workflow(path)
        assert workflow["permissions"] == WORKFLOW_PERMISSION_OVERRIDES.get(
            path.name, {"contents": "read"}
        )
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        for job in jobs.values():
            assert isinstance(job, dict)
            assert "timeout-minutes" in job
            steps = job.get("steps", ())
            assert isinstance(steps, list)
            for step in steps:
                assert isinstance(step, dict)
                action = step.get("uses")
                if action is not None:
                    assert isinstance(action, str)
                    assert PINNED_ACTION.fullmatch(action) is not None


def test_primary_workflows_use_exact_job_permissions_and_safe_checkout() -> None:
    for path in PRIMARY_WORKFLOWS:
        workflow = _workflow(path)
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        for job_name, job in jobs.items():
            assert isinstance(job_name, str)
            assert isinstance(job, dict)
            expected_permissions = JOB_PERMISSION_OVERRIDES.get((path.name, job_name))
            if expected_permissions is None:
                assert "permissions" not in job
            else:
                assert job["permissions"] == expected_permissions

            steps = job.get("steps", ())
            assert isinstance(steps, list)
            for step in steps:
                assert isinstance(step, dict)
                action = step.get("uses")
                if isinstance(action, str) and action.startswith("actions/checkout@"):
                    checkout_options = step.get("with")
                    assert isinstance(checkout_options, dict)
                    assert checkout_options["persist-credentials"] == "false"


def test_artifact_actions_use_verified_node24_releases() -> None:
    observed: list[str] = []
    for path in PRIMARY_WORKFLOWS:
        workflow = _workflow(path)
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        for job in jobs.values():
            assert isinstance(job, dict)
            steps = job.get("steps", ())
            assert isinstance(steps, list)
            for step in steps:
                assert isinstance(step, dict)
                action = step.get("uses")
                if isinstance(action, str) and action.startswith(
                    ("actions/upload-artifact@", "actions/download-artifact@")
                ):
                    observed.append(action)
    assert observed
    assert set(observed) <= {UPLOAD_ARTIFACT_V7, DOWNLOAD_ARTIFACT_V8}


def test_security_workflow_exposes_one_stable_required_check() -> None:
    workflow = _workflow(PRIMARY_WORKFLOWS[1])
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert {"pull_request", "push", "merge_group", "schedule"} <= set(triggers)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    required = jobs["security-required"]
    assert isinstance(required, dict)
    assert required["name"] == "Security required"
    assert required["if"] == "always()"
    assert set(required["needs"]) == {
        "dependency-review",
        "codeql",
        "dependency-audit",
    }

    audit = jobs["dependency-audit"]
    assert isinstance(audit, dict)
    steps = audit["steps"]
    assert isinstance(steps, list)
    step_names = {step.get("name") for step in steps if isinstance(step, dict)}
    assert {
        "Validate lock hashes and audit PyPI advisories",
        "Supplement the Python audit with OSV advisories",
        "Audit locked TUI dependencies",
        "Audit locked docs-site dependencies",
    } <= step_names
    steps_by_name = {step.get("name"): step for step in steps if isinstance(step, dict)}
    pypi_audit = steps_by_name["Validate lock hashes and audit PyPI advisories"]["run"]
    osv_audit = steps_by_name["Supplement the Python audit with OSV advisories"]["run"]
    assert isinstance(pypi_audit, str)
    assert isinstance(osv_audit, str)
    assert "--vulnerability-service pypi" in pypi_audit
    assert "--disable-pip" not in pypi_audit
    assert "--vulnerability-service osv" in osv_audit
    assert "--disable-pip" in osv_audit


def test_docs_site_deployment_is_main_only() -> None:
    workflow = _workflow(PRIMARY_WORKFLOWS[4])
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert isinstance(triggers, dict)
    assert {"push", "workflow_dispatch"} == set(triggers)
    push = triggers["push"]
    assert isinstance(push, dict)
    assert push["branches"] == ["main"]

    assert isinstance(jobs, dict)
    assert set(jobs) == {"build", "deploy"}
    build = jobs["build"]
    deploy = jobs["deploy"]
    assert isinstance(build, dict)
    assert isinstance(deploy, dict)

    build_steps = build["steps"]
    assert isinstance(build_steps, list)
    build_by_name = {
        step.get("name"): step for step in build_steps if isinstance(step, dict)
    }
    assert build_by_name["Install dependencies"]["run"] == "npm ci"
    assert build_by_name["Check site"]["run"] == "npm run check"
    assert build_by_name["Build site"]["run"] == "npm run build"
    assert build_by_name["Check built links"]["run"] == "npm run check:links"

    main_only = "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
    assert build_by_name["Upload Pages artifact"]["if"] == main_only
    assert deploy["if"] == main_only
    assert deploy["needs"] == "build"
    assert deploy["concurrency"] == {
        "group": "pages",
        "cancel-in-progress": "false",
    }
    assert deploy["environment"] == {
        "name": "github-pages",
        "url": "${{ steps.deployment.outputs.page_url }}",
    }


def test_release_gate_binds_artifacts_to_main_and_version_tag() -> None:
    workflow = _workflow(PRIMARY_WORKFLOWS[3])
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"build-and-verify", "verify-release", "attest-release"}
    build = jobs["build-and-verify"]
    verify = jobs["verify-release"]
    attest = jobs["attest-release"]
    assert isinstance(build, dict)
    assert isinstance(verify, dict)
    assert isinstance(attest, dict)
    build_steps = build["steps"]
    attest_steps = attest["steps"]
    assert isinstance(build_steps, list)
    assert isinstance(attest_steps, list)
    build_by_name = {
        step.get("name"): step for step in build_steps if isinstance(step, dict)
    }
    attest_by_name = {
        step.get("name"): step for step in attest_steps if isinstance(step, dict)
    }

    revision = build_by_name["Verify release revision"]["run"]
    identity_step = build_by_name["Verify release identity"]
    identity = identity_step["run"]
    assert isinstance(revision, str)
    assert isinstance(identity, str)
    assert "git merge-base --is-ancestor" in revision
    assert '"v${version}"' in revision
    assert "refs/heads/main" in revision
    assert 'policy="current"' in identity
    assert 'policy="absent"' in identity
    assert "python scripts/release/check_identity.py" in identity
    assert "uv run python scripts/release/check_identity.py" not in identity
    assert "--require-check-runs" in identity
    assert '--github-repository "${GITHUB_REPOSITORY}"' in identity
    assert '--github-sha "${GITHUB_SHA}"' in identity
    assert identity_step["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert workflow["permissions"] == {"checks": "read", "contents": "read"}
    build_step_names = [
        step.get("name") for step in build_steps if isinstance(step, dict)
    ]
    assert build_step_names.index("Verify release revision") < build_step_names.index(
        "Setup Python"
    )
    assert build_step_names.index("Setup Python") < build_step_names.index(
        "Verify release identity"
    )
    assert build_step_names.index("Verify release identity") < build_step_names.index(
        "Setup uv"
    )
    assert build_step_names.index("Verify release identity") < build_step_names.index(
        "Install locked dependencies"
    )

    deterministic_gate = build_by_name["Re-run deterministic release gate"]["run"]
    assert isinstance(deterministic_gate, str)
    assert "npm pack ./tui --dry-run" in deterministic_gate
    assert "npm --prefix tui pack --dry-run" not in deterministic_gate
    assert "--vulnerability-service pypi" in deterministic_gate
    assert "--vulnerability-service osv" in deterministic_gate
    assert deterministic_gate.count("--disable-pip") == 1

    assert "permissions" not in build
    assert attest["if"] == "github.event_name == 'push'"
    assert set(attest["needs"]) == {"build-and-verify", "verify-release"}
    assert attest["environment"] == "release"
    assert attest["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    upload = build_by_name["Upload verified artifacts"]
    download = attest_by_name["Download verified artifacts"]
    checksum = attest_by_name["Verify downloaded asset checksums"]
    provenance = attest_by_name["Attest release provenance"]
    assert upload["uses"] == UPLOAD_ARTIFACT_V7
    assert download["uses"] == DOWNLOAD_ARTIFACT_V8
    assert provenance["uses"] == ATTEST_V4
    assert upload["with"]["name"] == "awesome-release-${{ github.sha }}"
    assert download["with"] == {
        "name": "awesome-release-${{ github.sha }}",
        "path": "dist/release",
    }
    assert checksum["working-directory"] == "dist/release"
    assert checksum["run"] == "sha256sum --check --strict SHA256SUMS"
    assert provenance["with"] == {"subject-checksums": "dist/release/SHA256SUMS"}
    assert set(attest_by_name) == {
        "Download verified artifacts",
        "Verify downloaded asset checksums",
        "Attest release provenance",
    }
    assert [step for step in attest_steps if "run" in step] == [checksum]
    assert not any(
        isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/checkout@")
        for step in attest_steps
    )

    assert verify["needs"] == "build-and-verify"
    matrix = verify["strategy"]["matrix"]
    assert isinstance(matrix, dict)
    assert set(matrix["os"]) == {"windows-latest", "macos-latest"}
    verify_steps = verify["steps"]
    assert isinstance(verify_steps, list)
    verify_by_name = {
        step.get("name"): step for step in verify_steps if isinstance(step, dict)
    }
    assert verify_by_name["Download the build-once artifacts"]["uses"] == (
        DOWNLOAD_ARTIFACT_V8
    )
    assert (
        "scripts/release/verify_bundle.py"
        in verify_by_name["Verify the downloaded release bundle"]["run"]
    )


def test_dependabot_covers_every_locked_dependency_ecosystem() -> None:
    document = _workflow(DEPENDABOT)
    updates = document["updates"]
    assert isinstance(updates, list)
    configured = {
        (entry["package-ecosystem"], entry["directory"])
        for entry in updates
        if isinstance(entry, dict)
    }
    assert configured == {
        ("uv", "/"),
        ("npm", "/tui"),
        ("npm", "/site"),
        ("github-actions", "/"),
    }


def test_build_backend_and_toolchain_are_locked_to_release_inputs() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    build_requires = project["build-system"]["requires"]
    dev_requires = project["dependency-groups"]["dev"]
    assert build_requires == ["hatchling==1.31.0"]
    assert "hatchling==1.31.0" in dev_requires

    assert 'UV_VERSION="0.11.28"' in (ROOT / "install.sh").read_text(encoding="utf-8")
    assert '$UvVersion = "0.11.28"' in (ROOT / "install.ps1").read_text(
        encoding="utf-8-sig"
    )
    for path in PRIMARY_WORKFLOWS:
        assert "0.11.24" not in path.read_text(encoding="utf-8")

    build_bundle = (ROOT / "scripts" / "release" / "build_bundle.py").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r'"uv",\s*"build",\s*"--wheel",\s*"--no-build-isolation",',
        build_bundle,
    )
    ci = PRIMARY_WORKFLOWS[0].read_text(encoding="utf-8")
    assert "uv build --wheel --no-build-isolation" in ci
