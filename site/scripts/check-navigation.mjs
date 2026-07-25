import { readdir } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { docsRedirects, docsSidebar, sidebarRoutes } from "../docs-navigation.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "..", "..");
const docsDirectory = join(repositoryRoot, "docs");

async function listMarkdownFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listMarkdownFiles(path)));
    if (entry.isFile() && entry.name.endsWith(".md")) files.push(path);
  }
  return files;
}

function routeFor(relativePath) {
  const normalized = relativePath.replace(/\\/g, "/");
  if (normalized === "README.md" || normalized.endsWith(".zh-CN.md")) return null;
  const withoutExtension = normalized.replace(/\.md$/, "");
  return withoutExtension.replace(/(^|\/)README$/i, "").replace(/\/$/, "");
}

const sourceFiles = await listMarkdownFiles(docsDirectory);
const sourceRoutes = new Set(
  sourceFiles
    .map((path) => routeFor(relative(docsDirectory, path)))
    .filter((route) => route !== null),
);
sourceRoutes.add("architecture/overview");

const routes = sidebarRoutes(docsSidebar);
const routeCounts = new Map();
for (const route of routes) routeCounts.set(route, (routeCounts.get(route) ?? 0) + 1);

const duplicateRoutes = [...routeCounts]
  .filter(([, count]) => count !== 1)
  .map(([route]) => route);
const missingFromNavigation = [...sourceRoutes].filter((route) => !routeCounts.has(route));
const missingFromSources = routes.filter((route) => !sourceRoutes.has(route));

const redirectFailures = [];
for (const [source, destination] of Object.entries(docsRedirects)) {
  const sourceRoute = source.replace(/^\//, "").replace(/\/$/, "");
  const destinationRoute = destination
    .replace(/^\/zh-cn\//, "")
    .replace(/^\//, "")
    .replace(/\/$/, "");
  if (sourceRoutes.has(sourceRoute) || sourceRoutes.has(sourceRoute.replace(/^zh-cn\//, ""))) {
    redirectFailures.push(`redirect source is still canonical: ${source}`);
  }
  if (!source.startsWith("/") || !destination.startsWith("/")) {
    redirectFailures.push(`redirect must use root-relative routes: ${source} -> ${destination}`);
  }
  if (!sourceRoutes.has(destinationRoute)) {
    redirectFailures.push(`redirect target has no canonical page: ${source} -> ${destination}`);
  }
}

const localeFailures = [];
for (const sourcePath of sourceFiles) {
  const relativePath = relative(docsDirectory, sourcePath).replace(/\\/g, "/");
  if (!relativePath.endsWith(".zh-CN.md")) continue;
  const englishPath = relativePath.replace(/\.zh-CN\.md$/, ".md");
  if (!sourceFiles.some((path) => relative(docsDirectory, path).replace(/\\/g, "/") === englishPath)) {
    localeFailures.push(`${relativePath} has no ${englishPath}`);
  }
}

const failures = [
  ...duplicateRoutes.map((route) => `duplicate sidebar route: ${route}`),
  ...missingFromNavigation.map((route) => `canonical page is not navigated: ${route}`),
  ...missingFromSources.map((route) => `sidebar route has no canonical page: ${route}`),
  ...redirectFailures,
  ...localeFailures,
];

if (failures.length > 0) {
  console.error("Documentation navigation contract failed:");
  for (const failure of failures.sort()) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(
    `Navigation covers ${sourceRoutes.size} canonical pages in ${docsSidebar.length} groups ` +
      `and validates ${Object.keys(docsRedirects).length} legacy redirects.`,
  );
}
