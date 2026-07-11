import { readFile, writeFile } from "node:fs/promises";

const check = process.argv.slice(2);
if (check.length > 1 || (check.length === 1 && check[0] !== "--check")) {
  throw new Error("Usage: node scripts/sync-version.mjs [--check]");
}

const versionUrl = new URL("../../VERSION", import.meta.url);
const packageUrl = new URL("../package.json", import.meta.url);
const lockUrl = new URL("../package-lock.json", import.meta.url);
const sourceUrl = new URL("../src/version.ts", import.meta.url);

const rawVersion = await readFile(versionUrl, "utf8");
if (!/^[0-9]+\.[0-9]+\.[0-9]+\r?\n$/u.test(rawVersion)) {
  throw new Error("VERSION must contain MAJOR.MINOR.PATCH and a final newline");
}
const version = rawVersion.trim();

const packageJson = JSON.parse(await readFile(packageUrl, "utf8"));
const packageLock = JSON.parse(await readFile(lockUrl, "utf8"));
packageJson.version = version;
packageLock.version = version;
if (!packageLock.packages?.[""]) {
  throw new Error("package-lock.json has no root package entry");
}
packageLock.packages[""].version = version;

const expected = new Map([
  [packageUrl, `${JSON.stringify(packageJson, null, 2)}\n`],
  [lockUrl, `${JSON.stringify(packageLock, null, 2)}\n`],
  [
    sourceUrl,
    `export const PRODUCT_VERSION = ${JSON.stringify(version)} as const;\n`,
  ],
]);

if (check[0] === "--check") {
  const drift = [];
  for (const [url, content] of expected) {
    if ((await readFile(url, "utf8")) !== content) drift.push(url.pathname);
  }
  if (drift.length > 0) {
    throw new Error(`Generated version files are stale: ${drift.join(", ")}`);
  }
} else {
  await Promise.all(
    [...expected].map(([url, content]) => writeFile(url, content, "utf8")),
  );
}
