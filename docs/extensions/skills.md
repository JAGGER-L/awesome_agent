# Skills

A Skill is a named package of reusable instructions with optional text
resources. Skills are best for repeatable procedures such as review, debugging,
testing, or a repository-specific release checklist. They are context, not
executable plugins, and they cannot grant tool authority.

## Sources and precedence

Awesome discovers three sources after workspace trust:

| Source | Location | Typical owner | Session identity pinning |
| --- | --- | --- | --- |
| Bundled | inside the installed Python package | Awesome release | Yes |
| User | `<AWESOME_HOME>/skills/<name>/` | Current OS user | Yes |
| Workspace | `<workspace>/.awesome/skills/<name>/` | Repository/workspace | Yes, including the trusted workspace chain |

Discovery runs bundled, then user, then workspace. A later package with the
same name shadows the earlier package and produces a diagnostic, so effective
precedence is workspace > user > bundled. Disable a name in either user or
workspace configuration to remove that name from the effective catalog.

The current bundled catalog contains `debug`, `git-workflow`, `review`, and
`test`. Treat this as a release detail; `/skills` is the authoritative catalog
for the running installation.

## Create a Skill

Create a directory whose name matches the frontmatter `name`, then add
`SKILL.md`:

```text
<AWESOME_HOME>/skills/review-api/
|-- SKILL.md
`-- references/
    `-- checklist.md
```

A complete valid example is:

```markdown
---
name: review-api
description: Review an HTTP API change for compatibility and operational risk
allowed-tools: [ls, read_file, glob, grep]
license: MIT
compatibility: Awesome Agent 1.3.x
metadata:
  owner: platform-team
  maturity: stable
---
# Review an API change

Start from the public request and response contract. Inspect callers before
implementations. Report breaking behavior, authorization mistakes, error-shape
changes, missing cancellation, and missing tests.

Read `references/checklist.md` only when the change exposes an HTTP endpoint.
```

Supported frontmatter keys are exact; unknown fields invalidate the package.
The table gives the normative authoring types:

| Field | Required | Contract |
| --- | --- | --- |
| `name` | Yes | Lowercase letter followed by up to 63 lowercase letters, digits, or hyphens; exactly equals the directory name |
| `description` | Yes | 1–500 characters |
| `allowed-tools` | No | One string or a list of at most 128 unique tool names matching `[a-z][a-z0-9_.-]{0,199}`; descriptive metadata only |
| `license` | No | String, at most 500 characters |
| `compatibility` | No | String, at most 500 characters |
| `metadata` | No | Mapping whose values are JSON-compatible |

The current parser still normalizes several non-tool scalar fields with
`str()`, so a numeric description may be accepted as text. Do not rely on that
coercion; use strings as shown. `allowed-tools` is additionally bounded,
deduplicated, and name-validated so malformed metadata cannot inflate or
destabilize the automatic catalog.

The file must start with `---` YAML frontmatter. The YAML parser is bounded to
64 levels, 4,096 nodes, and 64 aliases; recursive aliases are invalid. `SKILL.md`
must be UTF-8, non-binary, and no larger than 1 MiB.

Keep the body imperative and testable: state when the Skill applies, required
inputs, ordered work, stop conditions, verification, and expected output. Put
large background material in resources so it is loaded only when needed.

## Select and load Skills

```text
/skills                 # inspect the catalog and choose a mode
/skills auto
/skills off
/skills review-api
```

The selection is stored on the current Thread and applies to future Turns. The
three modes have distinct, closed behavior:

| Mode | Frozen context | Model-visible Skill tools |
| --- | --- | --- |
| `auto` | A deterministic catalog of at most 64 effective Skills, bounded to 32 KiB and 4,096 estimated tokens | `load_skill`, `read_skill_resource` |
| `off` | No Skill catalog or body | None |
| `<name>` | Up to 5,000 estimated tokens from that Skill body as mandatory system context | `read_skill_resource` for that Skill only |

