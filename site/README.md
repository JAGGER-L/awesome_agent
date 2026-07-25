# Awesome documentation site

This Starlight site renders the repository's existing Markdown documentation.
The files under `docs/` and the root `ARCHITECTURE.md` remain the canonical
content source.

Node.js 22.12 or newer is required. The site self-hosts its Latin and Simplified
Chinese web fonts so the approved typography does not depend on a third-party
font CDN at runtime.

```text
npm ci
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

`docs-navigation.mjs` is the shared sidebar and legacy-redirect manifest.
`check:navigation` requires every canonical page to appear exactly once and
validates redirect targets and translation pairing. Its `translatedRoutes` set
distinguishes independently maintained Chinese pages from English-content
fallback routes. Fallback pages use the English canonical URL, publish no
language alternate, are `noindex,follow`, and stay out of the sitemap; the 404
page follows the same noindex principle without publishing a canonical URL.
`build` uses the production Pages origin/base by default and generates
`dist/llms.txt`; `check:links` then checks every built local route, anchor,
asset, redirect, canonical, language alternate, robots contract, and sitemap
entry. It treats absolute URLs on `SITE_URL` as local. Override `SITE_URL` and
`BASE_PATH` together only when testing another deployment target.
`check:contrast` protects the light/dark small-text palette at the WCAG AA
4.5:1 threshold.

Generated descriptions prefer one complete opening sentence and otherwise
truncate only at a safe boundary. Page update dates come from each canonical
source file's Git history, so CI and Pages checkouts must retain full history.
When Git history is unavailable in a local source archive, synchronization uses
the source file modification date as a display-only fallback.

Do not edit `src/content/docs/` or `dist/`: both are generated and ignored. Edit
repository Markdown, the homepage seed/component, the shared manifest, or the
sync/validation scripts instead.
