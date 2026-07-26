import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  docsSidebar,
  sidebarRoutes,
} from "../docs-navigation.mjs";
import {
  BuiltSiteContractError,
  buildExpectedSiteContract,
  exactCollectionFailures,
  extractMarkdownLinkTargets,
  normalizeBasePath,
  publicUrlForFile,
  resolveBuiltOutputPath,
} from "./built-site-contracts.mjs";
import { assertSafeSiteDirectory } from "./safe-site-paths.mjs";
import { analyzeHtmlDocument } from "./semantic-html.mjs";
import { parseSitemapXml } from "./semantic-xml.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const outputDirectory = join(siteDirectory, "dist");
const configuredBasePath =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH;
const basePath = normalizeBasePath(configuredBasePath);
const origin = new URL(
  process.env.SITE_URL ?? "https://jagger-l.github.io",
).origin;
const routes = sidebarRoutes(docsSidebar);
const siteContract = buildExpectedSiteContract(routes, { basePath, origin });

await assertSafeSiteDirectory({
  siteDirectory,
  targetDirectory: outputDirectory,
  label: "Built documentation output",
});

async function inspectBuiltTree(directory, state, root = directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    const relativePath = relative(root, path).replace(/\\/g, "/");
    if (entry.isDirectory()) {
      await inspectBuiltTree(path, state, root);
    } else if (entry.isFile()) {
      if (/\.html?$/iu.test(entry.name)) state.htmlFiles.push(path);
    } else {
      state.nonRegularEntries.push(relativePath);
    }
  }
}

const builtTree = { htmlFiles: [], nonRegularEntries: [] };
await inspectBuiltTree(outputDirectory, builtTree);
const htmlFiles = builtTree.htmlFiles;
const failures = [];
for (const path of builtTree.nonRegularEntries) {
  failures.push(`built output contains a non-ordinary entry: ${path}`);
}
const htmlRelativePaths = htmlFiles.map((path) =>
  relative(outputDirectory, path).replace(/\\/g, "/"),
);
const htmlRelativePathSet = new Set(htmlRelativePaths);
failures.push(
  ...exactCollectionFailures(
    htmlRelativePaths,
    siteContract.htmlPaths,
    "built HTML routes",
  ),
);
let checkedLinks = 0;
let checkedAnchors = 0;
let checkedDescriptions = 0;
let checkedUpdateDates = 0;
let checkedSeoContracts = 0;
let checkedLlmsLinks = 0;
let checkedSitemapUrls = 0;
const htmlCache = new Map();
const htmlAnalysisCache = new Map();

async function htmlFor(path) {
  if (!htmlCache.has(path)) htmlCache.set(path, await readFile(path, "utf8"));
  return htmlCache.get(path);
}

async function analysisFor(path) {
  if (!htmlAnalysisCache.has(path)) {
    htmlAnalysisCache.set(path, analyzeHtmlDocument(await htmlFor(path)));
  }
  return htmlAnalysisCache.get(path);
}

async function idsFor(path) {
  return (await analysisFor(path)).ids;
}

function validateLanguageSeo(analysis, page, canonicalRoute) {
  const expectedCanonical = siteContract.routeToUrl.get(page);
  if (!expectedCanonical) {
    failures.push(`${page || "home"}: route is absent from the canonical site contract`);
    return;
  }
  if (
    analysis.canonicalLinks.length !== 1 ||
    analysis.canonicalLinks[0] !== expectedCanonical
  ) {
    failures.push(
      `${page || "home"}: canonical must be ${expectedCanonical}`,
    );
  }

  const alternates = analysis.alternates;
  const english = siteContract.routeToUrl.get(canonicalRoute);
  const chinese = siteContract.routeToUrl.get(
    canonicalRoute ? `zh-cn/${canonicalRoute}` : "zh-cn",
  );
  const expectedAlternates = new Map([
    ["en", english],
    ["zh-CN", chinese],
    ["x-default", english],
  ]);
  if (alternates.length !== expectedAlternates.size) {
    failures.push(`${page || "home"}: expected exactly three language alternates`);
  }
  for (const [language, href] of expectedAlternates) {
    if (
      !alternates.some(
        (alternate) =>
          alternate.language === language && alternate.href === href,
      )
    ) {
      failures.push(`${page || "home"}: missing ${language} alternate ${href}`);
    }
  }

  const expectedLanguage = page === "zh-cn" || page.startsWith("zh-cn/")
    ? "zh-CN"
    : "en";
  if (
    analysis.htmlLanguages.length !== 1 ||
    analysis.htmlLanguages[0] !== expectedLanguage
  ) {
    failures.push(`${page || "home"}: document language must be ${expectedLanguage}`);
  }

  if (analysis.robots.some((robots) => /\bnoindex\b/iu.test(robots ?? ""))) {
    failures.push(`${page || "home"}: canonical documentation must be indexable`);
  }
  checkedSeoContracts += 1;
}

