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
npm run check
npm run build
```

`sync-content` runs automatically before development, checking, and building.
It creates ignored files under `src/content/docs/`, adds the frontmatter required
by Starlight, and keeps source edit links pointed at the canonical repository
files.
