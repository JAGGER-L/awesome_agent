---
id: superpowers-writing-plans
version: "1"
risk_level: low
compatible_actor_kinds: ["leader"]
requested_tools: ["repo.search", "repo.read", "repo.instructions"]
required_capabilities: ["repository:read"]
---

# Superpowers Writing Plans

Use this skill after a design direction is accepted and before implementation
starts. Produce a concrete execution plan with file boundaries, milestones,
tests, validation commands, acceptance criteria, and explicit non-goals.

Plans must preserve repository architecture invariants: durable coordination
stays in graphs, cross-cutting policy stays in middleware/hooks, tool authority
flows through the capability resolver, and extensions publish inventory rather
than permissions.

This skill does not grant write access or tool authority. Requested repository
read tools must still be granted by the assignment and capability policy.
