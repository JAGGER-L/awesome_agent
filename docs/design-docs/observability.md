# Observability

The project does not use LangSmith.

Use structured JSON logs, immutable runtime events, PostgreSQL projections, and
project-owned durable trace/metric query tables. OpenTelemetry integration is a
future exporter/instrumentation path, not the current source of production
Worker evidence.

## Current Solo Runtime Implementation

Task 12 implements solo-runtime observability without making observability a
new source of Run failure.

Durable query-table evidence:

- `observability_spans` stores run, graph, model, tool, and sandbox spans with
  trace/span IDs, status, timestamps, duration, bounded attributes, and error
  summaries.
- `observability_metrics` stores latency and counter-style metric points that
  can be recomputed from durable evidence if a best-effort write is missed.
- `model_calls` stores provider, model, turn number, status, stop reason, token
  usage, latency, trace/span IDs, and error summary.

Runtime event lineage:

- product-created and dispatcher-created runtime events receive a stable
  Run-scoped `trace_id` based on the Run UUID;
- graph-emitted model/tool events are projected into query tables by the
  Worker after the fenced runtime event append succeeds.

Telemetry isolation:

- Worker observability writes are best-effort and log failures without changing
  Run status;
- OpenTelemetry setup currently exists as an isolated local/exporter utility,
  but the Worker does not depend on OTel spans for production evidence;
- tool, approval, validation, artifact, and side-effect evidence remains
  durable execution evidence and is not weakened by the best-effort telemetry
  path.

FastAPI exposes:

```text
GET /runs/{run_id}/trace
GET /runs/{run_id}/metrics
GET /runs/{run_id}/model-calls
```

Full dashboards, Prometheus/Grafana export, production alerting, health-check
readiness, and budget enforcement remain separate roadmap work.

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
errors. The current solo-runtime API already exposes model calls, spans, and
metrics; team-runtime observability remains future work.

Model providers now expose generic reasoning-started and reasoning-delta events.
The future frontend displays only a generic `thinking` state and collapsible
reasoning content; it does not label the provider source. DeepSeek reasoning
content and OpenAI reasoning summaries share presentation but are not treated
as semantically identical.

Private continuation state, including encrypted provider continuation payloads,
is never an observability field.

Secrets, protected environment variables, and authorization headers are
redacted before logs, traces, database writes, or artifacts.
