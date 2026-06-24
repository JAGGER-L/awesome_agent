# Reliability

V1 reliability requirements:

- interrupted runs resume from PostgreSQL checkpoints
- runtime events retain deterministic sequence order
- Mem0 outages do not fail runs
- tool and model calls support timeout and cancellation
- sandbox termination does not corrupt the target repository
- task and agent state transitions are validated
- SSE clients can reconnect from an event cursor
- failures record cause, retry target, and remaining uncertainty

Retries must be bounded and evidence-driven. Silent infinite retries are
forbidden.

