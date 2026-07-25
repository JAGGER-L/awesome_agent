import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
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

function findDescription(body) {
  const paragraphs = body.split(/\n\s*\n/);
  for (const paragraph of paragraphs) {
    if (/^\s*(?:#|```|[-*]\s|\d+\.\s)/.test(paragraph)) continue;
    const text = plainText(paragraph);
    if (text.length >= 30) return text.slice(0, 180);
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
    if (normalizedTarget === "docs/architecture/README.md") {
      return "architecture/index.md";
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

  return body.replace(
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
}

function withFrontmatter(raw, sourceRelativePath, outputRelativePath) {
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
  await mkdir(dirname(targetPath), { recursive: true });
  await writeFile(
    targetPath,
    withFrontmatter(content, sourceRelativePath, outputRelativePath),
    "utf8",
  );
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
