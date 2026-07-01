---
id: superpowers-executing-plans
version: "1"
risk_level: low
compatible_actor_kinds: ["leader"]
requested_tools: ["repo.search", "repo.read", "repo.instructions"]
required_capabilities: ["repository:read"]
---

# Superpowers Executing Plans

Use this skill when an accepted implementation plan should be executed
milestone by milestone. Follow the written plan, keep work in scope, run
verification at each boundary, record evidence, and stop when a plan gap or
verification failure changes the design assumptions.

Execution must not weaken acceptance criteria, silently bypass capability
policy, or treat discovered extensions as authorized tools.

This skill does not grant write access or tool authority. Requested repository
read tools must still be granted by the assignment and capability policy.
