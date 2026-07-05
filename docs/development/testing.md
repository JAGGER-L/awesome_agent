# Testing

Validation should match risk. Documentation-only changes usually need markdown
link checks and affected structural tests. Runtime, API, CLI, persistence, or
startup changes need broader validation.

## Common Gates

```powershell
.\.venv\Scripts\python.exe -m pytest --no-cov tests/structural
git diff --check
```

Use targeted unit, integration, startup, or E2E checks when code behavior
changes. If a validation gate is unavailable, record it as unavailable rather
than silently skipping it.

## Evidence

Record commands, results, unresolved risks, and follow-up items in the active
plan, handoff note, pull request, or final response.
