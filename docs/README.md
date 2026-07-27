# Awesome documentation

[English](README.md) | [简体中文](README.zh-CN.md)

This directory is the canonical documentation source for Awesome. The GitHub
Pages site is generated from these Markdown files; this page is intentionally
kept as the repository-facing map and is not published as a second website
homepage.

Awesome's documentation is organized by the question a reader is trying to
answer, not by the Python package tree:

```text
Can I use Awesome?        -> Start here
How should I work with it? -> Core concepts + User guide
How do I extend it?       -> Extensions
What is the exact contract? -> Reference
How is it implemented?    -> Architecture
How do I change it safely? -> Development
What exists now or later? -> Roadmap
```

## Choose a path

| Goal | Start with | Continue with |
| --- | --- | --- |
| Install and complete one useful Turn | [Quickstart](getting-started/quickstart.md) | [Daily workflow](user-guide/README.md) |
| Understand the product before installing | [Start here](getting-started/README.md) | [Operating model](concepts/README.md) |
| Work safely in an existing repository | [Permissions](user-guide/permissions.md) | [Tools and shell](user-guide/tools-and-shell.md), then [changes](user-guide/changes.md) |
| Configure models, budgets, or extensions | [Configuration guide](user-guide/configuration.md) | [Configuration reference](reference/configuration.md) |
| Add durable context or external tools | [Extension decision guide](extensions/README.md) | [Memory](extensions/memory.md), [Skills](extensions/skills.md), or [MCP](extensions/mcp.md) |
| Diagnose a failure | [Troubleshooting](user-guide/troubleshooting.md) | [Files and state](reference/files-and-state.md) |
| Audit current implementation gaps | [Current known limitations](roadmap.md#current-known-limitations) | Follow the linked contract and architecture pages |
| Review the implementation | [Architecture overview](../ARCHITECTURE.md) | [Focused architecture guides](architecture/README.md) |
| Contribute a change | [Development guide](development/README.md) | [Testing](development/testing.md) and [contracts](development/contracts-and-documentation.md) |

## Documentation map

### Start here

- [Product overview and learning paths](getting-started/README.md)
- [Installation](getting-started/installation.md)
- [Five-step quickstart](getting-started/quickstart.md)

### Core concepts

- [How Awesome works](concepts/README.md)
- [Workspace, Thread, Turn, and Operation](concepts/workspace-thread-turn.md)
- [Context and workspace instructions](concepts/context-and-instructions.md)
- [Changes, cancellation, and recovery](concepts/changes-and-recovery.md)

### Use Awesome

- [Daily workflow](user-guide/README.md)
- [Commands and sessions](user-guide/commands.md)
- [Permissions and approvals](user-guide/permissions.md)
- [Tools and shell execution](user-guide/tools-and-shell.md)
- [Review, undo, and recover changes](user-guide/changes.md)
- [Configuration and providers](user-guide/configuration.md)
- [Troubleshooting](user-guide/troubleshooting.md)

### Extend Awesome

- [Choose an extension mechanism](extensions/README.md)
- [Memory](extensions/memory.md)
- [Skills](extensions/skills.md)
- [MCP](extensions/mcp.md)

### Reference

- [Reference index](reference/README.md)
- [CLI and keyboard](reference/cli.md)
- [Slash commands](reference/commands.md)
- [Configuration schema](reference/configuration.md)
- [Built-in tools](reference/built-in-tools.md)
- [Permission modes](reference/permission-modes.md)
- [Files and state](reference/files-and-state.md)
- [Protocol v4](reference/protocol.md)

### Architecture

- [Architecture reading path](architecture/README.md)
- [Request lifecycles](architecture/request-lifecycles.md)
- [Application and Agent](architecture/application-and-agent.md)
- [Context, models, and extensions](architecture/context-model-and-extensions.md)
- [Tool execution and Change Journal](architecture/tools-and-changes.md)
- [Storage and recovery](architecture/storage-and-recovery.md)
- [Protocol and TUI](architecture/protocol-and-tui.md)
- [Security and dependency boundaries](architecture/security-and-dependencies.md)

### Contribute

- [Contributor overview](development/README.md)
- [Setup and codebase](development/setup.md)
- [Testing and CI](development/testing.md)
- [Extend Awesome in code](development/extending-awesome.md)
- [Protocol and documentation contracts](development/contracts-and-documentation.md)
- [Release](development/release.md)
- [Roadmap](roadmap.md)

## Canonical ownership

Repeated facts drift. To keep the system maintainable, each kind of fact has one
owner:

| Fact | Canonical owner |
| --- | --- |
| Product position and shortest first run | Root README and quickstart |
| Exact command syntax, config fields, tool schemas, limits, and permission matrix | `docs/reference/` |
| User tasks and recovery procedures | `docs/user-guide/` |
| Extension setup and trust boundaries | `docs/extensions/` |
| System topology and dependency direction | Root `ARCHITECTURE.md` / `ARCHITECTURE.zh-CN.md` pair |
| Subsystem invariants and implementation flows | `docs/architecture/` |
| Build, test, CI, documentation, and release procedure | `docs/development/` |
| Current versus future product scope | `docs/roadmap.md` |

Other pages summarize and link to the owner instead of copying a full table or
configuration block.

## Language policy

English and Simplified Chinese are complete, first-class documentation sets.
Every English Markdown source must have exactly one `name.zh-CN.md` peer in the
same directory; the authoritative architecture overview uses the root
`ARCHITECTURE.md` / `ARCHITECTURE.zh-CN.md` pair. Both versions preserve the
same behavior, safety boundaries, examples, diagrams, and link destinations.
The homepage follows the same rule through the paired
`site/homepage-content.en.json` and `site/homepage-content.zh-CN.json` sources,
with one shared route map.

Synchronization and navigation checks fail when either side is missing or a
translation is orphaned. The site does not synthesize locale fallback pages.
Renamed or removed pages are deleted from the canonical route set and old URLs
return 404 instead of entering a compatibility redirect layer.

`site/translation-lock.json` binds each reviewed pair to the normalized hashes
of both sources. After updating and reviewing both languages, run
`npm --prefix site run translations:lock` and inspect the lock diff; refreshing
the lock without reviewing the translation is not completion evidence.

## Maintenance contract

When product behavior changes:

1. update the canonical owner page;
2. update its English or Simplified Chinese peer in the same change;
3. update any task guide whose outcome changed;
4. update architecture only when ownership, flow, or an invariant changed;
5. add the page to the shared site navigation manifest;
6. refresh the translation lock after both language versions are reviewed;
7. run the documentation structure, site type, production build, route, and
   anchor checks described in the [development guide](development/contracts-and-documentation.md).

Documentation examples are product contracts. Commands must be runnable,
configuration must validate against current models, and a claimed recovery path
must be backed by source or tests. Future behavior belongs only in the
[roadmap](roadmap.md).
