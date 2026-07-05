# Security Model

Awesome Agent is designed for trusted local project work. It should preserve
user control, make side effects visible, and avoid treating prompt instructions
as a security boundary.

## Trust Boundary

Trusted local execution runs as the same operating-system user. It uses command
classification, path checks, write-root checks, environment scrubbing, and
approval gates, but it is not a strong isolation boundary.

Docker and sandbox modes can provide stronger process separation for selected
workflows, but they still need explicit configuration and operational checks.

## Secrets

Provider keys belong in operating-system environment variables or
`<AWESOME_HOME>/.env`, not in project `.env` files. Documentation, plans, pull
requests, and generated references must not include credentials or raw secret
values.

## Approval

Approval is scoped to the exact canonical tool invocation. A resumed operation
must revalidate arguments, tool version, workspace fingerprint, and requested
capabilities before side effects continue.

Approval is not a blanket session grant. It is scoped to tool name, tool
version, canonical arguments, workspace path, workspace fingerprint, requested
capabilities, and run context.

## Related Documents

- [Tool capabilities](tool-capabilities.md)
- [Operations troubleshooting](../operations/troubleshooting.md)
- [Documentation governance](../governance/documentation.md)
