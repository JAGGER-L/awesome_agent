# DeepSeek, Memory, and Durable API Projections

Status: completed

Owner: project owner

Last updated: 2026-06-25

## Objective

Make DeepSeek the default model provider, assign configurable model defaults to
the Leader and every agent role, enable both memory layers in the local
environment, make API read models durable in PostgreSQL, and add a Chinese
README that must remain synchronized with the English README.

## Confirmed Decisions

- Preserve the existing observability/httpx2 change in its own commit.
- DeepSeek base URL: `https://api.deepseek.com`.
- Pro model: `deepseek-v4-pro`.
- Flash model: `deepseek-v4-flash`.
- Thinking remains enabled by default.
- Leader defaults to Pro.
- Every Teammate, Verifier, and Subagent defaults to Flash.
- Role model assignments are configurable.
- Built-in memory and Mem0 default to disabled in committed configuration.
- The current local `.env` enables both memory layers.
- DeepSeek and Mem0 credentials are stored only in local `.env`.
- PostgreSQL is the authoritative source for API Run/Agent/Todo/Event reads.
- In-memory state is allowed only as an explicit test adapter.
- English and Chinese READMEs must be updated together.
- Real smoke tests are authorized for DeepSeek Pro, DeepSeek Flash, and a
  temporary Mem0 write/search/delete cycle.

## Existing Local PostgreSQL

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

## Milestones

### 1. Provider and Role Model Configuration

- add DeepSeek Chat Completions adapter
- add provider/model settings
- add configurable role-to-model resolver
- bind Leader to Pro and all other profiles/Subagents to Flash by default
- preserve project-owned provider interfaces

Validation:

- provider request/response mapping tests
- role model default and override tests
- no credential values in tracked files

### 2. Memory Local Configuration

- update `.env.example` with non-secret memory/provider settings
- create ignored local `.env` with supplied credentials
- enable built-in memory and Mem0 only in local `.env`
- add Mem0 delete support for smoke-test cleanup

Validation:

- `.env` is ignored and untracked
- settings load both memory switches as enabled locally
- memory defaults remain disabled without `.env`

### 3. Resolve TD-004

- introduce a runtime repository port
- implement PostgreSQL and explicit in-memory adapters
- remove authoritative Run/Agent/Todo/Event dictionaries from RuntimeService
- initialize PostgreSQL resources through FastAPI lifespan
- query PostgreSQL for API reads and event history
- retain EventStream only for live SSE subscribers

Validation:

- create data with one service instance
- recreate service and read the same Run/Agent/Event data from PostgreSQL
- migrations remain reversible
- API tests use explicit in-memory repository

### 4. Bilingual Documentation and Smoke Tests

- add `README.zh-CN.md`
- add language links to both READMEs
- add synchronization rule to `AGENTS.md`
- update provider, agent-team, memory, architecture, security, and product docs
- close TD-004 with evidence
- run authorized external smoke tests

Validation:

- all local Markdown links resolve
- English/Chinese README structure check passes
- DeepSeek Pro and Flash return non-empty responses
- Mem0 temporary memory is written, found, and deleted
- full `scripts/check.ps1` and `scripts/system-test.ps1` pass

## Non-Goals

- adding a second active model provider
- production deployment
- storing credentials in PostgreSQL
- storing full source, full conversations, or raw tool output in Mem0
- changing the Team/Subagent ownership hierarchy

## Handoff

- Current milestone: complete
- Current scope: none
- Completed: existing user changes committed as `d5a71da`
- Completed: DeepSeek provider, per-role model resolution and persisted Agent
  model assignments
- Completed: local memory configuration and Mem0 add/search/delete adapter
- Completed: PostgreSQL-backed Run/Agent/Todo/Event projections; TD-004 closed
- Completed: bilingual README and synchronization structural test
- Validation: `scripts/check.ps1` passed 60 tests with 83.47% coverage
- Validation: `scripts/system-test.ps1` passed 8 integration/E2E tests and
  FastAPI startup
- Validation: real DeepSeek Pro, DeepSeek Flash, and Mem0 write/search/delete
  smoke tests passed; temporary Mem0 records were deleted
- Blockers: none
- Next action: select the next execution plan
