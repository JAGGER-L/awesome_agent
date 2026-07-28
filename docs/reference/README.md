# Reference

Reference pages define Awesome's current, exact public contracts. Use them to
look up a field, default, limit, command, path, or wire shape. For task-oriented
guidance, start with the [user guide](../user-guide/README.md); for ownership and
design rationale, use the [architecture documentation](../architecture/README.md).

| Reference | Canonical facts |
| --- | --- |
| [CLI and keyboard](cli.md) | Supported launch flags, terminal requirements, input classification, editor keys, and exit behavior |
| [Slash Commands](commands.md) | Complete command catalog, grammar, ownership, and foreground admission rules |
| [Configuration](configuration.md) | YAML documents, every field and default, source precedence, credentials, and environment variables |
| [Built-in tools](built-in-tools.md) | Tool names, capabilities, argument fields, limits, output, and conditional support tools |
| [Permission modes](permission-modes.md) | Exact three-mode matrix, one-call and Thread grants, hard denials, and Full access confirmation |
| [Files and state](files-and-state.md) | User/workspace paths, SQLite ownership, schema behavior, locks, backup, and reset boundaries |
| [Protocol v5](protocol.md) | Private stdio JSON-RPC methods, events, errors, handshake, and fixtures |

## How to read a Reference page

The tables describe implemented behavior in the current source tree. Examples
show valid shapes but use placeholder IDs, package names, and credentials. A
value labeled **internal** is recorded to explain state or wire behavior and is
not a supported user editing surface.

Canonical facts appear here once. Narrative pages link here instead of copying
field lists and limits. When source changes a public contract, update the
corresponding Reference page, focused user guide, protocol fixture if relevant,
and tests in the same change.

## Versioning boundaries

Awesome has several independent versions:

- the product version in `VERSION` and package metadata;
- private Core/TUI Protocol version `4`;
- event envelope version `1`;
- Application diagnostic log record version `1`;
- Application SQLite schema version `8`, with migration floor `7`;
- user configuration version `2`, with readable versions `{1, 2}`;
- workspace configuration version `1`, with readable versions `{1}`;
- UI preferences schema version `1`, with readable versions `{1}`;
- headless JSON result version `2`;
- Thread export version `1`, shared by its JSON document and Markdown marker.

This catalog covers cross-process wire contracts, user-editable persisted
formats, the Application database, public machine-readable outputs, and the
documented structured log. Same-release recovery markers and other internal
implementation records are intentionally excluded.

Matching one does not imply compatibility in another. In particular, the
private protocol handshake requires both protocol v5 and the exact installed
product version so independently upgraded Core and TUI components fail clearly.
`contract-versions.json` is the machine-readable catalog for the contract
identifiers; generated Python and TypeScript constants keep runtime code free of
JSON loading. A release combines that catalog with `VERSION` in its generated
`compatibility.json`. It does not make the numbers equal or increment every
format for every product release.

The catalog document and bundled compatibility manifest each currently use
envelope version `1`. Those metadata versions govern their own document shapes;
they are not additional entries in the runtime contract tuple above.
