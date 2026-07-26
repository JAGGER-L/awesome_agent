# Security policy

[English](SECURITY.md) | [简体中文](SECURITY.zh-CN.md)

Awesome can read files, modify a trusted workspace, start local processes, and
call configured external services. A report is security-relevant when an
untrusted input or a broken boundary can cause behavior outside the authority a
user granted, disclose secrets or private data, corrupt durable state, bypass a
mandatory approval, or compromise the release supply chain.

## Supported versions

Security fixes target the latest published GitHub Release and the current
development branch. Older releases are not maintained as separate security
lines; users should upgrade to the newest fixed release.

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/JAGGER-L/awesome_agent/security/advisories/new).
Do not open a public issue, discussion, or pull request before coordinated
disclosure. Include:

- the affected release or commit and operating system;
- the violated trust, permission, protocol, filesystem, process, credential,
  storage, extension, or supply-chain boundary;
- a minimal reproduction using synthetic data;
- the observed impact and whether the action crossed a workspace or account
  boundary;
- any proposed mitigation, without including real secrets or private data.

The maintainer will acknowledge and triage reports as availability permits,
coordinate validation and a fix with the reporter, and agree on disclosure
after affected users can obtain a safe release. Do not test against systems,
accounts, repositories, or data you do not own or have permission to use.

## Security model and limits

Read [security and dependency boundaries](docs/architecture/security-and-dependencies.md)
and [permissions](docs/user-guide/permissions.md) before evaluating a report.
Permission prompts and the command circuit breaker reduce accidental authority;
they are not an operating-system sandbox or a detector for arbitrary hostile
obfuscation. Full access does not automatically approve MCP or unknown future
capabilities.

For non-sensitive defects, use the public bug-report template. Never attach API
keys, `.env` files, private transcripts, user-state databases, or raw provider
responses to a public report.
