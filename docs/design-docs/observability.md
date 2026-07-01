# Observability

The project does not use LangSmith.

Use structured JSON logs, immutable runtime events, PostgreSQL projections,
project-owned durable trace/metric query tables, real OpenTelemetry spans, and
OpenTelemetry metrics. Durable query tables remain the product source of truth;
OTel spans and metrics are export/instrumentation paths for operators and
local debugging.

## Current Runtime Implementation

Task 23 added real OTel spans for API, Worker, and migrated solo AgentLoop
paths without making observability a new source of Run or API failure. Task 24
extended the same AgentLoop observability boundary to the forward distributed
team routes. Task 36 made AgentLoop observability the primary production
instrumentation boundary for agent/model/tool metrics and added OTel metrics
SDK export alongside durable query-table metrics.

Durable query-table evidence:

- `observability_spans` stores run, graph, model, tool, and sandbox spans with
  trace/span IDs, status, timestamps, duration, bounded attributes, and error
  summaries.
- `observability_metrics` stores latency, token, and counter-style metric
  points that can be recomputed from durable evidence if a best-effort write is
  missed.
- `model_calls` stores provider, model, turn number, status, stop reason, token
  usage, latency, trace/span IDs, and error summary.

Runtime event lineage:

- product-created and dispatcher-created runtime events receive a stable
  Run-scoped `trace_id` based on the Run UUID;
- migrated solo and distributed team AgentLoop agent/model/tool stages record
  `agent.run`, `model.call`, and `tool.call` spans and matching
  count/latency/token metrics through `ObservabilityMiddleware`;
- Worker-owned instrumentation records only outer `run.execute` and
  `graph.execute` boundaries;
- Worker event projection compatibility remains only for `team-coding-scoped`
  and other unmigrated routes, not for `team-coding`, `team-role`, or
  `team-verifier`.

Telemetry isolation:

- `ObservabilityFacade` is the single telemetry output boundary for durable
  query-table writes, OTel spans, and OTel metrics;
- Worker, API, and AgentLoop observability writes are best-effort and log
  failures without changing Run or HTTP results;
- API and Worker processes configure process-local OTel providers with
  `awesome.process_kind = api | worker`, console exporter defaults, and an OTLP
  endpoint hook for traces and metrics;
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
GET /runs/{run_id}/diagnostics
```

`GET /runs/{run_id}/diagnostics` is a read-only operational projection over
runtime state, dispatch metadata, events, agents, token ledgers, model-call
summaries, tool invocation records, validation reports, team child evidence,
and observability query tables. It is not a new durable state machine and does
not own Run transitions. It exists so operators can diagnose a Run without
reading raw logs or joining every inspection endpoint by hand.

The diagnostics response is bounded and redacted. It reports identifiers,
statuses, token counts, hashes, summaries, artifact references, and boolean
error presence. It does not include raw prompts, secrets, provider
continuation payloads, full tool result content, or validation stdout/stderr.

Dashboard and alert definitions should use these dimensions:

- `runtime.route`
- `agent.id`
- `agent.role`
- `agent.kind`
- `team.root_run_id`
- `parent_run.id`
- `assignment.id`
- `team_operation`
- `model.provider`
- `model.name`
- `tool.name`
- `tool.call_id`
- `status`

Recommended production alerts:

- elevated `agent.run` failed-rate by `runtime.route`;
- elevated `model.call` failed-rate by provider and model;
- elevated p95 `model.call.latency_ms` or `tool.call.latency_ms`;
- sustained token pressure through `model.input_tokens`,
  `model.output_tokens`, and `model.reasoning_tokens`;
- repeated observability export failures in process logs.

Prometheus/Grafana export can be provided by an OTel collector configured from
the API and Worker OTLP endpoint. The built-in runtime only emits the OTel
metric stream and durable query-table evidence; dashboard storage remains an
operator deployment concern.

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

Runtime observability records token usage and latency. It does not record,
estimate, or enforce money, price, cost, currency, or USD budget fields.
