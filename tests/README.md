# Test Strategy

Awesome uses the smallest validation set that protects the behavior changed by
the current task. Tests describe current product contracts and architecture.

## Fast Development Gate

Run formatting, lint, type checking, and the unit tests for the packages being
changed. These checks give fast feedback without coupling unrelated work to the
active change.

## Structural Contracts

`tests/structural/` protects package ownership, dependency direction, public
commands, version authority, documentation links, packaging inputs, and the
repository's current shape.

## Affected Integration Tests

Run integration tests when a change crosses a real package boundary such as
workspace trust, SQLite/checkpoints, Agent Turns, tool execution, memory, MCP,
or the JSON-RPC stdio Host.

## Deferred System Validation

Full E2E, smoke, performance, live-provider, network, and cross-host installer
validation is re-established as a release gate after the architecture baseline
stabilizes. Those suites are not required for every repository refactor.
