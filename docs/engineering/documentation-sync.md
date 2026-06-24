# Documentation Synchronization

Code and documentation are one change. A development agent must identify
documentation impact before editing and update the mapped documents before the
change is complete.

## Impact Matrix

| Changed area | Required documentation review |
| --- | --- |
| `src/awesome_agent/api/`, CLI, user workflow | both READMEs, product spec, architecture |
| `src/awesome_agent/orchestration/`, agents | agent-team, task/verification, runtime harness |
| providers or model settings | both READMEs, architecture, agent-team design |
| memory | memory design, security, reliability |
| tools, approvals, sandbox | security, reliability, runtime harness |
| persistence or migrations | architecture, reliability, generated DB schema |
| observability or events | observability design, architecture |
| configuration or environment variables | `.env.example`, both READMEs, security when secret-related |
| repository harness or checks | `AGENTS.md`, engineering harness, this matrix |
| durable architectural decision | `DECISIONS.md` and affected design |

The requirement is to review all mapped documents and modify those whose
statements or contracts changed. A local execution plan must contain one of:

```text
Documentation Impact: <documents and intended changes>
```

or:

```text
Documentation Impact: none
Reason: <why behavior, interfaces, operations, and contracts are unchanged>
```

## Machine Enforcement

`scripts/check_docs_sync.py` evaluates changed files against this matrix.
`scripts/check.ps1` runs it locally. GitHub Actions runs it against the pull
request or push diff.

The `documentation-sync` GitHub check is intended to be required on `main`.
GitHub rejected branch-protection configuration for the current private
repository because the account does not have GitHub Pro. Until the repository
becomes public or the account is upgraded, the check runs on every PR and push
but cannot prevent an administrator from bypassing it. This limitation is
tracked as TD-005.

The checker is deliberately conservative. Passing it does not prove that prose
is correct; review must still compare documentation claims with implementation.
