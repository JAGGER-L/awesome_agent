import { access, readFile, readdir } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteDirectory = resolve(scriptDirectory, "..");
const outputDirectory = join(siteDirectory, "dist");
const configuredBasePath =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH;
const basePath = configuredBasePath.replace(/\/$/, "");
const origin = "https://awesome-docs.invalid";

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

for (const htmlPath of htmlFiles) {
  const html = await readFile(htmlPath, "utf8");
  const pagePath = relative(outputDirectory, htmlPath)
    .replace(/\\/g, "/")
    .replace(/index\.html$/, "");
  const pageUrl = new URL(`${basePath}/${pagePath}`, origin);

  for (const match of html.matchAll(/\b(?:href|src)=(?:"([^"]+)"|'([^']+)')/g)) {
    const value = match[1] || match[2];
    if (
      !value ||
      value.startsWith("#") ||
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
    }
  }
}

if (failures.length > 0) {
  console.error(`Found ${failures.length} broken local link(s):`);
  for (const failure of [...new Set(failures)].sort()) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(`Checked ${checkedLinks} local links across ${htmlFiles.length} HTML files.`);
}
