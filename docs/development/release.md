# Release

A release is one reviewed source revision, one product version, one build of the
bundle, cross-platform verification of that same artifact, and checksums and
provenance that match the published assets. Rebuilding on each platform would
test source reproducibility but would not prove that users received the bytes
already verified elsewhere.

## Release invariants

- `VERSION` is the only manually maintained product version source.
- Python metadata, TUI package/lock/generated source, Protocol fixture manifest,
  installers, archive name, and embedded payload agree with `VERSION`.
- The release revision is on `main` and has passed Required and Security checks.
- Deterministic tests need no live credential or network service.
- The bundle is built once on Ubuntu and the downloaded bytes are verified on
  Windows and macOS.
- `SHA256SUMS` covers exactly the three published executable/archive assets.
- Tag provenance is attested only after unprivileged platform verification.
- A published tag or asset is never silently moved or replaced; corrections use
  a new version.

## Roles of the artifacts

`scripts/release/build_bundle.py` creates exactly four files:

```text
dist/release/
  install.sh
  install.ps1
  awesome-<version>.zip
  SHA256SUMS
```

The archive contains one deterministic top-level directory with:

- `VERSION`;
- the validated pure-Python Awesome wheel;
- an exact, SHA-256-hashed production requirements lock;
- the built TUI entry point, package metadata, npm lock, and license.

The installers are published beside the archive because the one-line install
URLs target those assets directly. They validate checksums and install private
Python/Node runtimes under the product installation root; they are not
source-checkout development scripts. That root is distinct from `AWESOME_HOME`,
which owns configuration, credentials, state, and Memory. The default install
roots are `~/.local/share/awesome` on POSIX and
`%LOCALAPPDATA%\Programs\Awesome` on Windows.

## 1. Choose and prepare the version

Choose a new semantic version. Do not reuse an existing Git tag or GitHub
Release version.

1. Update `VERSION` to exactly `MAJOR.MINOR.PATCH` plus one newline.
2. Synchronize TUI-owned copies:

   ```powershell
   npm --prefix tui run version:sync
   ```

3. Update the version constants in both root installers.
4. Regenerate Protocol fixtures so the manifest records the product version:

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py
   ```

5. Prepare GitHub Release notes from accepted changes, including user-visible
   behavior, security boundaries, configuration/state compatibility, and known
   limitations.
6. Inspect every version-related diff. A feature branch should not contain an
   accidental version change.

Protocol version and Application schema version are independent. Increment
Protocol only for an incompatible wire change. Increment Application schema
only when persisted semantics cannot be read safely. Neither replaces the need
for a unique product version.

## 2. Run deterministic release gates

Use the complete gate in [Testing](testing.md). At minimum it includes locked
dependency setup, Ruff, strict mypy, all Python tests, Protocol fixture check,
TUI format/lint/typecheck/tests/build, lock check, audits, wheel build, and npm
pack dry-run.

Run Python dependency audits from an exact exported graph:

```powershell
$Requirements = Join-Path ([System.IO.Path]::GetTempPath()) `
  "awesome-agent-release-requirements-$PID.txt"
try {
  uv export --locked --extra memory --no-dev --no-emit-project `
    --format requirements.txt --output-file $Requirements
  uv run pip-audit --require-hashes --progress-spinner off `
    --vulnerability-service pypi --requirement $Requirements
  uv run pip-audit --require-hashes --disable-pip --progress-spinner off `
    --vulnerability-service osv --requirement $Requirements
} finally {
  Remove-Item -LiteralPath $Requirements -ErrorAction SilentlyContinue
}
```

The first command lets pip-audit validate exported hashes through its isolated
pip path. The OSV command is supplemental advisory coverage over the already
validated exact graph; `--disable-pip` there is not a substitute for the first
check.

Also audit the npm lock:

```powershell
npm --prefix tui audit --package-lock-only --audit-level=high
```

An advisory requires an explicit decision: update, constrain, document why it
does not affect the shipped path, or stop the release. Do not suppress the gate
without a reviewed repository policy.

## 3. Build and verify locally

From a clean checkout of the intended release revision:

```powershell
uv run python scripts/release/build_bundle.py
$Version = (Get-Content VERSION -Raw).Trim()
uv run python scripts/release/verify_bundle.py `
  "dist/release/awesome-$Version.zip" $Version
```

The builder itself creates the wheel, exports the hashed requirements, checks
version agreement and TUI output, rejects forbidden content, assembles a
deterministic ZIP, copies installers, and writes checksums.

The verifier checks:

- release-directory inventory and all checksums;
- archive path safety, member inventory, and payload version;
- wheel filename, metadata, compatibility, entry points, RECORD hashes, import
  origins, and absence of editable/migration content;
