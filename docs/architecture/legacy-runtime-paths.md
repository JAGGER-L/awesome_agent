# Legacy Runtime Paths

The legacy orchestration package `src/awesome_agent/orchestration/` has been
removed. Current durable execution must use `src/awesome_agent/runtime/` graph
routes and `src/awesome_agent/runtime/agent_loop/` middleware.

New runtime features must not import `awesome_agent.orchestration`.

Historical scoped team run rows may remain in storage as legacy data, but
current Workers do not execute the retired scoped route. Operators should
cancel and recreate non-terminal legacy rows through the current distributed
team runtime instead of migrating them implicitly.
