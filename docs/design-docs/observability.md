# Observability

The project does not use LangSmith.

Use OpenTelemetry traces and metrics, structured JSON logs, immutable runtime
events, and PostgreSQL projections. Local development initially uses a console
exporter.

Every event includes lineage fields such as:

```text
run_id
team_id
agent_id
parent_agent_id
task_id
trace_id
span_id
sequence
timestamp
event_type
status
```

The future frontend must inspect agent topology, conversations, mailbox
messages, model calls, tool progress/results, task revisions, approvals,
artifacts, verification loops, memory operations, token usage, latency, and
errors.

Model providers now expose generic reasoning-started and reasoning-delta events.
The future frontend displays only a generic `thinking` state and collapsible
reasoning content; it does not label the provider source. DeepSeek reasoning
content and OpenAI reasoning summaries share presentation but are not treated
as semantically identical.

Private continuation state, including encrypted provider continuation payloads,
is never an observability field.

Secrets, protected environment variables, and authorization headers are
redacted before logs, traces, database writes, or artifacts.
