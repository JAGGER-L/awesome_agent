# Legacy Runtime Paths

`src/awesome_agent/orchestration/` is a legacy compatibility area. Current
durable execution must use `src/awesome_agent/runtime/` graph routes and
`src/awesome_agent/runtime/agent_loop/` middleware.

New runtime features must not import `awesome_agent.orchestration`.

Allowed uses are limited to historical tests, migration references, and
compatibility code that is explicitly documented in the importing module.
