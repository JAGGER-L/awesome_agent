import { access, readFile, readdir } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  docsRedirects,
  docsSidebar,
  redirectsForBase,
  sidebarRoutes,
  translatedRoutes,
} from "../docs-navigation.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const outputDirectory = join(siteDirectory, "dist");
const configuredBasePath =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH;
const basePath = configuredBasePath.replace(/\/$/, "");
const origin = new URL(
  process.env.SITE_URL ?? "https://jagger-l.github.io",
).origin;

async function listHtmlFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listHtmlFiles(path)));
    if (entry.isFile() && entry.name.endsWith(".html")) files.push(path);
  }
  return files;
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function resolveOutputPath(pathname) {
  const withoutBase = pathname.slice(basePath.length).replace(/^\//, "");
  if (!withoutBase) return join(outputDirectory, "index.html");
  if (withoutBase.endsWith("/")) {
    return join(outputDirectory, withoutBase, "index.html");
  }
  if (extname(withoutBase)) return join(outputDirectory, withoutBase);

  const direct = join(outputDirectory, withoutBase);
  if (await exists(direct)) return direct;
  return join(outputDirectory, withoutBase, "index.html");
}

const htmlFiles = await listHtmlFiles(outputDirectory);
const failures = [];
let checkedLinks = 0;
let checkedAnchors = 0;
let checkedRedirects = 0;
let checkedDescriptions = 0;
let checkedUpdateDates = 0;
let checkedSeoContracts = 0;
const htmlCache = new Map();
const idCache = new Map();

async function htmlFor(path) {
  if (!htmlCache.has(path)) htmlCache.set(path, await readFile(path, "utf8"));
  return htmlCache.get(path);
}

function documentIds(html) {
  return new Set(
    [...html.matchAll(/\bid=(?:"([^"]+)"|'([^']+)')/g)].map(
      (match) => match[1] || match[2],
    ),
  );
}

async function idsFor(path) {
  if (!idCache.has(path)) idCache.set(path, documentIds(await htmlFor(path)));
  return idCache.get(path);
}

function tagsNamed(html, name) {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "gi"))].map(
    (match) => match[0],
  );
}

function attribute(tag, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = tag.match(
    new RegExp(`\\b${escaped}=(?:"([^"]*)"|'([^']*)')`, "i"),
  );
  return match?.[1] ?? match?.[2];
}

function linksWithRel(html, relation) {
  return tagsNamed(html, "link").filter(
    (tag) => attribute(tag, "rel") === relation,
  );
}

function metaWithName(html, name) {
  return tagsNamed(html, "meta").find(
    (tag) => attribute(tag, "name") === name,
  );
}

function absoluteRoute(route) {
  const suffix = route ? `/${route.replace(/^\/+|\/+$/g, "")}/` : "/";
  return new URL(`${basePath}${suffix}`, origin).href;
}

function validateLanguageSeo(html, page, canonicalRoute, translated) {
  const fallback = page.startsWith("zh-cn/") && !translated;
  const canonicalTags = linksWithRel(html, "canonical");
  const expectedCanonical = absoluteRoute(fallback ? canonicalRoute : page);
  if (
    canonicalTags.length !== 1 ||
    attribute(canonicalTags[0], "href") !== expectedCanonical
  ) {
    failures.push(
      `${page || "home"}: canonical must be ${expectedCanonical}`,
    );
  }

  const alternates = linksWithRel(html, "alternate");
  if (fallback) {
    if (alternates.length > 0) {
      failures.push(`${page}: fallback page must not publish language alternates`);
    }
    const robots = metaWithName(html, "robots");
    if (attribute(robots ?? "", "content") !== "noindex,follow") {
      failures.push(`${page}: fallback page must be noindex,follow`);
    }
  } else if (translated) {
    for (const language of ["en", "zh-CN", "x-default"]) {
      if (!alternates.some((tag) => attribute(tag, "hreflang") === language)) {
        failures.push(`${page || "home"}: missing ${language} language alternate`);
      }
    }
  } else if (
    alternates.some((tag) => attribute(tag, "hreflang") === "zh-CN")
  ) {
    failures.push(`${page}: untranslated English page advertises zh-CN alternate`);
  }
  checkedSeoContracts += 1;
}

const builtRedirects = redirectsForBase(configuredBasePath, docsRedirects);
for (const [source, expectedDestination] of Object.entries(builtRedirects)) {
  const sourcePath = join(
    outputDirectory,
    source.replace(/^\//, ""),
    "index.html",
  );
  if (!(await exists(sourcePath))) {
    failures.push(`missing legacy redirect page: ${source}`);
    continue;
  }
  const html = await htmlFor(sourcePath);
  const refresh = html.match(
    /<meta\s+http-equiv=(?:"refresh"|'refresh')\s+content=(?:"0;url=([^\"]+)"|'0;url=([^']+)')/i,
  );
  const actualDestination = refresh?.[1] || refresh?.[2];
  if (actualDestination !== expectedDestination) {
    failures.push(
      `${source}: redirect target ${actualDestination ?? "is missing"}; expected ${expectedDestination}`,
    );
    continue;
  }
  checkedRedirects += 1;
}

