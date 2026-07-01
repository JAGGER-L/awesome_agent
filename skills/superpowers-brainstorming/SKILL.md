---
id: superpowers-brainstorming
version: "1"
risk_level: low
compatible_actor_kinds: ["leader"]
requested_tools: ["repo.search", "repo.read", "repo.instructions"]
required_capabilities: ["repository:read"]
---

# Superpowers Brainstorming

Use this skill when a request changes product behavior, architecture, runtime
policy, or extension behavior and the safest next step is design clarification.

First inspect the repository context and relevant design docs. Then identify
the smallest set of decisions that materially changes the architecture. Present
the recommended direction, alternatives, trade-offs, and risks before planning
implementation.

This skill does not grant write access or tool authority. Requested repository
read tools must still be granted by the assignment and capability policy.
