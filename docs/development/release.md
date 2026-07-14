# Release 1.2.1

Awesome 1.2.1 is published as a manual GitHub Release. `VERSION` is the only
manually maintained product version; Core packaging and the generated TUI
version files must resolve to the same value.

## Release candidate

1. Confirm the intended integration branch is clean and contains every
   accepted change for the release.
2. Run the deterministic Python and TUI [Release Gate](testing.md#release-gate).
3. Build the candidate with:

   ```powershell
   uv run python scripts/release/build_bundle.py
   ```

4. Inspect exactly four files under `dist/release`: `install.sh`, `install.ps1`,
   `awesome-1.2.1.zip`, and `SHA256SUMS`.
5. Verify the archive with:

   ```powershell
   uv run python scripts/release/verify_bundle.py `
     dist/release/awesome-1.2.1.zip 1.2.1
   ```

   This proves the bundled Core and TUI versions, fresh Schema 7 bootstrap,
   read-only classification of older and newer Application state, exclusive
   reset ownership, and preservation of configuration, Skills, and Memory.
   The release contains no historical migration module.
6. Run DeepSeek, Kimi, and Mem0 Cloud checks only when fresh credentials are
   available in the temporary process environment. Record redacted outcomes;
   never write credentials into the repository or release notes.

## Publish

1. Merge the verified release preparation into the integration branch, then
   merge that exact integration branch into `main`.
2. From an up-to-date, clean `main`, rerun the Release Gate, rebuild the four
   assets, and rerun bundle verification. Do not reuse artifacts built from a
   feature branch.
3. Create and push tag `v1.2.1` at the verified `main` commit.
4. Create GitHub Release `v1.2.1` and upload the four files from
   `dist/release`.
5. Compare the remote asset names and SHA-256 checksum with the verified local
   files.
6. Close existing Awesome processes and rerun the normal one-line installer.
   In a new terminal, verify `awesome --version`, workspace startup, and one
   simple Turn.

Users upgrade by closing Awesome and rerunning the same installation command.
