import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { compileDocumentationCatalog } from "../documentation-catalog.mjs";
import { docsSidebar } from "../docs-navigation.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "..", "..");

const catalog = await compileDocumentationCatalog({ repositoryRoot });
const englishCanonicalPages = catalog.pages.filter(
  (entry) => entry.locale === "en" && entry.kind !== "homepage",
);

console.log(
  `Navigation exactly covers ${englishCanonicalPages.length} canonical pages in ` +
    `${docsSidebar.length} groups; ${catalog.lockPairs.length} translation pairs match the lock.`,
);
