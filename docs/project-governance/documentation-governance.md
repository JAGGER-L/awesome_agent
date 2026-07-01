# Documentation Governance

## Purpose

This document defines where project information belongs. It keeps public
product docs, durable architecture contracts, local execution plans, and
roadmap governance from drifting into each other.

## Reader Classes

| Reader | Needs | Entry point |
| --- | --- | --- |
| User | Understand and try the project | `README.md` |
| Operator | Run, inspect, and diagnose local runtime | `docs/operations/README.md` |
| Contributor | Modify the repository safely | `docs/engineering/README.md` |
| Architect | Understand durable runtime contracts | `docs/design-docs/index.md` |
| Maintainer | Govern roadmap, debt, and documentation | `docs/project-governance/README.md` |

## File Boundaries

| Location | Owns | Must not contain |
| --- | --- | --- |
| `README.md` / `README.zh-CN.md` | User-facing intro, quick start, feature overview, docs map | Task history, local execution evidence, internal planning |
| `docs/README.md` | Documentation map by reader task | Runtime architecture details duplicated from design docs |
| `docs/getting-started/` | Manual first-run path | Full troubleshooting matrix |
| `docs/user-guide/` | Product surface usage | Internal roadmap governance |
| `docs/operations/` | Local runtime operation and diagnostics | Product marketing copy |
| `docs/design-docs/` | Durable architecture contracts | Local session handoffs |
| `docs/engineering/` | Repository modification rules | Runtime-agent behavior |
| `docs/project-governance/` | Roadmap, debt, documentation rules | Per-branch execution plans |
| `docs/generated/` | Generated references | Hand-authored decisions |

## README Rules

README files must stay short enough for a new reader to decide whether to try
the project and complete the first local setup path. English and Chinese
READMEs must be updated together in the same change.

README files may summarize implemented capabilities, but detailed design
contracts belong in `docs/design-docs/`, and sequencing/history belongs in the
runtime roadmap or archive.

## Update Procedure

Before changing docs, identify the reader, canonical file, and links that must
be updated. After changing docs, run markdown link and bilingual README
structural tests.

If a change modifies behavior, configuration, startup, security, or runtime
boundaries, update the relevant design, operations, or user guide document in
the same branch.
