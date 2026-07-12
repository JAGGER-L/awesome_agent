# Release 1.0.0

Awesome 1.0.0 is published as a manual GitHub Release.

1. Confirm the release branch is clean and contains the intended changes.
2. Run the complete [Release Gate](testing.md#release-gate).
3. Run `uv run python scripts/release/build_bundle.py` and inspect
   `dist/release`. Confirm the ZIP contains the hashed production requirements
   exported from the checked `uv.lock`.
4. For this pilot, test installation and first run directly on Windows: open a
   new terminal, check version/help, confirm workspace trust, complete one local
   workflow, close Awesome, and rerun the installer.
5. With credentials kept outside the repository, run one small DeepSeek Turn,
   one small Kimi Turn, and one Mem0 Cloud add/recall/remove check. Record only
   redacted pass/fail evidence using the explicit live-service command in the
   [testing guide](testing.md#release-gate).
6. Confirm the release directory contains only `install.sh`, `install.ps1`,
   `awesome-1.0.0.zip`, and `SHA256SUMS`, with no credentials, caches, tests,
   sources, or local paths.
7. Merge the approved release changes, tag `v1.0.0`, create the GitHub Release,
   and upload the four assets.

Users upgrade by closing Awesome and running the same installation command
again.
