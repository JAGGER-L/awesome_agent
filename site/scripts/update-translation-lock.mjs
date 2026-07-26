import { rename, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  compileDocumentationCatalog,
  createTranslationLock,
} from "../documentation-catalog.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "..", "..");
const lockPath = join(repositoryRoot, "site", "translation-lock.json");
const temporaryPath = `${lockPath}.tmp-${process.pid}`;

const catalog = await compileDocumentationCatalog({
  repositoryRoot,
  verifyTranslationLock: false,
});
const lock = createTranslationLock(catalog);
const serialized = `${JSON.stringify(lock, null, 2)}\n`;

try {
  await writeFile(temporaryPath, serialized, { encoding: "utf8", flag: "wx" });
  await rename(temporaryPath, lockPath);
} catch (error) {
  await rm(temporaryPath, { force: true });
  throw error;
}

console.log(`Updated translation-lock.json with ${lock.pairs.length} source pairs.`);
