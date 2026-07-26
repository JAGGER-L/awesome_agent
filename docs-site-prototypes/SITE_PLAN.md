# Awesome documentation site proposal

> **Status:** Historical design record. The production implementation under
> `site/` and the paired sources under `docs/` define the current contract. This
> proposal is not authoritative when it differs from those sources.

## Product goal

The site should help a first-time visitor reach a successful first session quickly,
then remain useful as a precise reference for experienced users and contributors.
The invariant is not "publish every Markdown file"; it is "make the next correct
action obvious, searchable, and trustworthy."

## Historical stack rationale

Use **Astro Starlight** as the documentation framework and deploy its static output
to **GitHub Pages through GitHub Actions**.

Why Starlight fits this project:

- built-in documentation navigation, table of contents, accessibility, dark mode,
  and Pagefind full-text search;
- built-in multilingual routing, used here with complete paired English and
  Simplified Chinese pages rather than untranslated locale fallback;
- Markdown/MDX authoring with room for richer product components later;
- static output, so the site has no runtime service or product-state boundary;
- visual customization is deep enough to carry Awesome's terminal identity without
  rebuilding documentation primitives.

Use VitePress instead only if minimizing the initial file migration and dependency
surface is more important than richer site composition.
Do not introduce documentation versioning yet; add it only after users must support
multiple released behavior contracts simultaneously.

## Proposed repository shape

```text
site/
  astro.config.mjs
  package.json
  public/
  src/
    assets/
    components/
    content/docs/
      en/
      zh-cn/
    styles/
.github/workflows/docs-pages.yml
```

During implementation, migrate content in one explicit pass and update root README
links. Do not keep two independently edited copies of the same page.

## Information architecture

```text
Home
├─ Get started
│  ├─ Install
│  ├─ First session
│  └─ Model setup
├─ Use Awesome
│  ├─ Commands
│  ├─ Workspace and tools
│  ├─ Permissions and safety
│  ├─ Configuration
│  ├─ Memory, Skills, and MCP
│  └─ Troubleshooting
├─ Understand Awesome
│  ├─ Architecture overview
│  ├─ Application and Agent
│  ├─ Protocol and Ink
│  ├─ Storage and recovery
│  └─ Security model
├─ Contribute
│  ├─ Development setup
│  ├─ Testing
│  └─ Release
└─ Roadmap
```

Primary user paths:

1. Landing page -> install command -> first task -> provider setup.
2. Search or command index -> exact syntax -> related configuration/troubleshooting.
3. Architecture overview -> subsystem guide -> source files -> testing guidance.

## Content rules

- English is the default/root locale; Simplified Chinese lives at `/zh-cn/`.
- Every public page has an English and Simplified Chinese peer; a missing
  translation is a build error, with no untranslated locale fallback.
- Every page starts with a one-sentence outcome and ends with a useful next step.
- Commands and configuration keys have stable anchors and copy buttons.
- User guides explain normal flow, failure, cancellation, and recovery.
- Architecture diagrams describe ownership and dependency direction, not decoration.
- Root READMEs remain concise product front doors and link to the canonical site.

## GitHub Pages delivery

- Build on pull requests to catch broken links and static-build failures.
- Deploy only from `main` through the `github-pages` environment.
- Set the repository subpath as the build base (for example `/awesome_agent/`).
- Upload the generated static directory as a Pages artifact; do not commit build
  output or maintain a `gh-pages` branch.
- Add a custom domain later without coupling content URLs to it.

## Rollout

1. Choose a visual direction from the prototypes.
2. Scaffold Starlight and encode the chosen tokens/components.
3. Migrate the current Markdown, normalize links, and add frontmatter.
4. Add navigation, Pagefind search, bilingual routing, and 404 handling without
   legacy route redirects.
5. Add the Pages workflow and verify the repository subpath build locally.
6. Enable GitHub Pages, publish, then update README links.

## Acceptance criteria

- A new user can find, install, launch, and complete the first task from the home
  page without searching.
- All current documentation has one canonical rendered URL.
- Search finds command names, config keys, provider names, and error terminology.
- Keyboard, touch, mobile navigation, light/dark modes, and code copy are usable.
- English and Chinese routes do not produce broken cross-language links.
- Pull requests verify the static build and internal links before deployment.
