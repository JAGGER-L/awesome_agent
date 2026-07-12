# CLI/TUI Redesign Plan Set

> **For agentic workers:** Execute the eight plans in order. Each plan is one reviewable PR. Use `superpowers:executing-plans` for inline execution or `superpowers:subagent-driven-development` for delegated execution.

**Goal:** Replace Awesome's fragmented terminal interaction paths with one Core-authoritative, single-focus, recoverable CLI/TUI architecture.

**Integration branch:** `codex/tui-interaction-redesign`

**Design authority:** `docs/superpowers/specs/2026-07-12-cli-tui-interaction-redesign.md`

## Ordered PRs

1. [Unified Input State Machine](2026-07-12-pr1-unified-input-state-machine.md)
2. [Workspace Trust Experience](2026-07-12-pr2-workspace-trust-experience.md)
3. [Permission and Approval Policy](2026-07-12-pr3-permission-approval-policy.md)
4. [Slash Commands and Thread Switching](2026-07-12-pr4-slash-command-thread-flow.md)
5. [Auth and Model Identity](2026-07-12-pr5-auth-model-identity.md)
6. [Transcript, Markdown, and Streaming](2026-07-12-pr6-transcript-markdown-streaming.md)
7. [Tool Timeline and Agent Stop Behavior](2026-07-12-pr7-tool-timeline-agent-stop.md)
8. [Visual System, Recovery, and Final Regression](2026-07-12-pr8-visual-recovery-regression.md)

## Merge Discipline

- Branch each PR from the updated `codex/tui-interaction-redesign` integration branch.
- Merge only after its targeted validation passes and the PR diff lists deleted legacy paths.
- PR3 changes permission and security boundaries and therefore requires explicit manual review before merge.
- Merge back to `codex/tui-interaction-redesign`, then start the next PR.
- Do not merge the integration branch to `main` until PR8 passes the new architecture's full validation suite and receives manual review; this redesign changes permission and security boundaries.
- Do not add protocol compatibility, temporary adapters, dual reducers, old-field aliases, or fallback parsers.
