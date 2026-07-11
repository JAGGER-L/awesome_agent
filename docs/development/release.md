# Manual V1.0.0 pilot release

This limited pilot uses a manual GitHub Release. Perform these steps in order:

1. Verify `codex/local-first-architecture` is clean and contains all eight
   sequential Phase 4 PR merges.
2. Run the complete retained gate in [Testing](testing.md).
3. Run `uv run python scripts/release/build_bundle.py` and inspect
   `dist/release` locally.
4. On clean/disposable homes, smoke Windows 11 x64, Apple Silicon macOS, and
   WSL2 Ubuntu 24.04 x64: install, new-terminal PATH, version/help, Welcome,
   workspace trust, one deterministic local flow, closed-process reinstall,
   and preserved user state.
5. With non-committed credentials on one supported host, run one minimal real
   DeepSeek Turn, one minimal real Kimi Turn, and one Mem0 Cloud
   add/recall/remove roundtrip. Record only redacted pass/fail evidence.
6. Review package contents, checksum, local/developer paths, credentials,
   caches, tests, sources, and version lineage. The only public assets are
   `install.sh`, `install.ps1`, `awesome-1.0.0.zip`, and `SHA256SUMS`.
7. After explicit final integration approval, merge the complete
   `codex/local-first-architecture` branch into `main`.
8. Tag `v1.0.0`, create a manual GitHub Release, upload the four assets, and
   state that it is a limited pilot for the three supported hosts.

Do not create a release workflow, channel, signature/notarization system,
background updater, update command, uninstaller, rollback system, package
manager submission, website, or domain for this pilot.
