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

Approval resume for the original interrupted tool call is exact-bound. The
runtime must revalidate arguments, tool version, workspace fingerprint, and
requested capabilities before that side effect continues.

Approval is not a blanket session grant. A prior decision may be reused only as
a bounded grant inside the same run when the tool name, tool version, workspace
path, requested capabilities, and risk level still match. The supported grant
resources are:

- `shell.execute`: the exact normalized `argv`
- `repo.apply_patch`: the exact set of patch target paths

Different commands, different patch paths, expired decisions, changed
capabilities, or changed tool risk must request a new approval. Reuse is
recorded as `approval.reused` so product surfaces can distinguish a new approval
prompt from an already-decided scope.

## Related Documents

- [Tool capabilities](tool-capabilities.md)
- [Operations troubleshooting](../operations/troubleshooting.md)
- [Documentation governance](../governance/documentation.md)