function validateHomepageLanguage(analysis, page) {
  if (analysis.mainTexts.length !== 1) {
    failures.push(`${page || "home"}: expected exactly one semantic main element`);
    return;
  }
  const text = analysis.mainTexts[0];
  const chineseCharacters = text.match(/[\u3400-\u9fff]/gu)?.length ?? 0;
  const latinLetters = text.match(/[A-Za-z]/gu)?.length ?? 0;
  if (page === "zh-cn") {
    const languageCharacters = chineseCharacters + latinLetters;
    if (
      chineseCharacters < 150 ||
      languageCharacters === 0 ||
      chineseCharacters / languageCharacters < 0.3
    ) {
      failures.push("zh-cn home: visible main content is not a complete Chinese homepage");
    }
  } else if (latinLetters < 300 || chineseCharacters !== 0) {
    failures.push("home: visible main content is not a complete English homepage");
  }
}

for (const route of routes) {
  for (const [localePrefix, updateLabel] of [
    ["", "Last updated:"],
    ["zh-cn/", "最近更新："],
  ]) {
    const page = `${localePrefix}${route}`;
    const htmlRelativePath = `${page}/index.html`;
    const htmlPath = join(outputDirectory, ...htmlRelativePath.split("/"));
    if (!htmlRelativePathSet.has(htmlRelativePath)) {
      failures.push(`${page}: missing canonical page for metadata checks`);
      continue;
    }
    const analysis = await analysisFor(htmlPath);
    const descriptionText = analysis.descriptions[0];
    if (analysis.descriptions.length !== 1 || !descriptionText) {
      failures.push(`${page}: missing meta description`);
    } else if (
      descriptionText.length > 180 ||
      (descriptionText.length === 180 && /[\p{L}\p{N}]$/u.test(descriptionText))
    ) {
      failures.push(`${page}: meta description is overlong or cut mid-word`);
    } else {
      checkedDescriptions += 1;
    }

    if (
      !analysis.documentText.includes(updateLabel) ||
      analysis.timeDatetimes.length === 0
    ) {
      failures.push(`${page}: missing canonical-source last-updated date`);
    } else {
      checkedUpdateDates += 1;
    }

    validateLanguageSeo(analysis, page, route);
  }
}

for (const [page, htmlRelativePath] of [
  ["", "index.html"],
  ["zh-cn", "zh-cn/index.html"],
]) {
  if (!htmlRelativePathSet.has(htmlRelativePath)) continue;
  const htmlPath = join(outputDirectory, ...htmlRelativePath.split("/"));
  const analysis = await analysisFor(htmlPath);
  validateLanguageSeo(analysis, page, "");
  validateHomepageLanguage(analysis, page);
}

if (htmlRelativePathSet.has("404.html")) {
  const notFound = await analysisFor(join(outputDirectory, "404.html"));
  if (notFound.canonicalLinks.length > 0) {
    failures.push("404.html: must not publish a canonical URL");
  }
  if (notFound.alternates.length > 0) {
    failures.push("404.html: must not publish language alternates");
  }
  if (notFound.robots.length !== 1 || notFound.robots[0] !== "noindex,follow") {
    failures.push("404.html: must be noindex,follow");
  }
  checkedSeoContracts += 1;
}

const outputRootEntries = await readdir(outputDirectory, { withFileTypes: true });
const sitemapArtifacts = outputRootEntries.filter((entry) =>
  /^sitemap.*\.xml$/u.test(entry.name),
);
for (const entry of sitemapArtifacts) {
  if (
    entry.name !== "sitemap-index.xml" &&
    !/^sitemap-\d+\.xml$/u.test(entry.name)
  ) {
    failures.push(`sitemap artifacts: unexpected ${entry.name}`);
  }
  if (!entry.isFile()) failures.push(`sitemap artifacts: ${entry.name} is not an ordinary file`);
}

const sitemapChunkNames = sitemapArtifacts
  .filter((entry) => entry.isFile() && /^sitemap-\d+\.xml$/u.test(entry.name))
  .map((entry) => entry.name)
  .sort();