To keep Turn preparation bounded, `auto` considers only the first 256 effective
Skills after deterministic name sorting when selecting the final 64. The
catalog marks itself incomplete when later candidates exist or a candidate is
excluded by the byte or token limit.

`auto` lets the model choose from the bounded catalog; it does not silently run
a Skill. `off` is a runtime isolation boundary: even a forged call is rejected
by hard admission before permission policy or the handler. Tool argument
objects are strict and closed, so unknown fields and non-string scalar
substitutes are rejected as `invalid_arguments`.

```yaml
version: 2
skills:
  disabled:
    - review-api
```

The same `skills.disabled` field exists in trusted workspace configuration.
Disabled names from both scopes are combined. Changes are discovered when Core
starts; restart Awesome after adding, replacing, or removing a package.

## Internal loading chain

```text
discover directories
  -> parse bounded frontmatter
  -> resolve shadowing + disabled names
  -> pin package + SKILL.md identity
  -> immutable session catalog + diagnostics
  -> freeze selected identities in Turn context/checkpoint
  -> expose tools allowed by the frozen Skill mode
  -> strip frontmatter + 5,000-token bound
  -> Context Builder
```

`allowed-tools` is frozen and returned as diagnostic metadata when a Skill
loads. It does not filter the model's tool catalog and does not bypass or tighten
[permission policy](../reference/permission-modes.md). Put tool expectations in
the body, but enforce real authority at the Tool Executor boundary.

Skill reads use the built-in `context.read` capability. Permission modes allow
that capability without a prompt only after hard admission proves that the
requested package and operation occur in the Turn's frozen Skill scope. A
Runtime rebuild, recovery, or on-disk package change cannot widen that scope;
an identity mismatch returns `conflict`. Package changes take effect only in a
new session.

Resources use package-relative paths, reject absolute paths and `..`, and must
be UTF-8 non-binary text no larger than 1 MiB. `read_skill_resource` returns at
most 5,000 estimated tokens per call and reports whether truncation occurred.

## Skill identity and Workspace safety

Workspace packages are repository-controlled, so discovery starts from the
trusted workspace anchor and verifies every component of `.awesome/skills`, the
package directory, and `SKILL.md`. Symlinks, junctions, and other reparse points
are rejected before content is accepted.

Every catalog entry has a versioned identity derived from normalized descriptor
metadata and the discovered `SKILL.md` fingerprint and content. Later admission
and handler reads reopen the package without following links and require that
identity. Workspace entries additionally record the workspace anchor and every
directory identity through the package. Replacing a package or `SKILL.md`, or
replacing the workspace chain for a Workspace Skill, therefore fails closed.

A resource read revalidates the pinned chain and containment, rejects every
symlink/junction/reparse component below the package, and uses an
`lstat`/open/`fstat` identity check for the file being read. It does **not** pin
ordinary nested resource directories or resource-file contents at discovery
time. A safe non-reparse resource replacement completed before the read may be
observed; a nested reparse point or replacement during the checked open fails
closed. This distinction prevents path redirection and check/open races without
claiming that the complete package is an immutable content snapshot.

Bundled and User packages use the same package/`SKILL.md` identity requirement;
Workspace packages add the stronger trusted-anchor chain. All resource
traversal rejects escaping and symlink/reparse components.

## Diagnostics and recovery

One invalid package produces one catalog diagnostic and does not hide valid
packages. `/skills` reports `invalid_skill`, `unsafe_workspace_skill_path`,
`disabled`, and `shadowed` conditions with bounded source information.

When a Skill fails:

1. Run `/skills` and identify the source, effective name, and diagnostic.
2. Confirm the directory name equals frontmatter `name` and remove unknown
   fields.
3. Check UTF-8, size, and link/reparse boundaries.
4. Restart Awesome to create a fresh catalog snapshot.
5. Select the Skill explicitly and inspect `/context` to verify inclusion.

Use [Memory](memory.md) instead when content is a fact that should evolve
independently of a procedure. Use [MCP](mcp.md) only when the procedure needs a
new external capability rather than existing Awesome tools.
