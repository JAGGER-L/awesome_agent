# Contracts and documentation

Awesome uses executable contracts to keep Python Core, Ink, storage, packaging,
and documentation aligned. A contract is not only a public API: package
ownership, one graph compiler, the command inventory, schema identity, event
ordering, and documentation navigation are all intentionally checked.

## Contract hierarchy

When sources disagree, resolve them in this order:

1. current repository behavior and accepted product requirements;
2. strict source contracts and their focused tests;
3. the root `ARCHITECTURE.md` ownership map;
4. focused architecture pages;
5. user guides and reference pages;
6. chat, old plans, historical release notes, or external project conventions.

This order does not mean tests are immutable. A reviewed product change may
change a test, but the implementation, cross-boundary fixtures, documentation,
and migration/reset behavior must change coherently.

## Public-contract checklist

Treat a change as public or cross-boundary when it affects any of:

- CLI arguments, startup, workspace selection, or exit behavior;
- commands, interactions, approval choices, or error codes;
- configuration keys, environment variables, precedence, defaults, or limits;
- permission modes, hard denials, tool capabilities, or reversibility;
- model/provider catalog, stream events, usage, or retry semantics;
- Tool names, schemas, results, activity, timeouts, or cancellation;
- Protocol methods, payloads, events, optional/null behavior, or version;
- Thread/Turn records, Application schema, checkpoints, or reset boundary;
- Skills, MCP, Memory, or workspace instruction behavior;
- package dependencies, entry points, wheel/npm/bundle inventory;
- architecture ownership, dependency direction, or framework ownership;
- documentation routes, navigation, localization, or installation commands.

For each applicable item, identify the Python owner, TypeScript consumer,
persisted shape, recovery behavior, tests, docs, and release verifier.

## Structural contracts

`tests/structural/` checks architecture without running a full user flow. The
suite currently protects:

- exact Python package and storage module inventories;
- allowed internal import edges;
- owners of LangGraph, OpenAI SDK, MCP, jsonschema, and SQLite;
- one `StateGraph` compiler;
- `AgentState` fields and context invocation shape;
- Application facade, command dispatcher, and Thread replacement ownership;
- built-in tool capabilities and Change Journal independence;
- product version authority and direct dependencies;
- protocol/package/documentation inventories and Markdown links.

These tests turn high-value architecture decisions into immediate failures. If
you add a package, table, command, dependency, or graph field, update the test
only after explaining why the architecture changed.

## Protocol v4 change workflow

Protocol fixtures are the bidirectional Python/TypeScript evidence. To change a
method, result, event, command outcome, or projection:

1. Update the strict Python model and owning facade/method/event path.
2. Add valid boundary examples and invalid near-misses to the fixture generator.
3. Regenerate fixtures:

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py
   ```

4. Inspect `protocol/fixtures/v4/` and manifest hashes; never hand-edit them.
5. Update strict Zod schemas under `tui/src/protocol/`.
6. Update reducer/effect code for authoritative state changes.
7. Update exhaustive Presenter/components for visible facts.
8. Run:

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py --check
   uv run pytest -q tests/unit/protocol tests/e2e/test_stdio_product.py
   npm --prefix tui run typecheck
   npm --prefix tui test
   ```

Unknown fields remain errors. Optional and nullable remain distinct. Request
integers stay within the JSON/JavaScript safe range. A breaking wire contract
requires a new protocol version; a product version bump alone cannot prevent an
old same-version component from handshaking.

## Command contract

`CommandName` and `COMMAND_OWNERS` in `application/commands.py` are runtime
authority. The current catalog has 22 Application commands and four Ink-local
commands:

```text
Application:
  new rename resume context compact auth model thinking workspace
  diff undo redo tools skills mcp web memory status usage doctor config permissions

Ink:
  help theme copy quit
```

The full syntax and behavior live in the
[Slash Command reference](../reference/commands.md). Do not maintain a second
manual registry in contributor docs.

For a command change, verify:

- parser and completion insert only executable syntax;
- owner inventory matches Python fixtures and TypeScript catalog;
- Application commands have one dispatcher handler;
- no slash command submits a hidden Agent Turn;
- one discriminated result/interaction/error is returned;
- authoritative effects and pure presentation remain separate;
- citation values remain identical across tool result, Agent checkpoint,
  Conversation, fixture, TUI hydration, and headless JSON;
- empty, invalid, unavailable, busy, and interaction states are visible;
- foreground observation classification is explicit;
- Help, Presenter, transcript, and focused UI tests agree.

During an active Operation, only `/context`, `/workspace`, `/tools`, `/mcp`,
`/mcp status [id]`, `/status`, `/usage`, and `/config` are Core observations.
Changing that set is a concurrency-contract change and needs both race
directions in tests.

## Version and package contracts

`VERSION` is the only manually maintained product version. The Python package,
TUI package/lock/generated source, installers, release archive, and Protocol
fixture manifest must agree.

Run the package contract gates:

```powershell
node tui/scripts/sync-version.mjs --check
uv lock --check
uv build --wheel --no-build-isolation
npm --prefix tui test -- tests/packaging/package.test.ts
npm --prefix tui run build
npm pack ./tui --dry-run
```

