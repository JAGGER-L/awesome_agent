# Target test suite

This suite protects the implemented Local-first product and retained contracts.
It is not a compatibility suite for deleted architecture.

## Layers

- `unit/`: Agent, Application, configuration, context, conversations, Core
  tools/changes, extensions, memory, model/provider contracts, protocol,
  safety, and storage.
- `integration/`: cross-package trust, SQLite/checkpoints, tool/change,
  memory/extension, Agent Turn, recovery, and headless product behavior.
- `e2e/`: real `awesome-core` stdio flows with deterministic fake external
  boundaries.
- `packaging/`: bundle and installer contracts.
- `structural/`: ownership, dependencies, version, documentation, and final
  repository shape.
- `tui/tests/`: Ink rendering, protocol, state, lifecycle, CLI, packaging, and
  real stdio integration.

Use affected subsets while developing. The complete gate and its exact command
order are in [docs/development/testing.md](../docs/development/testing.md).

Delete tests that only bind removed structure. When observable behavior still
matters, rewrite its test against a retained public or package boundary. Never
add compatibility code, permanent skips, or expected failures solely for an
obsolete test.

Live DeepSeek, Kimi, Mem0 Cloud, network, and cross-host installer checks are
manual release evidence. Deterministic tests must not require credentials.
