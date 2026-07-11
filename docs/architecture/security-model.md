# Security Model

Awesome Agent is designed for trusted local project work. It should preserve
user control, make side effects visible, and avoid treating prompt instructions
as a security boundary.

## Trust Boundary

Trusted local execution runs as the same operating-system user. It uses command
classification, path checks, write-root checks, and environment scrubbing, but
it is not a strong isolation boundary.

Docker and sandbox modes can provide stronger process separation for selected
workflows, but they still need explicit configuration and operational checks.

## Secrets

Provider keys belong in operating-system environment variables or
`<AWESOME_HOME>/.env`, not in project `.env` files. Documentation, plans, pull
requests, and generated references must not include credentials or raw secret
values.

## Built-In Workspace Tools

The initial model-facing workspace tools are `ls`, `read_file`, `write_file`,
`edit_file`, `delete`, `glob`, `grep`, and `execute`. This is a starting
inventory, not a permanent tool ceiling.

File operations are bounded to the trusted workspace, reject path escape, and
reject symlink traversal. `execute` applies the same workspace and environment
policy before starting a local process. Trust is a workspace decision; tool
policy remains an executor invariant rather than a prompt convention.

## Related Documents

- [Documentation governance](../governance/documentation.md)