The packaging test builds, packs, installs, and runs the tarball. The final
dry-run follows a fresh build and is only the inspectable contents view; by
itself it does not prove that `dist` is current or that the installed bin runs.

For an intentional release version, edit `VERSION`, run the version sync,
update both installers and release notes, then inspect all changes. Do not hide
version drift by relaxing packaging tests.

The wheel contract validates project identity, metadata, pure-Python tag,
entry points, RECORD hashes, required package members, and absence of editable
or development artifacts. The TUI package includes only `dist`, README, and
license. Release bundle verification installs the exact hashed dependency lock
plus wheel in an isolated environment.

## Documentation information architecture

The documentation is organized by reader intent rather than by source package:

```text
Start here -> Core concepts -> Use Awesome -> Extend Awesome
           -> Reference -> Architecture -> Contribute -> Project
```

- **Start here** gets a new user to one successful Turn.
- **Core concepts** supplies the mental model needed to predict behavior.
- **Use Awesome** gives task-oriented workflows and recovery guidance.
- **Extend Awesome** covers supported Memory, Skill, and MCP surfaces.
- **Reference** provides complete searchable syntax, schemas, and limits.
- **Architecture** explains ownership, implementation, failures, and tradeoffs.
- **Contribute** turns those contracts into development and release workflows.

The separation prevents a quickstart from becoming a source-build manual and
prevents a reference table from carrying design rationale it cannot explain.
Pages should link forward to the next reader task and sideways to concept,
reference, and architecture detail rather than repeat entire sections.

### Information architecture basis

