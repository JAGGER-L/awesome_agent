# Awesome documentation site

This Starlight site renders the repository's existing Markdown documentation.
The English/Simplified Chinese pairs under `docs/`, the root architecture pair,
and `homepage-content.en.json` / `homepage-content.zh-CN.json` remain the
canonical content sources.

Node.js 22.12 or newer is required. The site self-hosts its Latin and Simplified
Chinese web fonts so the approved typography does not depend on a third-party
font CDN at runtime.

```text
npm ci
npm run check:contracts
npm run dev
npm run check:navigation
npm run check:contrast
npm run check
npm run build
npm run check:links
```

`sync-content` runs automatically before development, checking, and building.
It creates ignored files under `src/content/docs/`, adds the frontmatter required
by Starlight, and keeps source edit links pointed at the canonical repository
files.

`documentation-catalog.mjs` compiles source identity, locale, route, and output
path before synchronization performs any write. It consumes the sidebar in
`docs-navigation.mjs`, rejects missing/orphaned pairs and source/route/output
collisions, and requires the sidebar and canonical source routes to be exact
sets. `translation-lock.json` records normalized English and Chinese SHA-256
identities for all 46 repository documentation and homepage-content pairs, so
changing either side requires an explicit translation review followed by
`npm run translations:lock`.
That command acknowledges the reviewed pair; it is not a substitute for
translation or review. The homepage schema fixes field parity, stable card IDs,
shared targets, and language completeness. Markdown discovery and generated-link
rewriting use the same AST; contract tests also preserve heading/fence structure,
executable examples, external URLs, inline identifiers, target-page pairs, and a
substantive Chinese prose ratio.

The site never publishes an English-content fallback under the Chinese locale.
It intentionally has no redirect manifest or legacy route layer: removed and
renamed routes return 404. Every canonical page publishes self-canonical
metadata plus `en`, `zh-CN`, and `x-default` alternates; the 404 page remains
`noindex,follow` and publishes neither a canonical URL nor language alternates.
`build` uses the production Pages origin/base by default and generates
`dist/llms.txt`; `check:links` then checks every built local route, anchor,
asset, canonical, language alternate, robots contract, bilingual `llms.txt`
entry, and sitemap entry. HTML must be the exact 86 canonical pages plus 404;
sitemap and llms links must each be the exact 86 canonical URLs. The checker
also rejects non-ordinary output nodes, generated redirects, encoded path
escapes, directories without an index, and real paths outside `dist`. HTML and
sitemap checks use semantic parsers, so markup hidden in comments or scripts is
not accepted as evidence. Generation checks every existing path component and
rejects symlinks, junctions, and reparse points before replacing or writing
generated content. Canonical inputs are limited to 1 MiB, strict UTF-8 without
NUL, and are read through `lstat`/containment checks, a no-follow open where the
platform exposes it, and matching `fstat` identity and metadata before and
after the bounded read. Generated pages are built in a complete sibling staging
tree; individual files use exclusive sibling temporaries, handle identity
checks, `fsync`, and rename-based installation. Cleanup never traverses an
identity that no longer matches the object that was inspected.

These checks are build-integrity circuit breakers, not an OS sandbox. Node does
not expose portable directory-handle-relative rename/unlink or atomic directory
exchange. The implementation therefore detects observed identity drift and
fails closed, but does not claim to make hostile same-user pathname races
impossible. A raced failure may deliberately retain a random temporary or
backup for inspection rather than deleting an object whose identity changed.
Absolute URLs on `SITE_URL` are local. Override `SITE_URL` and `BASE_PATH`
together only when testing another deployment target.
`check:contrast` protects the light/dark small-text palette at the WCAG AA
4.5:1 threshold.

Generated descriptions prefer one complete opening sentence and otherwise
truncate only at a safe boundary. Page update dates come from each canonical
source file's Git history, so CI and Pages checkouts must retain full history.
When Git history is unavailable in a local source archive, synchronization uses
the source file modification date as a display-only fallback.

Do not edit `src/content/docs/` or `dist/`: both are generated and ignored. Edit
repository Markdown, the paired homepage JSON sources, the shared manifest, or
the sync/validation scripts instead.
