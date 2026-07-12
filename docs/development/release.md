# Release 1.1.1

Awesome 1.1.1 is published as a manual GitHub Release in this order:

1. Confirm PR1 through PR3 are merged into the hotfix branch and PR4 is its only
   remaining pull request.
2. Run the deterministic Python and TUI [Release Gate](testing.md#release-gate).
3. Build and inspect exactly four assets: `install.sh`, `install.ps1`,
   `awesome-1.1.1.zip`, and `SHA256SUMS`.
4. Run `scripts/release/verify_bundle.py` against `awesome-1.1.1.zip` to prove
   the bundled database upgrade and TUI version contract.
5. Run the external DeepSeek, Kimi, and Mem0 Cloud checks only with temporary
   process credentials. Record only redacted status, duration, and diagnostic
   codes.
6. Merge PR4 into the hotfix branch.
7. Merge the hotfix branch into `main`.
8. Rebuild from `main`, create the `v1.1.1` tag and GitHub Release, upload the
   four assets, and verify their remote hashes.
9. Close every running Awesome process and rerun the original one-line
   installer to upgrade.
10. In a new terminal, verify `awesome --version`, workspace startup, and one
    `hi` Turn.

Users upgrade by closing Awesome and rerunning the same installation command.
