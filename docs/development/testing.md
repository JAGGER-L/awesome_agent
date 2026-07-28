# Testing and CI

Testing is evidence for an invariant at the cheapest layer that can prove it.
A passing reproduction is not enough when an equivalent input, cancellation,
race, recovery, protocol peer, or platform can violate the same invariant.

## Test layers

| Layer | Proves | Location |
| --- | --- | --- |
| unit | one pure policy, state transition, adapter, or bounded failure | `tests/unit/`, `tui/tests/**` |
| integration | real collaboration across a local component/persistence boundary | `tests/integration/` |
| E2E | one complete user-facing process flow | `tests/e2e/`, `tui/tests/e2e/` |
| structural | package ownership, inventory, dependency and source contracts | `tests/structural/`, `tui/tests/structural/` |
| packaging | installed wheel/TUI/installer/release shape | `tests/packaging/`, `tui/tests/packaging/` |
| external | explicitly enabled live provider/network evidence | `tests/external/` |

Tests protect current product contracts, not discarded implementation details.
If architecture intentionally removes a behavior, update the contract and its
test instead of adding an adapter solely to preserve an obsolete shape.

## Progressive local gate

Run checks in increasing cost. Stop when a lower gate fails unless you have
proved the failure is unrelated.

### 1. Python format and lint

```powershell
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
```

To format an intentional Python change:

```powershell
uv run ruff format src tests scripts
```

### 2. Strict type checking

```powershell
uv run mypy src tests scripts
```

Mypy is strict. Do not add an ignore to avoid modeling a boundary. The only
current missing-import override is the optional Mem0 package contract declared
in `pyproject.toml`.

### 3. Focused tests

Examples:

```powershell
uv run pytest -q tests/unit/core/tools/test_permissions.py
uv run pytest -q tests/unit/application/test_operation_controller.py
uv run pytest -q tests/integration/test_agent_turn.py
uv run pytest -q -k "shutdown or cancellation"
```

Use the narrowest file or selection that exercises the changed owner. When a
test needs the optional Memory implementation, run it as:

```powershell
uv run --extra memory pytest -q tests/integration/test_mem0_cloud.py
```

### 4. Structural and packaging contracts

```powershell
uv run pytest -q tests/structural tests/packaging
uv build --wheel --no-build-isolation
```

Structural tests are executable architecture. A failure should trigger an
ownership review, not a search-and-replace of the expected inventory.

### 5. TUI gate

```powershell
npm --prefix tui run version:check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm pack ./tui --dry-run
```

Run TUI tests whenever a Python payload, protocol method, event, command result,
or thread-transition contract changes—even if no `.ts` file was initially
edited. The packaging test is stronger than a bare dry-run: it performs a fresh
build, packs, installs the tarball, and executes the installed CLI. Keep the
explicit post-build `npm pack --dry-run` as a human-readable contents check;
running it before build can inspect stale `dist` or fail to prove the bin target.

### 6. Protocol v5 fixtures

```powershell
uv run python scripts/release/contract_versions.py
uv run python scripts/generate_protocol_fixtures.py --check
```

If the intentional contract changed, regenerate without `--check`, inspect all
fixture and manifest changes, update strict TypeScript schemas/presenters, then
rerun Python and TUI fixture tests. Do not hand-edit generated JSON.

For Web changes, use the fake Tavily transport/provider suites to cover Search
and Extract request/response bounds, public-HTTPS and blocked-domain admission,
24,000-character Fetch truncation, Tavily-cloud rather than local target
access, `trust_env=False`, explicit proxy selection, every stable HTTP failure,
no automatic retry, permission choices and grant revocation, the shared
eight-request hard budget, non-replayable recovery, citation finalization, and
Python/TypeScript round trips. A live Tavily request is an explicit release
gate only; ordinary tests must not require network access or a real key.

### 7. Documentation site

```powershell
npm ci --prefix site
npm --prefix site run check:navigation
npm --prefix site run check
$env:SITE_URL = "https://jagger-l.github.io"
$env:BASE_PATH = "/awesome_agent"
npm --prefix site run build
npm --prefix site run check:links
Remove-Item Env:SITE_URL, Env:BASE_PATH -ErrorAction SilentlyContinue
```

`check` synchronizes source Markdown, validates the navigation manifest, and
runs Astro checks. The production-base build also generates a base-aware
`dist/llms.txt`; the built-link check catches deployment-path errors that a
root-hosted dev server can hide.

### 8. Complete deterministic suites

