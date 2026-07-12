---
name: debug
description: Diagnose failures systematically from evidence
allowed-tools: [ls, read_file, glob, grep, execute, edit_file]
---
# Debug from evidence

Reproduce the smallest failing path and capture the exact symptom. Trace data and
control flow backward until the first violated invariant is identified. Form one
testable hypothesis at a time and prefer direct observations over broad edits.

Fix the root cause within scope, add or update a focused regression test, then
rerun the narrow validation gate. Record unrelated failures without changing
them unless they block diagnosis.
