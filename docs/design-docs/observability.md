# Observability

The project does not use LangSmith.

Use structured JSON logs, immutable runtime events, PostgreSQL projections,
project-owned durable trace/metric query tables, and real OpenTelemetry spans.
Durable query tables remain the product source of truth; OTel spans are an
export/instrumentation path for operators and local debugging.

## Current Runtime Implementation

Task 23 adds real OTel spans for API, Worker, and migrated solo AgentLoop
paths without making observability a new source of Run or API failure. Task 24
extends the same AgentLoop observability boundary to the forward distributed
team routes.

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
- migrated solo and distributed team AgentLoop model/tool stages record
  `agent.run`, `model.call`, and `tool.call` through
  `ObservabilityMiddleware`;
- Worker-owned instrumentation records only outer `run.execute` and
  `graph.execute` boundaries;
- Worker event projection compatibility remains only for `team-coding-scoped`
  and other unmigrated routes, not for `team-coding`, `team-role`, or
  `team-verifier`.

Telemetry isolation:

- `ObservabilityFacade` is the single telemetry output boundary for durable
  query-table writes and OTel spans;
- Worker, API, and AgentLoop observability writes are best-effort and log
  failures without changing Run or HTTP results;
- API and Worker processes configure process-local OTel providers with
  `awesome.process_kind = api | worker`, console exporter defaults, and an OTLP
  endpoint hook;
- API endpoints are wrapped manually; automatic FastAPI instrumentation is not
  used in Task 23;
- tool, approval, validation, artifact, and side-effect evidence remains
  durable execution evidence and is not weakened by the best-effort telemetry
  path.

FastAPI exposes:

```text
GET /runs/{run_id}/trace
GET /runs/{run_id}/metrics
GET /runs/{run_id}/model-calls
```

Full dashboards, Prometheus/Grafana export, OTel metrics SDK integration,
production alerting, and money-cost budget enforcement remain separate roadmap
work.

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
errors. The current API exposes model calls, durable spans, and durable metrics
for solo and forward distributed team routes.

Model providers now expose generic reasoning-started and reasoning-delta events.
The future frontend displays only a generic `thinking` state and collapsible
reasoning content; it does not label the provider source. DeepSeek reasoning
content and OpenAI reasoning summaries share presentation but are not treated
as semantically identical.

Private continuation state, including encrypted provider continuation payloads,
is never an observability field.

Secrets, protected environment variables, and authorization headers are
redacted before logs, traces, database writes, or artifacts.