```powershell
uv run --extra memory pytest -q tests/unit
uv run --extra memory pytest -q tests/integration
uv run --extra memory pytest -q tests/e2e
uv run --extra memory pytest -q tests/packaging tests/structural
```

Required CI combines unit, integration, and E2E coverage with branch coverage
and a minimum of 80%. Do not chase the number with low-value assertions; cover
decisions, failures, and state transitions.

## Risk-to-test matrix

| Change | Minimum focused evidence | Add when crossed |
| --- | --- | --- |
| pure parser or policy | unit normal/boundary/negative cases | dialect/platform parameterization |
| Application state change | unit service test | integration with Conversation/Storage and foreground race |
| Agent route/state | node/budget/state unit tests | compiled graph integration and recovery |
| file mutation | tool + filesystem unit tests | Change Journal integration, conflict, Windows reparse |
| shell process | command-policy + runner tests | real timeout/cancel/process-tree test per platform |
| provider adapter | normalized stream/error unit tests | Gateway integration; live check only for release evidence |
| MCP/Skill/Memory | package unit tests | atomic catalog/load integration and malformed/limit cases |
| protocol/event/result | Python fixture tests | TypeScript schema, reducer, presenter, E2E stdio |
| TUI keyboard/mode | reducer/router unit tests | component flow and terminal E2E |
| storage schema/recovery | database unit tests | crash-window, lock, reset, and packaging verifier |
| documentation/navigation | Markdown inventory/link tests | Astro check, production build, built-link validation |

## Designing robust tests

### Prove the invariant, not one spelling

For a parser or safety boundary, test original input, case/path/suffix variants,
nested wrappers, compound/newline forms, and a benign string containing similar
words. A security fix that only matches one reproduction is incomplete.

### Make concurrency deterministic

Use `asyncio.Event`, barriers, injected fakes, and controlled task handoff.
Exercise both orderings:

```text
Operation wins -> command/mutation is busy
command/mutation wins -> Operation is busy
```

Also prove cleanup releases ownership and shutdown prevents new admission. Do
not rely on arbitrary sleeps to “probably” create a race.

### Treat cancellation separately

Cancellation is not a normal failure. Assert that:

- cleanup is bounded;
- the original `CancelledError` propagates where required;
- one terminal event and one audit activity exist;
- durable state is terminal or explicitly resumable;
- child processes/readers are reaped;
- no automatic retry duplicates an uncertain effect.

### Test limits at both sides

For byte, token, page, depth, node, timeout, and queue limits, test just below,
at, and above the boundary. Include malformed shapes that fail before expensive
work or external I/O.

### Keep deterministic suites offline

Provider, Mem0, MCP, and process tests use fakes or local fixtures. Normal CI
does not require credentials. Live behavior is separate release evidence so a
network outage cannot redefine a code regression.

## Platform evidence

Filesystem and process semantics differ materially:

- Windows: junctions/reparse points, path aliases, Job Objects, `taskkill`, and
  locked database rename;
- POSIX: symlinks, descriptor-relative paths, process groups, and detached
  inode behavior;
- shell policy: CMD, POSIX, and PowerShell should use explicit dialect
  parameters on any host.

A platform skip records missing evidence; it is not a passing substitute. Put
real Windows-only reparse/process tests in the Windows contracts job and use the
nightly three-OS matrix for broader system evidence.

The candidate installer hook makes pre-tag release evidence executable without
publishing assets: serve the manual-dispatch artifact on `127.0.0.1`, then run
the installer with the explicit candidate variables. The current manual
real-host gate covers Windows 11 x64 only; a hosted Windows Server job is not a
substitute. WSL2 and Apple Silicon macOS retain automated CI/nightly evidence,
and their missing manual real-host evidence is recorded as release residual risk.

## Required CI

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, merge-queue
revisions, and manual dispatch. Its stable `Required` aggregator depends on:

| Job | Evidence |
| --- | --- |
| Python quality | actionlint, lock check, Ruff, strict mypy, Protocol fixtures |
| Python tests and coverage | unit + integration + E2E with branch coverage |
| Windows contracts | Windows-sensitive Core/Application/Protocol/extension tests plus installer source/parse contracts; not a real download-and-install flow |
| Structural and packaging contracts | ownership, inventory, wheel build and clean install |
| TUI matrix | Node 22.23.1/24 on Ubuntu and 22.23.1 on Windows and macOS |
| Docs site | navigation, Astro check, base-aware build, built-link check |

