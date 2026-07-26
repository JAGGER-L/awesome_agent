import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { compileDocumentationCatalog } from "../documentation-catalog.mjs";
import { docsSidebar } from "../docs-navigation.mjs";
import {
  atomicWriteSafeSiteFile,
  ensureSafeSiteDirectory,
} from "./safe-site-paths.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const repositoryRoot = resolve(siteDirectory, "..");
const outputDirectory = join(siteDirectory, "dist");
const siteOrigin = process.env.SITE_URL ?? "https://jagger-l.github.io";
const configuredBase =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH;
const basePath = configuredBase === "/" ? "" : `/${configuredBase.replace(/^\/+|\/+$/g, "")}`;

function titleForRoute(catalog, route) {
  const entry = catalog.byRoute.get(route);
  if (!entry) throw new Error(`Canonical page is missing from the catalog: ${route}`);
  const heading = entry.content.match(/^#\s+(.+)$/m);
  if (!heading) throw new Error(`Canonical page has no H1: ${entry.source}`);
  return heading[1].trim();
}

function publicUrl(route = "") {
  const path = `${basePath}/${route ? `${route}/` : ""}`.replace(/\/{2,}/g, "/");
  return new URL(path, siteOrigin).href;
}

const catalog = await compileDocumentationCatalog({ repositoryRoot });
const lines = [
  "# Awesome documentation",
  "",
  "> Official English and Simplified Chinese documentation for Awesome, the terminal AI coding assistant.",
  "",
  "## English",
  "",
  `- [Documentation home](${publicUrl()})`,
];

for (const group of docsSidebar) {
  lines.push("", `### ${group.label}`, "");
  for (const item of group.items) {
    const route = typeof item === "string" ? item : item.slug;
    lines.push(`- [${titleForRoute(catalog, route)}](${publicUrl(route)})`);
  }
}

lines.push("", "## 简体中文", "", `- [文档首页](${publicUrl("zh-cn")})`);
for (const group of docsSidebar) {
  lines.push("", `### ${group.translations["zh-CN"]}`, "");
  for (const item of group.items) {
    const route = typeof item === "string" ? item : item.slug;
    const localizedRoute = `zh-cn/${route}`;
    lines.push(
      `- [${titleForRoute(catalog, localizedRoute)}](${publicUrl(localizedRoute)})`,
    );
  }
}

lines.push(
  "",
  "Canonical sources live in the Awesome repository " +
    "(`docs/`, paired root READMEs and architecture, and paired homepage MDX/JSON under `site/`).",
  "",
);

await ensureSafeSiteDirectory({
  siteDirectory,
  targetDirectory: outputDirectory,
  label: "Documentation build output directory",
});
const llmsPath = join(outputDirectory, "llms.txt");
await atomicWriteSafeSiteFile({
  siteDirectory,
  targetFile: llmsPath,
  content: lines.join("\n"),
  label: "Generated llms.txt",
});
console.log(
  `Generated llms.txt with ${lines.filter((line) => line.startsWith("- [")).length} links.`,
);
