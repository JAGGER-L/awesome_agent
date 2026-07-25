import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { docsSidebar } from "../docs-navigation.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const repositoryRoot = resolve(siteDirectory, "..");
const outputDirectory = join(siteDirectory, "dist");
const siteOrigin = process.env.SITE_URL ?? "https://jagger-l.github.io";
const configuredBase =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH;
const basePath = configuredBase === "/" ? "" : `/${configuredBase.replace(/^\/+|\/+$/g, "")}`;

function sourceForRoute(route) {
  if (route === "architecture/overview") return join(repositoryRoot, "ARCHITECTURE.md");
  if (route === "roadmap") return join(repositoryRoot, "docs", "roadmap.md");
  const segments = route.split("/");
  if (segments.length === 1) {
    return join(repositoryRoot, "docs", route, "README.md");
  }
  return join(repositoryRoot, "docs", `${route}.md`);
}

async function titleForRoute(route) {
  const content = await readFile(sourceForRoute(route), "utf8");
  const heading = content.match(/^#\s+(.+)$/m);
  if (!heading) throw new Error(`Canonical page has no H1: ${route}`);
  return heading[1].trim();
}

function publicUrl(route = "") {
  const path = `${basePath}/${route ? `${route}/` : ""}`.replace(/\/{2,}/g, "/");
  return new URL(path, siteOrigin).href;
}

const lines = [
  "# Awesome documentation",
  "",
  "> Official documentation for Awesome, the terminal AI coding assistant.",
  "",
  `- [Documentation home](${publicUrl()})`,
];

for (const group of docsSidebar) {
  lines.push("", `## ${group.label}`, "");
  for (const item of group.items) {
    const route = typeof item === "string" ? item : item.slug;
    lines.push(`- [${await titleForRoute(route)}](${publicUrl(route)})`);
  }
}

lines.push(
  "",
  "Canonical sources: https://github.com/JAGGER-L/awesome_agent/tree/main " +
    "(`docs/`, `ARCHITECTURE.md`, and the documentation homepage under `site/`).",
  "",
);

await mkdir(outputDirectory, { recursive: true });
await writeFile(join(outputDirectory, "llms.txt"), lines.join("\n"), "utf8");
console.log(`Generated llms.txt with ${lines.filter((line) => line.startsWith("- [")).length} links.`);