const sitemapIndex = sitemapArtifacts.find((entry) => entry.name === "sitemap-index.xml");
if (!sitemapIndex?.isFile()) {
  failures.push("sitemap index: missing ordinary sitemap-index.xml");
} else {
  const sitemapIndexXml = await readFile(join(outputDirectory, sitemapIndex.name), "utf8");
  try {
    const parsedIndex = parseSitemapXml(sitemapIndexXml);
    if (parsedIndex.root !== "sitemapindex") {
      failures.push("sitemap index: root element must be sitemapindex");
    }
    const expectedChunkUrls = sitemapChunkNames.map((name) =>
      publicUrlForFile(name, { basePath, origin }),
    );
    failures.push(
      ...exactCollectionFailures(
        parsedIndex.locations,
        expectedChunkUrls,
        "sitemap index chunks",
      ),
    );
  } catch (error) {
    failures.push(`sitemap index: ${error.message}`);
  }
}

const sitemapXmlDocuments = await Promise.all(
  sitemapChunkNames.map(async (name) => ({
    name,
    xml: await readFile(join(outputDirectory, name), "utf8"),
  })),
);
const sitemapUrls = [];
for (const { name, xml } of sitemapXmlDocuments) {
  try {
    const parsed = parseSitemapXml(xml);
    if (parsed.root !== "urlset") failures.push(`${name}: root element must be urlset`);
    sitemapUrls.push(...parsed.locations);
  } catch (error) {
    failures.push(`${name}: ${error.message}`);
  }
}
failures.push(
  ...exactCollectionFailures(
    sitemapUrls,
    siteContract.canonicalUrls,
    "sitemap canonical URLs",
  ),
);
checkedSitemapUrls = sitemapUrls.length;

const llmsPublicUrl = publicUrlForFile("llms.txt", { basePath, origin });
let llmsPath = null;
try {
  llmsPath = await resolveBuiltOutputPath({
    pathname: new URL(llmsPublicUrl).pathname,
    basePath,
    outputDirectory,
  });
} catch (error) {
  const detail = error instanceof BuiltSiteContractError ? error.message : String(error);
  failures.push(`llms.txt: ${detail}`);
}
if (llmsPath !== null) {
  const llms = await readFile(llmsPath, "utf8");
  const { targets: llmsTargets, invalidLines } = extractMarkdownLinkTargets(llms);
  for (const line of invalidLines) failures.push(`llms.txt: malformed Markdown link: ${line}`);
  failures.push(
    ...exactCollectionFailures(
      llmsTargets,
      siteContract.canonicalUrls,
      "llms.txt canonical links",
    ),
  );
  checkedLlmsLinks = llmsTargets.length;
}

for (const htmlPath of htmlFiles) {
  const analysis = await analysisFor(htmlPath);
  const pagePath = relative(outputDirectory, htmlPath)
    .replace(/\\/g, "/")
    .replace(/index\.html$/, "");
  const pageUrl = new URL(`${basePath}/${pagePath}`, origin);

  if (analysis.refreshMetas > 0) {
    failures.push(`${pagePath || "index.html"}: redirect pages are not allowed`);
  }

  for (const value of analysis.localReferences) {
    if (
      !value ||
      /^(?:data:|mailto:|tel:|javascript:)/i.test(value)
    ) {
      continue;
    }

    let url;
    try {
      url = new URL(value, pageUrl);
    } catch {
      failures.push(`${pagePath || "index.html"}: invalid URL -> ${value}`);
      continue;
    }
    if (url.origin !== origin) continue;
    checkedLinks += 1;

    let targetPath;
    try {
      targetPath = await resolveBuiltOutputPath({
        pathname: url.pathname,
        basePath,
        outputDirectory,
      });
    } catch (error) {
      const detail = error instanceof BuiltSiteContractError ? error.message : String(error);
      failures.push(`${pagePath || "index.html"}: ${detail} -> ${value}`);
      continue;
    }

    if (url.hash && targetPath.endsWith(".html")) {
      checkedAnchors += 1;
      let fragment;
      try {
        fragment = decodeURIComponent(url.hash.slice(1));
      } catch {
        failures.push(`${pagePath || "index.html"}: invalid anchor encoding -> ${value}`);
        continue;
      }
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
      `${checkedLlmsLinks} llms.txt entries and ${checkedSitemapUrls} sitemap URLs; ` +
      `validated ${checkedDescriptions} descriptions, ` +
      `${checkedUpdateDates} update dates, and ${checkedSeoContracts} SEO contracts ` +
      `across ${htmlFiles.length} HTML files.`,
  );
}
