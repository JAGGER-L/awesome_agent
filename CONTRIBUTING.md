# Contributing to Awesome

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

Thank you for improving Awesome. A contribution is complete only when its user
behavior, architecture boundaries, tests, documentation, and release impact
agree. Repository files are the source of truth; do not rely on an issue or
chat summary when the current code says otherwise.

## Start here

1. Read the [contributor guide](docs/development/README.md).
2. Follow [development setup](docs/development/setup.md).
3. Identify the owning package in [the architecture](ARCHITECTURE.md).
4. Choose the smallest validation set from [testing and CI](docs/development/testing.md).
5. Read the [contract guide](docs/development/contracts-and-documentation.md)
   before changing protocol, configuration, storage, commands, tools, or docs.

## Contribution contract

- Keep the Ink TUI as presentation and Python Core as the authority for model,
  tool, lifecycle, and persisted state behavior.
- Preserve unrelated work and avoid opportunistic refactors.
- Add a regression test for a defect before or with its fix. Do not add skips,
  expected failures, relaxed assertions, or compatibility shims to hide drift.
- Update both English and Simplified Chinese documentation for user-visible or
  architectural changes.
- Never commit credentials, private paths, generated caches, debug payloads, or
  copied production data.
- Record the commands and results that prove the change. State any unverified
  platform, provider, or release evidence explicitly.

Open a pull request with one coherent change. The PR template asks for user
impact, architectural reasoning, tests, documentation, and remaining risk.
Security vulnerabilities follow [the private reporting policy](SECURITY.md),
not a public issue.
