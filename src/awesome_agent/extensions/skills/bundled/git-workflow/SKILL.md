---
name: git-workflow
description: Prepare safe Git commits and workflow handoff
allowed-tools: [ls, read_file, glob, grep, execute]
---
# Prepare a Git change safely

Inspect branch, status, and diff before staging. Preserve unrelated user work and
group only one verified logical change per commit. Check for secrets, generated
noise, temporary files, and unintended deletions before publishing.

Use non-interactive Git commands. Summarize the change, validation evidence,
unverified risks, and target branch in the commit or pull-request handoff. Never
rewrite history or merge to a different target without explicit authorization.
