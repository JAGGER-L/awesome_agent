import { execFileSync } from "node:child_process";
import { stat } from "node:fs/promises";
import { dirname, join, posix, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import {
  compileDocumentationCatalog,
  resolveCatalogSourceLink,
} from "../documentation-catalog.mjs";
import {
  atomicWriteSafeSiteFile,
  ensureSafeSiteDirectory,
  replaceSafeSiteDirectory,
} from "./safe-site-paths.mjs";
import { rewriteMarkdownLinks as rewriteMarkdownLinksWithAst } from "./markdown-ast.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const repositoryRoot = resolve(siteDirectory, "..");
const generatedDocs = join(siteDirectory, "src", "content", "docs");

function assertGeneratedTarget(target) {
  const relativeTarget = relative(siteDirectory, target);
  if (
    !relativeTarget ||
    relativeTarget.startsWith(`..${sep}`) ||
    relativeTarget === ".." ||
    relativeTarget !== join("src", "content", "docs")
  ) {
    throw new Error(`Refusing to replace unexpected content target: ${target}`);
  }
}

function fallbackTitle(filePath) {
  return filePath
    .replace(/\.(md|mdx)$/i, "")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function plainText(markdown) {
  return markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_>#~-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function boundedDescription(text, limit = 180) {
  if (text.length <= limit) return text;

  const firstSentence = text.match(/^(.+?(?:[!?。！？]|\.(?=\s|$)))/u)?.[1];
  if (firstSentence && firstSentence.length <= limit) return firstSentence;

  const candidate = text.slice(0, limit - 1);
  const boundaries = [
    candidate.lastIndexOf(" "),
    candidate.lastIndexOf(","),
    candidate.lastIndexOf(";"),
    candidate.lastIndexOf("，"),
    candidate.lastIndexOf("；"),
  ];
  const boundary = Math.max(...boundaries);
  const cutoff = boundary >= Math.floor(limit * 0.6) ? boundary : candidate.length;
  return `${candidate.slice(0, cutoff).replace(/[\s,;，；]+$/u, "")}…`;
}

function findDescription(body) {
  const paragraphs = body.split(/\n\s*\n/);
  for (const paragraph of paragraphs) {
    if (/^\s*(?:#|```|[-*]\s|\d+\.\s)/.test(paragraph)) continue;
    const text = plainText(paragraph);
    if (text.length >= 30) {
      const description = boundedDescription(text);
      if (description.length > 180) {
        throw new Error("Generated description exceeds 180 characters.");
      }
      return description;
    }
  }
  return "Awesome documentation.";
}

function rewriteMarkdownLinks(body, entry, catalog) {
  const currentRoute = entry.route || ".";
  return rewriteMarkdownLinksWithAst(body, (destination) => {
    const suffixIndex = destination.search(/[?#]/u);
    const sourceTarget =
      suffixIndex === -1 ? destination : destination.slice(0, suffixIndex);
    const suffix = suffixIndex === -1 ? "" : destination.slice(suffixIndex);
    if (!/\.md$/iu.test(sourceTarget)) return null;
    const target = resolveCatalogSourceLink(catalog, entry.source, sourceTarget);
    if (!target || target.route === null) return null;
    const targetRoute = target.route || ".";
    const relativeRoute = posix.relative(currentRoute, targetRoute);
    return `${relativeRoute || "."}/${suffix}`;
  });
}

function withFrontmatter(raw, entry, catalog, lastUpdated) {
  const existing = raw.match(/^---\n([\s\S]*?)\n---\n?/);
  let frontmatter = existing?.[1] ?? "";
  let body = existing ? raw.slice(existing[0].length) : raw;
  const heading = body.match(/^#\s+(.+)\s*$/m);
  const title = heading?.[1]?.trim() || fallbackTitle(entry.output);

  if (heading) {
    body = `${body.slice(0, heading.index)}${body.slice((heading.index ?? 0) + heading[0].length)}`
      .replace(/^\s+/, "");
  }

  const fields = [];
  if (!/^title\s*:/m.test(frontmatter)) fields.push(`title: ${JSON.stringify(title)}`);
  if (!/^description\s*:/m.test(frontmatter)) {
    fields.push(`description: ${JSON.stringify(findDescription(body))}`);
  }
  if (!/^lastUpdated\s*:/m.test(frontmatter)) {
    fields.push(`lastUpdated: ${lastUpdated}`);
  }
  if (!/^editUrl\s*:/m.test(frontmatter)) {
    fields.push(
      `editUrl: ${JSON.stringify(`https://github.com/JAGGER-L/awesome_agent/edit/main/${entry.source}`)}`,
    );
  }
  if (/\/index\.md$/i.test(`/${entry.output}`)) {
    const slug = dirname(entry.output).replace(/\\/g, "/");
    if (slug !== "." && !/^slug\s*:/m.test(frontmatter)) fields.push(`slug: ${slug}`);
  }

  frontmatter = [frontmatter.trim(), ...fields].filter(Boolean).join("\n");
  body = rewriteMarkdownLinks(body, entry, catalog).trimStart();
  return `---\n${frontmatter}\n---\n\n${body.trimEnd()}\n`;
}

async function lastUpdatedFor(sourcePath) {
  try {
    const committed = execFileSync(
      "git",
      ["log", "-1", "--format=%cI", "--", sourcePath],
      { cwd: repositoryRoot, encoding: "utf8", windowsHide: true },
    ).trim();
    if (committed) return committed.slice(0, 10);
  } catch {
    // Source archives and fresh untracked pages have no usable Git history.
  }
  return (await stat(sourcePath)).mtime.toISOString().slice(0, 10);
}

async function writeCatalogPage(entry, catalog, outputRoot) {
  const targetPath = join(outputRoot, ...entry.output.split("/"));
  const targetDirectory = dirname(targetPath);
  await ensureSafeSiteDirectory({
    siteDirectory,
    targetDirectory,
    label: `Generated documentation parent for ${entry.output}`,
  });
  if (entry.kind === "homepage") {
    await atomicWriteSafeSiteFile({
      siteDirectory,
      targetFile: targetPath,
      content: entry.content,
      label: `Generated documentation page ${entry.output}`,
    });
    return;
  }
  const lastUpdated = await lastUpdatedFor(entry.absolutePath);
  const content = withFrontmatter(entry.content, entry, catalog, lastUpdated);
  await atomicWriteSafeSiteFile({
    siteDirectory,
    targetFile: targetPath,
    content,
    label: `Generated documentation page ${entry.output}`,
  });
}

async function main() {
  assertGeneratedTarget(generatedDocs);
  const catalog = await compileDocumentationCatalog({ repositoryRoot });

  await replaceSafeSiteDirectory({
    siteDirectory,
    targetDirectory: generatedDocs,
    label: "Generated documentation replacement",
    populate: async (stagingDirectory) => {
      for (const entry of catalog.pages) {
        await writeCatalogPage(entry, catalog, stagingDirectory);
      }
    },
  });

  console.log(
    `Generated ${catalog.pages.length} documentation pages from one validated catalog.`,
  );
}

await main();