The structure borrows patterns, not behavior, from current official Agent
documentation: Hermes uses an explicit learning path and separates user,
developer, and reference material in its [documentation](https://hermes-agent.nousresearch.com/docs/);
Codex groups practical workflows, configuration, customization, and layered
approval/security topics in its [best-practices guide](https://learn.chatgpt.com/guides/best-practices)
and [security documentation](https://learn.chatgpt.com/docs/agent-approvals-security);
Claude Code distinguishes concepts, task guides, reference, and the separate
roles of permissions and isolation in its [overview](https://code.claude.com/docs/en/overview)
and [permissions guide](https://code.claude.com/docs/en/permissions). These are
organizational inputs, not feature comparisons. Every statement about Awesome
must be verified against this repository's source, tests, configuration, and
release contracts.

## Canonical Markdown and site generation

Repository Markdown under `docs/` is canonical. `site/scripts/sync-content.mjs`
rebuilds Starlight content before check/build:

- `docs/README.md` is the repository documentation map and is excluded from
  site pages;
- a directory `README.md` becomes that directory's index route;
- every English source has one complete `name.zh-CN.md` counterpart, including
  directory `README.md` files; missing or orphaned translations fail sync and
  navigation checks instead of producing a fallback page;
- root `ARCHITECTURE.md` and `ARCHITECTURE.zh-CN.md` become the paired
  `architecture/overview` routes;
- source headings become generated title frontmatter;
- bounded whole-sentence descriptions, canonical-source update dates, and edit
  URLs are supplied when absent;
- relative Markdown links to `.md` files are rewritten for generated routes.

Do not edit `site/src/content/docs/`; it is generated. Edit canonical Markdown,
the seed homepage, navigation manifest, styles/components, or sync scripts as
appropriate.

`site/documentation-catalog.mjs` compiles every source into one source identity,
locale, canonical route, and output path before synchronization writes generated
content. It consumes `site/docs-navigation.mjs`, requires sidebar routes and
canonical English sources to be exact sets, and rejects source, route, or output
collisions. Every English source, including the repository-only root `README.md`
and `docs/README.md`, must have one complete Chinese counterpart with no orphan
in either language.

`site/translation-lock.json` records normalized SHA-256 identities for all 46
English/Chinese repository documentation and homepage-content pairs. After both
sources have been translated and reviewed, run
`npm --prefix site run translations:lock` and
inspect the lock diff. Changing an English or Chinese source without updating
the reviewed pair leaves a stale hash and stops synchronization before it
replaces generated content. Updating the lock alone is not translation evidence;
the same contract also checks prose language, structure, executable fences,
external URLs, inline identifiers, and matching same-locale target-page pairs.
Homepage JSON has its own strict schema, stable IDs, shared route map, structural
parity, and language-completeness checks.

When a page is merged or renamed, update the canonical route directly. Awesome
does not preserve documentation route aliases or redirects; old and
non-canonical URLs return 404 by design.

After a successful site build, `site/scripts/generate-llms.mjs` derives
`dist/llms.txt` from that same navigation manifest and each canonical page's H1.
It publishes the ordered documentation map with base-aware public URLs; it is
generated output, not a second hand-maintained index.

The built-site checker derives an exact contract from that same route set: 86
canonical HTML pages plus one 404, exactly 86 sitemap URLs, and exactly 86
Markdown links in `llms.txt`. Extra pages, duplicate URLs, redirect pages,
encoded path escapes, directories without an index, non-ordinary output nodes,
and real paths outside `dist` all fail the check. Markdown link discovery and
rewriting share one AST. Built HTML and sitemap evidence is parsed semantically,
not counted with comment-sensitive regular expressions. Before replacing or
writing generated content, every existing path component is checked and any
symlink, junction, or reparse point is rejected.

Canonical inputs are capped at 1 MiB and must be strict UTF-8 without NUL. The
reader validates the repository and every path component, uses a no-follow open
where the platform exposes it, binds the open handle to the preceding `lstat`
identity with `fstat`, and repeats identity and metadata checks after the
bounded read. Output files use exclusive random sibling temporaries, handle
identity checks, `fsync`, and rename-based installation. The complete generated
documentation tree is populated in a sibling staging directory before an
identity-bound rename/backup swap, so a rendering failure leaves the prior tree
in place. Cleanup refuses to traverse any object whose identity changed.

This is a fail-closed build-integrity boundary, not OS isolation. Node has no
portable directory-handle-relative rename/unlink or atomic directory exchange,
so the code does not claim to eliminate a hostile same-user race in the final
pathname syscall. Observed drift aborts the build; a random temporary or backup
may be retained for inspection instead of risking cleanup of an unknown object.

Starlight renders generated files, so its default Git lookup cannot recover the
history of canonical Markdown. Synchronization therefore reads the last commit
date of each source file and injects `lastUpdated`; the docs CI and Pages jobs
use full-history checkout. A local source archive without `.git` falls back to
the source file's modification date.

## Page contract

A durable page should answer, as relevant:

1. What problem does this concept or workflow solve?
2. What is the smallest correct path for the reader?
3. Why is it designed this way?
4. Which component owns it internally?
5. What inputs, outputs, limits, and defaults are exact?
6. What happens on error, timeout, cancellation, concurrency, and recovery?
7. What security boundary does it not provide?
8. Which alternatives or tradeoffs matter?
9. Where should the reader go next?
10. Which source and tests prove an architecture claim?

Not every page needs every section. Quickstarts should optimize for first
success; reference pages should optimize for completeness and search; deep
architecture pages should include ownership, data flow, failure semantics,
tradeoffs, and source/test maps.

## Writing conventions

- Use the exact product names Workspace, Thread, Turn, Operation, ChangeSet,
  Skill, Memory, MCP, Core, and TUI consistently.
- Describe current behavior in present tense and roadmap behavior explicitly as
  future work.
- Prefer exact commands and bounded examples that can be copied.
- Explain safe defaults and destructive consequences before the action.
- Separate approval/policy from sandbox/isolation claims.
- Use portable text diagrams when sequence, ownership, or branching is easier
  to understand visually. If a rendered diagram format is introduced, add its
  pinned build-time renderer and site validation in the same change; do not
  publish an unrendered diagram language as a code sample by accident.
- Keep diagrams small enough for terminal/GitHub and Pages reading.
- Use relative `.md` links between canonical docs and backticks for source/test
  paths; generated-site synchronization rewrites the former.
- Link to the owning reference instead of copying long tables into several
  pages.
- Avoid hard-coded historical version narratives in evergreen guides.
- Keep every English and Chinese page behaviorally aligned; both languages are
  required canonical documentation, not fallback variants.

## Documentation validation

Run repository and site contracts:

```powershell
git diff --check
uv run pytest -q tests/structural/test_markdown_links.py
npm ci --prefix site
npm --prefix site run check:contracts
npm --prefix site run check:navigation
npm --prefix site run check:contrast
npm --prefix site run check
$env:SITE_URL = "https://jagger-l.github.io"
$env:BASE_PATH = "/awesome_agent"
npm --prefix site run build
npm --prefix site run check:links
Remove-Item Env:SITE_URL, Env:BASE_PATH -ErrorAction SilentlyContinue
```

The source link test catches missing files. Catalog and navigation validation
catch stale translation hashes, language/identifier drift, orphans, duplicates,
collisions, nonexistent routes, and unpaired translations. The theme contrast
check protects small-text foregrounds in both palettes at WCAG AA. Astro checks
generated frontmatter/content. A production-base build and exact built-site
scan catch route, anchor, locale-pair, output-containment, sitemap, llms, and
asset errors that source-only checks miss.

For a documentation change, also search for superseded names and links:

```powershell
rg -n "old-page-name|old-command|old-config-key" README.md README.zh-CN.md `
  ARCHITECTURE.md AGENTS.md docs site tests
```

## Documentation review checklist

- [ ] Claim verified against current code/tests, not chat or competitor docs.
- [ ] User path, design reason, internal owner, limits, and failures are clear.
- [ ] Examples use current commands/configuration and safe placeholders.
- [ ] Destructive or external effects state recovery limits.
- [ ] New/moved/deleted page is reflected in both documentation indexes and navigation.
- [ ] Every English source has one complete Chinese counterpart and neither locale has an orphan.
- [ ] Incoming/outgoing links and next-step reading path remain coherent.
- [ ] Root README English/Chinese and `AGENTS.md` map are updated when affected.
- [ ] Root architecture remains authoritative; focused pages do not redefine it.
- [ ] No generated site content or local build output is committed.
- [ ] Source links, navigation, Astro, production build, and built links pass.