Branch rules should require the stable `Required` check, not matrix-generated
job display names.

Pull-request revisions cancel stale Required CI runs. Jobs use explicit
deadlines and third-party Actions pinned to full commit hashes. Required CI
downloads a checksum-pinned actionlint binary before validating workflows.

## Security and nightly CI

`.github/workflows/security.yml` supplies the stable `Security required`
aggregator:

- dependency review for new pull-request dependencies;
- CodeQL for Python and JavaScript/TypeScript;
- locked Python export audited through pip-audit's hash-validating PyPI path;
- supplemental OSV lookup over the validated name/version graph;
- npm lock audits for TUI and documentation site.

The OSV command uses `--disable-pip` only as a supplemental advisory lookup. It
does not independently validate artifacts; the PyPI path performs the hash
check first.

`.github/workflows/nightly.yml` runs the complete Python suite and TUI/package
tests on Ubuntu, Windows, and macOS, plus npm audits. Nightly evidence expands
platform coverage but does not excuse a focused PR regression.

## Known CI evidence gaps

The candidate-artifact installer smoke is now an explicit manual pre-tag gate,
but it is intentionally not mislabeled as hosted CI evidence. The workflows
still leave four useful automation additions. They should be added as focused
jobs rather than overstating the existing gates:

1. **Source-derived documentation contracts.** Generate or compare reference
   inventories from `COMMAND_OWNERS`, configuration models, Tool registration,
   and Protocol method models so a new public contract cannot bypass docs by
   forgetting to update a hand-copied test list.
2. **Browser accessibility smoke.** Use Playwright plus axe against the built
   homepage, one English guide, and its corresponding Chinese page. Static
   contrast and link checks cannot prove keyboard navigation, landmarks, ARIA,
   mobile menus, search, language switching, or copy-button behavior.
3. **Scheduled external-link check.** Run with bounded timeouts, retries, and an
   allowlist as a non-PR-blocking scheduled workflow; the deterministic local
   link checker intentionally skips other origins.
4. **Post-deployment Pages smoke.** After deployment, fetch the real base URL,
   representative English and Chinese pages, `llms.txt`, and a representative
   unknown/non-canonical route that must return 404 without redirecting. Build
   success does not prove the deployed origin and base-path routing are reachable.

## Release gate

The release workflow rebuilds from the exact revision, runs deterministic
Python, TUI, Protocol, audit, and packaging gates, builds one release bundle on
Ubuntu, and reverifies that downloaded artifact on Windows and macOS. Only a
tag run that passes every unprivileged job reaches attestation.

Manual dispatch from `main` additionally requires the latest GitHub Actions
`Required` and `Security required` check-runs to be successful for the exact
`GITHUB_SHA`. Its artifact is served over loopback for installer smoke on a real
Windows 11 x64 host before a tag is allowed. The tag workflow rebuild is
compared byte-for-byte by the three published asset hashes; any difference
requires that Windows real-host loopback smoke to be repeated on the tag
artifact before publication. Hosted Windows/macOS verification and nightly
three-OS coverage remain automated gates. See [Release](release.md) for the
commands and the post-publication rollout recheck.

The local release-quality gate is:

```powershell
uv sync --locked --extra memory --dev
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src tests scripts
uv run --extra memory pytest -q tests
uv run python scripts/generate_protocol_fixtures.py --check
uv lock --check
uv build --wheel --no-build-isolation

npm --prefix tui ci
npm --prefix tui run version:check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm --prefix tui audit --package-lock-only --audit-level=high
npm pack ./tui --dry-run
```

Dependency audits and release bundle verification are detailed in
[Release](release.md).

## Live release evidence

With fresh credentials in a temporary environment:

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY, Env:TAVILY_API_KEY `
  -ErrorAction SilentlyContinue
```

Record provider/service, status, duration, and redacted diagnostic code only.
If credentials, network, or a platform are unavailable, state the missing
evidence and residual risk rather than reporting success.

## Failure triage

1. Read the first failing lower gate and its exact command.
2. Reproduce that job's lockfile, OS, Node/Python version, and environment.
3. Decide whether the failure is code, contract drift, generated-file drift,
   packaging, platform behavior, external authorization, or infrastructure.
4. Fix the owning layer; do not weaken the test or aggregator.
5. Rerun the focused failure, then the next broader gate.

For GitHub Actions, inspect the actual log before editing code. A maintainer
approval, label gate, exhausted quota, or unavailable service is not a product
test failure, but it still prevents claiming the required check passed.