for (const route of sidebarRoutes(docsSidebar)) {
  for (const [localePrefix, updateLabel] of [
    ["", "Last updated:"],
    ["zh-cn/", "最近更新："],
  ]) {
    const page = `${localePrefix}${route}`;
    const htmlPath = join(outputDirectory, page, "index.html");
    if (!(await exists(htmlPath))) {
      failures.push(`${page}: missing canonical page for metadata checks`);
      continue;
    }
    const html = await htmlFor(htmlPath);
    const description = html.match(
      /<meta\s+name=(?:"description"|'description')\s+content=(?:"([^\"]*)"|'([^']*)')/i,
    );
    const descriptionText = description?.[1] ?? description?.[2];
    if (!descriptionText) {
      failures.push(`${page}: missing meta description`);
    } else if (
      descriptionText.length > 180 ||
      (descriptionText.length === 180 && /[\p{L}\p{N}]$/u.test(descriptionText))
    ) {
      failures.push(`${page}: meta description is overlong or cut mid-word`);
    } else {
      checkedDescriptions += 1;
    }

    if (!html.includes(updateLabel) || !/<time\s+datetime="[^"]+">/i.test(html)) {
      failures.push(`${page}: missing canonical-source last-updated date`);
    } else {
      checkedUpdateDates += 1;
    }

    validateLanguageSeo(html, page, route, translatedRoutes.has(route));
  }
}

for (const page of ["", "zh-cn/"]) {
  const htmlPath = join(outputDirectory, page, "index.html");
  validateLanguageSeo(await htmlFor(htmlPath), page.replace(/\/$/, ""), "", true);
}

const notFoundHtml = await htmlFor(join(outputDirectory, "404.html"));
if (linksWithRel(notFoundHtml, "canonical").length > 0) {
  failures.push("404.html: must not publish a canonical URL");
}
if (linksWithRel(notFoundHtml, "alternate").length > 0) {
  failures.push("404.html: must not publish language alternates");
}
if (attribute(metaWithName(notFoundHtml, "robots") ?? "", "content") !== "noindex,follow") {
  failures.push("404.html: must be noindex,follow");
}
checkedSeoContracts += 1;

const sitemapFiles = (await readdir(outputDirectory))
  .filter((name) => /^sitemap-\d+\.xml$/.test(name))
  .map((name) => join(outputDirectory, name));
const sitemap = (
  await Promise.all(sitemapFiles.map((path) => readFile(path, "utf8")))
).join("\n");
for (const route of sidebarRoutes(docsSidebar)) {
  const english = absoluteRoute(route);
  const chinese = absoluteRoute(`zh-cn/${route}`);
  if (!sitemap.includes(`<loc>${english}</loc>`)) {
    failures.push(`${route}: English route missing from sitemap`);
  }
  if (sitemap.includes(`<loc>${chinese}</loc>`) !== translatedRoutes.has(route)) {
    failures.push(`${route}: sitemap translation inventory is incorrect`);
  }
}
for (const route of ["", "zh-cn"]) {
  const url = absoluteRoute(route);
  if (!sitemap.includes(`<loc>${url}</loc>`)) {
    failures.push(`${route || "home"}: translated homepage missing from sitemap`);
  }
}

for (const htmlPath of htmlFiles) {
  const html = await htmlFor(htmlPath);
  const pagePath = relative(outputDirectory, htmlPath)
    .replace(/\\/g, "/")
    .replace(/index\.html$/, "");
  const pageUrl = new URL(`${basePath}/${pagePath}`, origin);

  for (const match of html.matchAll(/\b(?:href|src)=(?:"([^"]+)"|'([^']+)')/g)) {
    const value = match[1] || match[2];
    if (
      !value ||
      /^(?:data:|mailto:|tel:|javascript:)/i.test(value)
    ) {
      continue;
    }

    const url = new URL(value, pageUrl);
    if (url.origin !== origin) continue;
    checkedLinks += 1;

    if (!(url.pathname === basePath || url.pathname.startsWith(`${basePath}/`))) {
      failures.push(`${pagePath || "index.html"}: escapes Pages base path -> ${value}`);
      continue;
    }

    const targetPath = await resolveOutputPath(decodeURIComponent(url.pathname));
    if (!(await exists(targetPath))) {
      failures.push(`${pagePath || "index.html"}: missing -> ${value}`);
      continue;
    }

    if (url.hash && targetPath.endsWith(".html")) {
      checkedAnchors += 1;
      const fragment = decodeURIComponent(url.hash.slice(1));
      if (!(await idsFor(targetPath)).has(fragment)) {
        failures.push(
          `${pagePath || "index.html"}: missing anchor -> ${value}`,
        );
      }
    }
  }
}

if (failures.length > 0) {
  console.error(`Found ${failures.length} broken local link(s):`);
  for (const failure of [...new Set(failures)].sort()) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(
    `Checked ${checkedLinks} local links, ${checkedAnchors} anchors, and ` +
      `${checkedRedirects} legacy redirects; validated ${checkedDescriptions} descriptions ` +
      `${checkedUpdateDates} update dates, and ${checkedSeoContracts} SEO contracts ` +
      `across ${htmlFiles.length} HTML files.`,
  );
}
