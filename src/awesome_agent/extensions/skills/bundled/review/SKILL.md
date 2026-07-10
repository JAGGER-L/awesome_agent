---
name: review
description: Review code for correctness, risk, and maintainability
allowed-tools: [ls, read_file, glob, grep, execute]
---
# Review a change

Start from the requested behavior and inspect the changed call paths. Prioritize
correctness, data loss, security boundaries, concurrency, recovery, and missing
tests. Verify each finding against the current repository instead of inferring
from names or diff shape.

Report only actionable findings, ordered by severity, with tight file and line
references. Distinguish confirmed defects from residual risks. If no defect is
found, say so and identify the most important unverified area.
