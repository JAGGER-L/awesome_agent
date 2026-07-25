# Release

`v1.2.1` is an already-published historical release. Do not move that tag or
replace it with a different build. This hardening work is source and CI work;
before publishing it, maintainers must choose a new version and update
`VERSION`, the generated TUI version, both installers, and release notes as one
reviewed release-preparation change. Protocol v3 prevents an old Core and new
TUI that share a product version from handshaking, but it is not a substitute
for a unique release version.

`VERSION` is the only manually maintained product version; Core packaging and
the generated TUI version files must resolve to the same value.

The tracked `Release gate` workflow runs for version tags and manual release
candidates. Its unprivileged build job rebuilds from the exact revision, reruns
deterministic Python, TUI, protocol, and bundle verification, then uploads the
four verified CI artifacts. Unprivileged Windows and macOS jobs download and
reverify that exact build; they never rebuild it. Only after every platform
passes does a version-tag run target the separate `release` environment and
grant the minimal OIDC/attestation permissions needed to attest the two
installers and archive named by `SHA256SUMS`. Administrators must protect both
the `v*` tag namespace and that environment with allowed-tag rules and required
reviewers; the workflow file alone cannot create that repository policy. A
manual run does not emit provenance. The workflow does not publish a GitHub
Release automatically.

## Release candidate

1. Confirm the intended integration branch is clean and contains every
   accepted change for the release.
2. Run the deterministic Python and TUI [Release Gate](testing.md#release-gate).
3. Build the candidate with:

   ```powershell
   uv run python scripts/release/build_bundle.py
   ```

4. Inspect exactly four files under `dist/release`: `install.sh`, `install.ps1`,
   `awesome-<version>.zip`, and `SHA256SUMS`.
5. Verify the archive with:

   ```powershell
   $Version = (Get-Content VERSION -Raw).Trim()
   uv run python scripts/release/verify_bundle.py `
     "dist/release/awesome-$Version.zip" $Version
   ```

   This validates the wheel member inventory, metadata, RECORD hashes, and
   import origins; validates every dependency as an exact version with a
   SHA-256 hash; and installs that lock plus the wheel into an isolated
   environment before running `uv pip check` and a Core smoke import. The
   sibling checksum manifest must list exactly `install.sh`, `install.ps1`, and
   the archive, and every digest is checked before archive extraction. It also
   proves the bundled Core and TUI versions, fresh Schema 7 bootstrap, read-only
   classification of older and newer Application state, exclusive reset
   ownership, and preservation of configuration, Skills, and Memory. The
   release contains no historical migration module.
6. Run DeepSeek, Kimi, and Mem0 Cloud checks only when fresh credentials are
   available in the temporary process environment. Record redacted outcomes;
   never write credentials into the repository or release notes.

## Publish

1. Merge the verified release preparation into the integration branch, then
   merge that exact integration branch into `main`.
2. From an up-to-date, clean `main`, rerun the Release Gate, rebuild the four
   assets, and rerun bundle verification. Do not reuse artifacts built from a
   feature branch.
3. Create and push tag `v<version>` at the verified `main` commit.
4. Create the matching GitHub Release and upload the four files from
   the successful `Release gate` artifact. Do not rebuild between attestation
   and upload.
5. Compare the remote asset names and all three SHA-256 checksums with the
   verified workflow artifact.
6. Close existing Awesome processes and rerun the normal one-line installer.
   In a new terminal, verify `awesome --version`, workspace startup, and one
   simple Turn.

Users upgrade by closing Awesome and rerunning the same installation command.

## Residual controls

Repository administrators must separately verify the branch ruleset, protected
tag rules, `release` environment reviewers, Actions allowlist, secret scanning,
and push protection. Those controls live in GitHub settings and cannot be
proven by files in this repository.

Before a public release, maintainers must also make explicit product and legal
decisions about repository-wide license distribution and supported dependency
major-version ceilings. CI intentionally does not infer or silently change
either policy.
