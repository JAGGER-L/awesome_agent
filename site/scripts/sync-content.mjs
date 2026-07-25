import { execFileSync } from "node:child_process";
import { cp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { dirname, join, posix, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const repositoryRoot = resolve(siteDirectory, "..");
const sourceDocs = join(repositoryRoot, "docs");
const seedDirectory = join(siteDirectory, "content");
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

function routeForOutput(outputRelativePath) {
  const withoutExtension = outputRelativePath
    .replace(/\\/g, "/")
    .replace(/\.(md|mdx)$/i, "");
  return withoutExtension.replace(/(^|\/)index$/i, "").replace(/\/$/, "");
}

function targetOutputPath(sourceRelativePath, markdownPath) {
  const normalizedSource = sourceRelativePath.replace(/\\/g, "/");
  const sourceIsChinese = normalizedSource.endsWith(".zh-CN.md");
  const normalizedTarget = posix.normalize(
    posix.join(posix.dirname(normalizedSource), markdownPath),
  );

  if (normalizedSource === "ARCHITECTURE.md") {
    if (normalizedTarget.startsWith("docs/")) {
      return outputPathFor(normalizedTarget.slice("docs/".length)).replace(/\\/g, "/");
    }
    return null;
  }

  if (normalizedTarget === "../ARCHITECTURE.md") {
    return "architecture/overview.md";
  }
  if (normalizedTarget === "../README.md") return "index.mdx";
  if (normalizedTarget.startsWith("../")) return null;
  const outputPath = outputPathFor(normalizedTarget).replace(/\\/g, "/");
  return sourceIsChinese && !outputPath.startsWith("zh-cn/")
    ? `zh-cn/${outputPath}`
    : outputPath;
}

function rewriteMarkdownLinks(body, sourceRelativePath, outputRelativePath) {
  const currentRoute = routeForOutput(outputRelativePath) || ".";
  let fence = null;

  return body
    .split("\n")
    .map((line) => {
      const marker = line.match(/^\s*(`{3,}|~{3,})/);
      if (marker) {
        const candidate = marker[1];
        if (fence === null) {
          fence = candidate;
        } else if (candidate[0] === fence[0] && candidate.length >= fence.length) {
          fence = null;
        }
        return line;
      }
      if (fence !== null) return line;

      return line.replace(
        /\]\((?!https?:|mailto:|#)([^)\s]+?)\.md(#[^)]+)?\)/g,
        (_match, path, hash = "") => {
          const targetOutput = targetOutputPath(sourceRelativePath, `${path}.md`);
          if (!targetOutput) return _match;

          const targetRoute = routeForOutput(targetOutput) || ".";
          const relativeRoute = posix.relative(currentRoute, targetRoute);
          const href = `${relativeRoute || "."}/${hash}`;
          return `](${href})`;
        },
      );
    })
    .join("\n");
}

function withFrontmatter(raw, sourceRelativePath, outputRelativePath, lastUpdated) {
  const normalized = raw.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n");
  const existing = normalized.match(/^---\n([\s\S]*?)\n---\n?/);
  let frontmatter = existing?.[1] ?? "";
  let body = existing ? normalized.slice(existing[0].length) : normalized;
  const heading = body.match(/^#\s+(.+)\s*$/m);
  const title = heading?.[1]?.trim() || fallbackTitle(outputRelativePath);

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
    const sourcePath = sourceRelativePath === "ARCHITECTURE.md"
      ? "ARCHITECTURE.md"
      : `docs/${sourceRelativePath.replace(/\\/g, "/")}`;
    fields.push(
      `editUrl: ${JSON.stringify(`https://github.com/JAGGER-L/awesome_agent/edit/main/${sourcePath}`)}`,
    );
  }
  if (/\/index\.md$/i.test(`/${outputRelativePath.replace(/\\/g, "/")}`)) {
    const slug = dirname(outputRelativePath).replace(/\\/g, "/");
    if (slug !== "." && !/^slug\s*:/m.test(frontmatter)) fields.push(`slug: ${slug}`);
  }

  frontmatter = [frontmatter.trim(), ...fields].filter(Boolean).join("\n");
  body = rewriteMarkdownLinks(body, sourceRelativePath, outputRelativePath).trimStart();
  return `---\n${frontmatter}\n---\n\n${body.trimEnd()}\n`;
}

async function listMarkdownFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listMarkdownFiles(path)));
    if (entry.isFile() && /\.md$/i.test(entry.name)) files.push(path);
  }
  return files;
}

function outputPathFor(sourceRelativePath) {
  const normalized = sourceRelativePath.replace(/\\/g, "/");
  const chinese = normalized.endsWith(".zh-CN.md");
  const localized = chinese ? normalized.replace(/\.zh-CN\.md$/, ".md") : normalized;
  const indexed = localized.replace(/(^|\/)README\.md$/i, "$1index.md");
  return chinese ? join("zh-cn", ...indexed.split("/")) : join(...indexed.split("/"));
}

async function writeGeneratedPage(sourcePath, sourceRelativePath, outputRelativePath) {
  const targetPath = join(generatedDocs, outputRelativePath);
  const content = await readFile(sourcePath, "utf8");
  const lastUpdated = await lastUpdatedFor(sourcePath);
  await mkdir(dirname(targetPath), { recursive: true });
  await writeFile(
    targetPath,
    withFrontmatter(content, sourceRelativePath, outputRelativePath, lastUpdated),
    "utf8",
  );
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

async function main() {
  assertGeneratedTarget(generatedDocs);
  await rm(generatedDocs, { recursive: true, force: true });
  await mkdir(generatedDocs, { recursive: true });

  await cp(join(seedDirectory, "index.mdx"), join(generatedDocs, "index.mdx"));
  await mkdir(join(generatedDocs, "zh-cn"), { recursive: true });
  await cp(
    join(seedDirectory, "index.zh-cn.mdx"),
    join(generatedDocs, "zh-cn", "index.mdx"),
  );

  const docsFiles = await listMarkdownFiles(sourceDocs);
  let pageCount = 2;
  for (const sourcePath of docsFiles) {
    const sourceRelativePath = relative(sourceDocs, sourcePath);
    if (sourceRelativePath.replace(/\\/g, "/") === "README.md") continue;
    const outputRelativePath = outputPathFor(sourceRelativePath);
    await writeGeneratedPage(sourcePath, sourceRelativePath, outputRelativePath);
    pageCount += 1;
  }

  await writeGeneratedPage(
    join(repositoryRoot, "ARCHITECTURE.md"),
    "ARCHITECTURE.md",
    join("architecture", "overview.md"),
  );
  pageCount += 1;

  console.log(`Generated ${pageCount} documentation pages from repository Markdown.`);
}

await main();
