---
name: init
description: Initialize project guidance for this workspace
allowed-tools: [ls, read_file, glob, grep, write_file]
---
# Initialize workspace guidance

Inspect the repository's existing guidance, manifests, documentation, and source
layout before proposing changes. Produce concise project guidance that records
only durable commands, boundaries, and conventions verified from repository
files. Preserve existing user instructions and avoid speculative architecture.

When writing guidance, keep it small, actionable, and local to the directory it
governs. Do not add generated inventories or restate information that is already
obvious from the source tree.