- exact hashed dependency requirements and isolated installation;
- `uv pip check`, Core import, and console entry point;
- TUI package/version/entry point;
- current storage bootstrap, incompatible-state classification, exclusive reset
  ownership, and preservation of config, Skills, and Memory.

Verification must operate on the built wheel and extracted payload. A fallback
to the editable checkout would prove the wrong artifact and is rejected.

## 4. Collect optional live evidence

With fresh credentials and a stable network, run the explicitly gated DeepSeek,
Kimi, and Mem0 checks:

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY -ErrorAction SilentlyContinue
```

Record only service, status, duration, and redacted diagnostic code. Live
evidence supplements deterministic adapter tests; it does not authorize
checking credentials into the repository. If it cannot be collected, state
that gap in the release decision.

## 5. Merge the release preparation

Before merge:

1. ensure the branch contains only accepted release work;
2. confirm Required and Security aggregators pass;
3. confirm no unresolved review or merge conflict;
4. verify the diff contains no secrets, generated caches, debug output, or
   stale documentation;
5. merge the exact reviewed revision to `main`.

From an up-to-date clean `main`, rerun the local identity/fixture checks. A
manual `Release gate` workflow dispatch is permitted only from `main`; it builds
and uploads a candidate but does not attest provenance.

## 6. Tag and verify CI artifacts

Create an annotated or lightweight version tag that exactly matches `VERSION`:

```powershell
$Version = (Get-Content VERSION -Raw).Trim()
git tag "v$Version"
git push origin "v$Version"
```

The `Release gate` workflow verifies that the tagged commit is an ancestor of
`origin/main` and that the tag is exactly `v<version>`. It then:

1. installs locked Python and TUI dependencies;
2. reruns deterministic release checks and audits;
3. builds and verifies the bundle once on Ubuntu;
4. uploads the four-file artifact;
5. downloads and verifies the same artifact on Windows and macOS;
6. enters the protected `release` environment;
7. rechecks `SHA256SUMS` and creates a GitHub artifact attestation for the
   three checksummed subjects.

Windows and macOS do not rebuild. A failed platform verifier invalidates the
candidate.

## 7. Publish the GitHub Release

The workflow does not create a GitHub Release automatically. After the tagged
workflow and attestation succeed:

1. download `awesome-release-<commit>` from the successful workflow;
2. verify `SHA256SUMS` locally once more;
3. create GitHub Release `v<version>` at the existing tag;
4. paste the reviewed release notes;
5. upload exactly `install.sh`, `install.ps1`, `awesome-<version>.zip`, and
   `SHA256SUMS` from that workflow artifact;
6. compare remote names, sizes, and all three SHA-256 values with the verified
   artifact;
7. verify the published attestation refers to the same subjects.

Do not rebuild, edit, recompress, or regenerate an asset between CI verification
and upload.

## 8. Installation smoke test

After publication, close existing Awesome processes and test the documented
installer on each supported host where release evidence is required.

Verify:

```text
one-line installer succeeds
awesome --version == VERSION
workspace starts and trust prompt is correct
provider configuration is visible without exposing a key
one simple Turn completes
one read-only command and one approved edit behave correctly
close/restart and --continue restore the expected Thread
```

Install tests should use a temporary home and workspace. Never point destructive
test cleanup at a user home, repository root, or unresolved environment
variable.

## Repository controls outside source

Maintainers must separately verify:

- branch rules require `Required` and `Security required`;
- the version-tag namespace is protected against movement/deletion;
- the `release` environment permits only version tags and requires the intended
  reviewer policy;
- Actions are restricted to the reviewed allowlist;
- GitHub Dependency Graph and Dependabot are enabled;
- secret scanning and push protection are enabled;
- GitHub Pages deployment environment and permissions are correct.

These settings cannot be proven by workflow files alone. For a single
maintainer, rules may allow an explicit administrative break-glass path, but a
normal merge/release should still wait for required checks.

## Failed or defective release

If a tag workflow fails, fix the source on a new commit and choose a new version
when any public tag or asset already exists. Never move a published tag to make
the old name point at different code.

If a release is published with a serious defect:

1. mark the release and notes clearly;
2. stop recommending its installers;
3. assess credential, state-schema, and workspace impact;
4. prepare a fixed higher version through the same full gate;
5. publish recovery or upgrade guidance that does not ask users to delete local
   state blindly.

Downgrading a binary across a newer Application schema is not a supported
recovery technique. The older binary will fail closed on newer state.

## Release record

Retain in the GitHub Release or maintainer handoff:

- version, tag, and commit SHA;
- Required/Security/Release gate run links;
- artifact attestation and checksums;
- deterministic and optional live evidence;
- supported-platform smoke results;
- unverified evidence and residual risk;
- state/protocol compatibility notes;
- exact published asset inventory.

Do not include secret values, private machine paths, raw provider responses, or
unbounded CI logs.
