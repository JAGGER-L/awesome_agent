import { createHash } from "node:crypto";
import { dirname, join, posix, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  docsSidebar,
  sidebarRoutes as collectSidebarRoutes,
} from "./docs-navigation.mjs";
import { validateHomepageContentSources } from "./homepage-content.mjs";
import {
  markdownExternalUrls,
  markdownInlineCodeLiterals,
  markdownLinks,
  markdownProseText,
  markdownStructure,
} from "./scripts/markdown-ast.mjs";
import { createDocumentationInputReader } from "./scripts/documentation-input-safety.mjs";

const siteDirectory = dirname(fileURLToPath(import.meta.url));
const defaultRepositoryRoot = resolve(siteDirectory, "..");
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const LOCK_TOP_LEVEL_FIELDS = [
  "version",
  "hash_algorithm",
  "text_normalization",
  "pairs",
];
const LOCK_PAIR_FIELDS = [
  "english_source",
  "chinese_source",
  "english_sha256",
  "chinese_sha256",
];
const TRANSLATABLE_FENCE_LANGUAGES = new Set([
  "",
  "text",
  "markdown",
  "md",
  "mermaid",
]);
const DEFAULT_ALLOWED_REPOSITORY_MARKDOWN_TARGETS = Object.freeze([]);
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/u;
const PORTABLE_SOURCE_SEGMENT_PATTERN = /^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$/u;
const PORTABLE_ROUTE_SEGMENT_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/u;
const PORTABLE_OUTPUT_SEGMENT_PATTERN = /^[a-z0-9]+(?:[.-][a-z0-9]+)*$/u;
const WINDOWS_DEVICE_SEGMENT_PATTERN =
  /^(?:aux|com[1-9]|con|lpt[1-9]|nul|prn)(?:\.|$)/iu;
const ENGLISH_DOCUMENTATION_HOME =
  "https://jagger-l.github.io/awesome_agent/";
const CHINESE_DOCUMENTATION_HOME =
  "https://jagger-l.github.io/awesome_agent/zh-cn/";

export const TRANSLATION_LOCK_VERSION = 1;
export const TRANSLATION_LOCK_HASH_ALGORITHM = "sha256";
export const TRANSLATION_LOCK_TEXT_NORMALIZATION =
  "utf8-bom-stripped-newlines-lf-v1";

export function compareCodePoints(left, right) {
  const leftPoints = [...String(left)];
  const rightPoints = [...String(right)];
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference =
      leftPoints[index].codePointAt(0) - rightPoints[index].codePointAt(0);
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

function portableIdentitySegments(
  value,
  label,
  { allowEmpty = false, segmentPattern },
) {
  if (typeof value !== "string" || value !== value.trim()) {
    throw new Error(`${label} must be a non-padded string`);
  }
  if (allowEmpty && value === "") return [];
  if (!value || value.startsWith("/") || value.endsWith("/")) {
    throw new Error(`${label} must be a non-empty relative path`);
  }
  if (value.includes("\\")) {
    throw new Error(`${label} contains a backslash`);
  }
  if (value.includes("%")) {
    throw new Error(`${label} contains percent encoding`);
  }
  if (CONTROL_CHARACTER_PATTERN.test(value)) {
    throw new Error(`${label} contains a control character`);
  }

  const segments = value.split("/");
  for (const segment of segments) {
    if (segment === "" || segment === "." || segment === "..") {
      throw new Error(`${label} contains an empty or dot segment`);
    }
    if (!segmentPattern.test(segment)) {
      throw new Error(`${label} is not a portable ASCII slug path: ${value}`);
    }
    if (WINDOWS_DEVICE_SEGMENT_PATTERN.test(segment)) {
      throw new Error(
        `${label} contains a reserved portable path segment: ${segment}`,
      );
    }
  }
  return segments;
}

function assertUrlNormalizationStable(
  value,
  label,
  { directory = false } = {},
) {
  const expected = `/${value}${directory && value ? "/" : ""}`;
  const normalized = new URL(expected, "https://documentation.invalid")
    .pathname;
  if (normalized !== expected) {
    throw new Error(`${label} changes under URL normalization: ${value}`);
  }
}

function normalizedSourcePath(source) {
  portableIdentitySegments(source, "Documentation source path", {
    segmentPattern: PORTABLE_SOURCE_SEGMENT_PATTERN,
  });
  assertUrlNormalizationStable(source, "Documentation source path");
  if (posix.normalize(source) !== source) {
    throw new Error(`Documentation source path is not normalized: ${source}`);
  }
  return source;
}

function assertRouteIdentity(route, label, { allowEmpty = false } = {}) {
  portableIdentitySegments(route, label, {
    allowEmpty,
    segmentPattern: PORTABLE_ROUTE_SEGMENT_PATTERN,
  });
  assertUrlNormalizationStable(route, label, { directory: true });
}

function assertOutputIdentity(output, label) {
  portableIdentitySegments(output, label, {
    segmentPattern: PORTABLE_OUTPUT_SEGMENT_PATTERN,
  });
  assertUrlNormalizationStable(output, label);
}

function assertEntryIdentities(entry, source) {
  if (entry.canonicalRoute !== null) {
    assertRouteIdentity(entry.canonicalRoute, `${source} canonical route`, {
      allowEmpty: entry.kind === "homepage",
    });
  }
  if (entry.route !== null) {
    assertRouteIdentity(entry.route, `${source} public route`, {
      allowEmpty: entry.kind === "homepage" && entry.locale === "en",
    });
  }
  if (entry.output !== null) {
    assertOutputIdentity(entry.output, `${source} output path`);
  }
}

export function normalizeSourceText(content) {
  return String(content)
    .replace(/^\uFEFF/u, "")
    .replace(/\r\n?/g, "\n");
}

export function normalizedSourceHash(content) {
  return createHash("sha256")
    .update(normalizeSourceText(content), "utf8")
    .digest("hex");
}

function routeForOutput(output) {
  const withoutExtension = output.replace(/\.(?:md|mdx)$/i, "");
  return withoutExtension.replace(/(^|\/)index$/i, "$1").replace(/\/$/, "");
}

function describeSource(source) {
  if (source === "README.md" || source === "README.zh-CN.md") {
    const chinese = source.endsWith(".zh-CN.md");
    return {
      pairId: "README.md",
      kind: "repository-readme",
      locale: chinese ? "zh-CN" : "en",
      canonicalRoute: null,
      route: null,
      output: null,
      lock: true,
    };
  }
  if (source === "site/content/index.mdx") {
    return {
      pairId: "homepage",
      kind: "homepage",
      locale: "en",
      canonicalRoute: "",
      route: "",
      output: "index.mdx",
      lock: true,
    };
  }
  if (source === "site/content/index.zh-cn.mdx") {
    return {
      pairId: "homepage",
      kind: "homepage",
      locale: "zh-CN",
      canonicalRoute: "",
      route: "zh-cn",
      output: "zh-cn/index.mdx",
      lock: true,
    };
  }
  if (
    source === "site/homepage-content.en.json" ||
    source === "site/homepage-content.zh-CN.json"
  ) {
    const chinese = source.endsWith(".zh-CN.json");
    return {
      pairId: "site/homepage-content.en.json",
      kind: "homepage-content",
      locale: chinese ? "zh-CN" : "en",
      canonicalRoute: null,
      route: null,
      output: null,
      lock: true,
    };
  }
  if (source === "ARCHITECTURE.md" || source === "ARCHITECTURE.zh-CN.md") {
    const chinese = source.endsWith(".zh-CN.md");
    const canonicalRoute = "architecture/overview";
    return {
      pairId: "ARCHITECTURE.md",
      kind: "architecture",
      locale: chinese ? "zh-CN" : "en",
      canonicalRoute,
      route: chinese ? `zh-cn/${canonicalRoute}` : canonicalRoute,
      output: chinese
        ? "zh-cn/architecture/overview.md"
        : "architecture/overview.md",
      lock: true,
    };
  }
  if (!source.startsWith("docs/") || !source.endsWith(".md")) {
    throw new Error(`Unsupported documentation source: ${source}`);
  }

  const chinese = source.endsWith(".zh-CN.md");
  const englishSource = chinese
    ? source.replace(/\.zh-CN\.md$/u, ".md")
    : source;
  const docsRelative = englishSource.slice("docs/".length);
  const repositoryOnly = docsRelative === "README.md";
  if (repositoryOnly) {
    return {
      pairId: englishSource,
      kind: "repository-only",
      locale: chinese ? "zh-CN" : "en",
      canonicalRoute: null,
      route: null,
      output: null,
      lock: true,
    };
  }

  const outputWithoutLocale = docsRelative.replace(
    /(^|\/)README\.md$/iu,
    "$1index.md",
  );
  const canonicalRoute = routeForOutput(outputWithoutLocale);
  return {
    pairId: englishSource,
    kind: "documentation",
    locale: chinese ? "zh-CN" : "en",
    canonicalRoute,
    route: chinese ? `zh-cn/${canonicalRoute}` : canonicalRoute,
    output: chinese ? `zh-cn/${outputWithoutLocale}` : outputWithoutLocale,
    lock: true,
  };
}

function parseMarkdown(content) {
  return markdownStructure(content);
}

function proseText(content) {
  return markdownProseText(content);
}

function externalUrls(content) {
  return markdownExternalUrls(content).sort(compareCodePoints);
}

function normalizedLocalizedLiteral(literal) {
  return literal.replace(/\.zh-CN\.md/gu, ".md");
}

function markdownCodeSpans(content) {
  const spans = [];
  let cursor = 0;
  while (cursor < content.length) {
    const opening = content.indexOf("`", cursor);
    if (opening === -1) break;

    let openingEnd = opening;
    while (content[openingEnd] === "`") openingEnd += 1;
    const delimiterLength = openingEnd - opening;
    let search = openingEnd;
    let closing = -1;
    let closingEnd = -1;
    while (search < content.length) {
      const candidate = content.indexOf("`", search);
      if (candidate === -1) break;
      let candidateEnd = candidate;
      while (content[candidateEnd] === "`") candidateEnd += 1;
      if (candidateEnd - candidate === delimiterLength) {
        closing = candidate;
        closingEnd = candidateEnd;
        break;
      }
      search = candidateEnd;
    }
    if (closing === -1) {
      cursor = openingEnd;
      continue;
    }

    spans.push({
      start: opening,
      end: closingEnd,
      content: content.slice(openingEnd, closing),
    });
    cursor = closingEnd;
  }
  return spans;
}

function markdownCodeSpanLiterals(content) {
  return markdownCodeSpans(content).map((span) =>
    normalizedLocalizedLiteral(span.content.replace(/\s+/gu, " ").trim()),
  );
}

function inlineCodeLiterals(content) {
  return markdownInlineCodeLiterals(content)
    .map(normalizedLocalizedLiteral)
    .sort(compareCodePoints);
}

function pathLikeLiterals(content) {
  const candidates =
    content.match(
      /[A-Za-z]:[\\/][^\s<>"'`(){}\[\],;]+|(?<![\p{L}\p{N}_.@-])(?:\.{1,2}|~)?[\\/][^\s<>"'`(){}\[\],;]+|(?<![\p{L}\p{N}_.@/\\-])[A-Za-z0-9_.@-]+(?:[\\/][A-Za-z0-9_.@-]+)+[\\/]?/gu,
    ) ?? [];
  return candidates.filter((candidate) => {
    if (/^(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|~[\\/]|[\\/])/u.test(candidate)) {
      return true;
    }
    return (
      candidate.endsWith("/") ||
      candidate.endsWith("\\") ||
      candidate.includes(".")
    );
  });
}

function technicalFenceLiterals(content, info) {
  const literals = [];
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (
      /^[!/][a-z]/iu.test(trimmed) ||
      /^(?:awesome|cd|git|uv|npm|python3?|node|curl|irm|sh|rm)\b/u.test(
        trimmed,
      ) ||
      /^(?:Get|Invoke|Remove)-[A-Za-z]+\b/u.test(trimmed) ||
      /^[A-Za-z_][A-Za-z0-9_]*=/u.test(trimmed)
    ) {
      literals.push(`line:${trimmed.replace(/\s+/gu, " ")}`);
    }
  }
  if (info === "markdown" || info === "md") {
    for (const literal of markdownCodeSpanLiterals(content)) {
      literals.push(`code:${literal}`);
    }
  }
  for (const literal of pathLikeLiterals(content)) {
    literals.push(`path:${normalizedLocalizedLiteral(literal)}`);
  }
  for (const match of content.matchAll(
    /@[A-Za-z0-9_-]+[./\\][A-Za-z0-9_./\\-]*|<[A-Za-z0-9_.-]+>|[a-z][a-z0-9]*_[a-z0-9_]+|[A-Z][A-Z0-9_]{2,}/gu,
  )) {
    literals.push(`token:${normalizedLocalizedLiteral(match[0])}`);
  }
  return literals.sort(compareCodePoints);
}

function multisetDifference(left, right) {
  const counts = new Map();
  for (const value of left) counts.set(value, (counts.get(value) ?? 0) + 1);
  for (const value of right) counts.set(value, (counts.get(value) ?? 0) - 1);
  return [...counts].filter(([, count]) => count !== 0);
}

function sameMultiset(left, right) {
  return multisetDifference(left, right).length === 0;
}

export function translationContractFailures(
  english,
  chinese,
  label = "translation",
) {
  const failures = [];
  const normalizedEnglish = normalizeSourceText(english);
  const normalizedChinese = normalizeSourceText(chinese);

  if (!/[\u3400-\u9fff]/u.test(normalizedChinese)) {
    failures.push(`${label} contains no Simplified Chinese content`);
  }
  if (normalizedChinese.length < normalizedEnglish.length * 0.35) {
    failures.push(`${label} is too short to preserve the English source`);
  }

  const prose = proseText(normalizedChinese);
  const cjkCount = [...prose.matchAll(/[\u3400-\u9fff]/gu)].length;
  const latinCount = [...prose.matchAll(/[A-Za-z]/gu)].length;
  const languageCharacters = cjkCount + latinCount;
  const cjkShare = languageCharacters === 0 ? 0 : cjkCount / languageCharacters;
  if (cjkShare < 0.25) {
    failures.push(
      `${label} has too little Simplified Chinese prose ` +
        `(${Math.round(cjkShare * 100)}% CJK; minimum 25%)`,
    );
  }

  let englishShape;
  let chineseShape;
  try {
    englishShape = parseMarkdown(normalizedEnglish);
  } catch (error) {
    failures.push(`${label} English source ${error.message}`);
  }
  try {
    chineseShape = parseMarkdown(normalizedChinese);
  } catch (error) {
    failures.push(`${label} ${error.message}`);
  }

  if (englishShape && chineseShape) {
    const englishStructure = {
      headings: englishShape.headings,
      fences: englishShape.fences.map((fence) => fence.info),
    };
    const chineseStructure = {
      headings: chineseShape.headings,
      fences: chineseShape.fences.map((fence) => fence.info),
    };
    if (JSON.stringify(englishStructure) !== JSON.stringify(chineseStructure)) {
      failures.push(`${label} does not preserve heading/fence structure`);
    } else {
      for (let index = 0; index < englishShape.fences.length; index += 1) {
        const englishFence = englishShape.fences[index];
        const chineseFence = chineseShape.fences[index];
        if (TRANSLATABLE_FENCE_LANGUAGES.has(englishFence.info)) {
          if (
            !sameMultiset(
              technicalFenceLiterals(englishFence.content, englishFence.info),
              technicalFenceLiterals(chineseFence.content, chineseFence.info),
            )
          ) {
            failures.push(
              `${label} changes technical literals in fenced block ${index + 1}`,
            );
          }
        } else if (englishFence.content !== chineseFence.content) {
          failures.push(
            `${label} changes non-Mermaid fenced code block ${index + 1}`,
          );
        }
      }
    }
  }

  if (
    !sameMultiset(
      externalUrls(normalizedEnglish),
      externalUrls(normalizedChinese),
    )
  ) {
    failures.push(
      `${label} does not preserve external URLs with duplicate counts`,
    );
  }
  if (
    !sameMultiset(
      inlineCodeLiterals(normalizedEnglish),
      inlineCodeLiterals(normalizedChinese),
    )
  ) {
    failures.push(
      `${label} does not preserve inline-code literals with duplicate counts`,
    );
  }
  return failures;
}

function repositoryReadmeTranslationContractFailures(
  english,
  chinese,
  label,
) {
  const failures = translationContractFailures(
    english,
    chinese.replaceAll(CHINESE_DOCUMENTATION_HOME, ENGLISH_DOCUMENTATION_HOME),
    label,
  );
  const englishHomeCount = externalUrls(english).filter(
    (url) => url === ENGLISH_DOCUMENTATION_HOME,
  ).length;
  const chineseHomeCount = externalUrls(chinese).filter(
    (url) => url === CHINESE_DOCUMENTATION_HOME,
  ).length;
  if (englishHomeCount === 0 || chineseHomeCount !== englishHomeCount) {
    failures.push(
      `${label} must preserve the localized documentation-home URL count`,
    );
  }
  return failures;
}

function collisionFailures(entries, field) {
  const owners = new Map();
  for (const entry of entries) {
    const value = entry[field];
    if (value === null) continue;
    const previous = owners.get(value);
    if (previous) {
      owners.set(value, [...previous, entry.source]);
    } else {
      owners.set(value, [entry.source]);
    }
  }
  return [...owners]
    .filter(([, sources]) => sources.length > 1)
    .map(
      ([value, sources]) =>
        `${field} collision ${JSON.stringify(value)}: ${sources.sort(compareCodePoints).join(", ")}`,
    );
}

function exactNavigationFailures(entries, navigationRoutes) {
  const failures = [];
  const routeCounts = new Map();
  for (const route of navigationRoutes) {
    routeCounts.set(route, (routeCounts.get(route) ?? 0) + 1);
  }
  for (const [route, count] of routeCounts) {
    if (count !== 1) failures.push(`duplicate sidebar route: ${route}`);
  }

  const canonicalRoutes = new Set(
    entries
      .filter(
        (entry) =>
          entry.locale === "en" &&
          entry.route !== null &&
          entry.kind !== "homepage",
      )
      .map((entry) => entry.route),
  );
  const sidebarSet = new Set(navigationRoutes);
  for (const route of canonicalRoutes) {
    if (!sidebarSet.has(route))
      failures.push(`canonical page is not navigated: ${route}`);
  }
  for (const route of sidebarSet) {
    if (!canonicalRoutes.has(route))
      failures.push(`sidebar route has no canonical page: ${route}`);
  }
  return failures;
}

function markdownLinkDestinations(content) {
  return markdownLinks(content);
}

function externalMarkdownDestination(destination) {
  return (
    destination.startsWith("//") ||
    /^[A-Za-z][A-Za-z0-9+.-]*:/u.test(destination)
  );
}

function markdownSourceTarget(source, destination) {
  const path = destination.split(/[?#]/u, 1)[0];
  return posix.normalize(posix.join(posix.dirname(source), path));
}

function localMarkdownLinkContract(entries, allowedRepositoryMarkdownTargets) {
  const failures = [];
  const targetsBySource = new Map();
  const sources = new Map(entries.map((entry) => [entry.source, entry]));
  const allowed = new Map();
  for (const record of allowedRepositoryMarkdownTargets) {
    let source;
    try {
      source = normalizedSourcePath(record.source);
    } catch (error) {
      failures.push(`allowed repository Markdown target ${error.message}`);
      continue;
    }
    if (record.locale !== "en" && record.locale !== "zh-CN") {
      failures.push(`${source} has invalid allowed repository Markdown locale`);
      continue;
    }
    const localizedName = source.endsWith(".zh-CN.md");
    if ((record.locale === "zh-CN") !== localizedName) {
      failures.push(
        `${source} does not match its allowed repository Markdown locale`,
      );
      continue;
    }
    if (sources.has(source)) {
      failures.push(
        `${source} is both a catalog source and an allowed external target`,
      );
      continue;
    }
    if (allowed.has(source)) {
      failures.push(`duplicate allowed repository Markdown target: ${source}`);
      continue;
    }
    allowed.set(source, record.locale);
  }

  for (const entry of entries) {
    const logicalTargets = [];
    if (entry.kind === "homepage-content") {
      targetsBySource.set(entry.source, logicalTargets);
      continue;
    }
    for (const link of markdownLinkDestinations(entry.content)) {
      if (externalMarkdownDestination(link.destination)) continue;
      if (link.destination.startsWith("#")) {
        logicalTargets.push(entry.pairId);
        continue;
      }
      const linkPath = link.destination.split(/[?#]/u, 1)[0];
      if (!/\.md$/iu.test(linkPath)) {
        failures.push(
          `${entry.source}:${link.line} local Markdown link must target an ` +
            `explicit .md source: ${link.destination}`,
        );
        continue;
      }
      const targetSource = markdownSourceTarget(entry.source, link.destination);
      if (
        targetSource === ".." ||
        targetSource.startsWith("../") ||
        targetSource.startsWith("/")
      ) {
        failures.push(
          `${entry.source}:${link.line} local Markdown link escapes the repository: ${link.destination}`,
        );
        continue;
      }
      const target = sources.get(targetSource);
      const targetLocale = target?.locale ?? allowed.get(targetSource);
      if (targetLocale === undefined) {
        failures.push(
          `${entry.source}:${link.line} local Markdown target is not in the catalog or allowlist: ${targetSource}`,
        );
        continue;
      }
      if (
        targetLocale !== entry.locale &&
        (target === undefined || target.pairId !== entry.pairId)
      ) {
        failures.push(
          `${entry.source}:${link.line} local Markdown link crosses locale ` +
            `(${entry.locale} -> ${targetLocale}): ${targetSource}`,
        );
        continue;
      }
      logicalTargets.push(
        target?.pairId ?? normalizedLocalizedLiteral(targetSource),
      );
    }
    targetsBySource.set(entry.source, logicalTargets.sort(compareCodePoints));
  }
  return { failures, targetsBySource };
}

export function compileDocumentationCatalogFromSources({
  sources,
  navigationRoutes,
  allowedRepositoryMarkdownTargets = DEFAULT_ALLOWED_REPOSITORY_MARKDOWN_TARGETS,
}) {
  const failures = [];
  const sourceOwners = new Map();
  const portableSourceOwners = new Map();
  const entries = [];

  for (const record of sources) {
    let source;
    try {
      source = normalizedSourcePath(record.source);
    } catch (error) {
      failures.push(error.message);
      continue;
    }
    if (sourceOwners.has(source)) {
      failures.push(`source collision ${JSON.stringify(source)}`);
      continue;
    }
    sourceOwners.set(source, true);
    const portableSourceKey = source.toLowerCase();
    const portableSourceOwner = portableSourceOwners.get(portableSourceKey);
    if (portableSourceOwner !== undefined) {
      failures.push(
        `source portability collision ${JSON.stringify(portableSourceOwner)} and ` +
          `${JSON.stringify(source)}`,
      );
      continue;
    }
    portableSourceOwners.set(portableSourceKey, source);

    try {
      const description = describeSource(source);
      assertEntryIdentities(description, source);
      const content = normalizeSourceText(record.content);
      entries.push({
        ...description,
        source,
        absolutePath: record.absolutePath ?? null,
        content,
        sha256: normalizedSourceHash(content),
      });
    } catch (error) {
      failures.push(error.message);
    }
  }

  for (const route of navigationRoutes) {
    try {
      assertRouteIdentity(route, "Sidebar route");
    } catch (error) {
      failures.push(error.message);
    }
  }

  failures.push(...collisionFailures(entries, "route"));
  failures.push(...collisionFailures(entries, "output"));
  const localMarkdownLinks = localMarkdownLinkContract(
    entries,
    allowedRepositoryMarkdownTargets,
  );
  failures.push(...localMarkdownLinks.failures);

  const pairOwners = new Map();
  for (const entry of entries) {
    const pair = pairOwners.get(entry.pairId) ?? new Map();
    if (pair.has(entry.locale)) {
      failures.push(`${entry.pairId} has duplicate ${entry.locale} sources`);
    } else {
      pair.set(entry.locale, entry);
    }
    pairOwners.set(entry.pairId, pair);
  }

  const pairs = [];
  for (const [pairId, locales] of [...pairOwners].sort(([left], [right]) =>
    compareCodePoints(left, right),
  )) {
    const english = locales.get("en");
    const chinese = locales.get("zh-CN");
    if (!english)
      failures.push(`${pairId} has an orphan Simplified Chinese source`);
    if (!chinese) failures.push(`${pairId} has no Simplified Chinese source`);
    if (!english || !chinese) continue;
    if (
      english.kind !== chinese.kind ||
      english.canonicalRoute !== chinese.canonicalRoute ||
      english.lock !== chinese.lock
    ) {
      failures.push(
        `${pairId} locale sources do not describe the same catalog entry`,
      );
      continue;
    }
    pairs.push({ pairId, english, chinese, lock: english.lock });
    if (english.kind === "homepage-content") {
      try {
        validateHomepageContentSources({
          english: JSON.parse(english.content),
          chinese: JSON.parse(chinese.content),
        });
      } catch (error) {
        failures.push(`${chinese.source} ${error.message}`);
      }
    } else if (english.kind === "repository-readme") {
      failures.push(
        ...repositoryReadmeTranslationContractFailures(
          english.content,
          chinese.content,
          chinese.source,
        ),
      );
    } else {
      failures.push(
        ...translationContractFailures(
          english.content,
          chinese.content,
          chinese.source,
        ),
      );
    }
    if (
      !sameMultiset(
        localMarkdownLinks.targetsBySource.get(english.source) ?? [],
        localMarkdownLinks.targetsBySource.get(chinese.source) ?? [],
      )
    ) {
      failures.push(
        `${chinese.source} does not preserve local Markdown target pairs ` +
          "with duplicate counts",
      );
    }
  }

  failures.push(...exactNavigationFailures(entries, navigationRoutes));
  if (failures.length > 0) {
    throw new Error(
      "Documentation catalog contract failed:\n" +
        failures
          .sort(compareCodePoints)
          .map((failure) => `- ${failure}`)
          .join("\n"),
    );
  }

  entries.sort((left, right) => compareCodePoints(left.source, right.source));
  const pages = entries
    .filter((entry) => entry.output !== null)
    .sort((left, right) => compareCodePoints(left.output, right.output));
  const lockPairs = pairs
    .filter((pair) => pair.lock)
    .sort((left, right) =>
      compareCodePoints(left.english.source, right.english.source),
    );
  return {
    entries,
    pages,
    pairs,
    lockPairs,
    navigationRoutes: [...navigationRoutes],
    bySource: new Map(entries.map((entry) => [entry.source, entry])),
    byRoute: new Map(pages.map((entry) => [entry.route, entry])),
    byOutput: new Map(pages.map((entry) => [entry.output, entry])),
  };
}

function assertExactFields(value, expected, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const actual = Object.keys(value).sort(compareCodePoints);
  const wanted = [...expected].sort(compareCodePoints);
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
    throw new Error(
      `${label} fields must be exactly ${wanted.join(", ")}; received ${actual.join(", ")}`,
    );
  }
}

export function parseTranslationLock(raw) {
  let parsed;
  try {
    parsed = JSON.parse(normalizeSourceText(raw));
  } catch (error) {
    throw new Error(`Translation lock is not valid JSON: ${error.message}`);
  }
  assertExactFields(parsed, LOCK_TOP_LEVEL_FIELDS, "translation lock");
  if (parsed.version !== TRANSLATION_LOCK_VERSION) {
    throw new Error(`Unsupported translation lock version: ${parsed.version}`);
  }
  if (parsed.hash_algorithm !== TRANSLATION_LOCK_HASH_ALGORITHM) {
    throw new Error(
      `Unsupported translation lock hash algorithm: ${parsed.hash_algorithm}`,
    );
  }
  if (parsed.text_normalization !== TRANSLATION_LOCK_TEXT_NORMALIZATION) {
    throw new Error(
      `Unsupported translation lock text normalization: ${parsed.text_normalization}`,
    );
  }
  if (!Array.isArray(parsed.pairs))
    throw new Error("translation lock pairs must be an array");
  for (let index = 0; index < parsed.pairs.length; index += 1) {
    const pair = parsed.pairs[index];
    assertExactFields(pair, LOCK_PAIR_FIELDS, `translation lock pair ${index}`);
    for (const field of ["english_source", "chinese_source"]) {
      if (
        typeof pair[field] !== "string" ||
        pair[field] !== normalizedSourcePath(pair[field])
      ) {
        throw new Error(`translation lock pair ${index} has invalid ${field}`);
      }
    }
    for (const field of ["english_sha256", "chinese_sha256"]) {
      if (typeof pair[field] !== "string" || !HASH_PATTERN.test(pair[field])) {
        throw new Error(`translation lock pair ${index} has invalid ${field}`);
      }
    }
  }
  return parsed;
}

export function createTranslationLock(catalog) {
  return {
    version: TRANSLATION_LOCK_VERSION,
    hash_algorithm: TRANSLATION_LOCK_HASH_ALGORITHM,
    text_normalization: TRANSLATION_LOCK_TEXT_NORMALIZATION,
    pairs: catalog.lockPairs.map((pair) => ({
      english_source: pair.english.source,
      chinese_source: pair.chinese.source,
      english_sha256: pair.english.sha256,
      chinese_sha256: pair.chinese.sha256,
    })),
  };
}

export function validateTranslationLock(catalog, lock) {
  const normalizedLock = parseTranslationLock(JSON.stringify(lock));
  const failures = [];
  const expected = new Map(
    catalog.lockPairs.map((pair) => [pair.english.source, pair]),
  );
  const seenEnglish = new Set();
  const seenChinese = new Set();
  let previousSource = null;

  for (const pair of normalizedLock.pairs) {
    if (seenEnglish.has(pair.english_source)) {
      failures.push(`duplicate lock English source: ${pair.english_source}`);
    }
    if (seenChinese.has(pair.chinese_source)) {
      failures.push(
        `duplicate lock Simplified Chinese source: ${pair.chinese_source}`,
      );
    }
    seenEnglish.add(pair.english_source);
    seenChinese.add(pair.chinese_source);
    if (
      previousSource !== null &&
      compareCodePoints(previousSource, pair.english_source) >= 0
    ) {
      failures.push(
        "translation lock pairs are not strictly sorted by English source",
      );
    }
    previousSource = pair.english_source;

    const current = expected.get(pair.english_source);
    if (!current) {
      failures.push(
        `unexpected translation lock entry: ${pair.english_source}`,
      );
      continue;
    }
    if (pair.chinese_source !== current.chinese.source) {
      failures.push(
        `${pair.english_source} lock translation is ${pair.chinese_source}; ` +
          `expected ${current.chinese.source}`,
      );
    }
    if (pair.english_sha256 !== current.english.sha256) {
      failures.push(`${pair.english_source} English hash is stale`);
    }
    if (pair.chinese_sha256 !== current.chinese.sha256) {
      failures.push(
        `${current.chinese.source} Simplified Chinese hash is stale`,
      );
    }
  }

  for (const source of expected.keys()) {
    if (!seenEnglish.has(source))
      failures.push(`missing translation lock entry: ${source}`);
  }
  if (failures.length > 0) {
    throw new Error(
      "Translation lock contract failed:\n" +
        failures
          .sort(compareCodePoints)
          .map((failure) => `- ${failure}`)
          .join("\n"),
    );
  }
}

async function loadSourceRecord(inputReader, source) {
  const { absolutePath, content } = await inputReader.readUtf8Source(
    source,
    `Documentation source ${source}`,
  );
  return { source, absolutePath, content: normalizeSourceText(content) };
}

export async function compileDocumentationCatalog({
  repositoryRoot = defaultRepositoryRoot,
  navigationRoutes = collectSidebarRoutes(docsSidebar),
  verifyTranslationLock = true,
  translationLockPath = join(repositoryRoot, "site", "translation-lock.json"),
  allowedRepositoryMarkdownTargets = DEFAULT_ALLOWED_REPOSITORY_MARKDOWN_TARGETS,
} = {}) {
  const inputReader = await createDocumentationInputReader({ repositoryRoot });
  const docsDirectory = join(repositoryRoot, "docs");
  const sources = [
    "README.md",
    "README.zh-CN.md",
    "site/content/index.mdx",
    "site/content/index.zh-cn.mdx",
    "site/homepage-content.en.json",
    "site/homepage-content.zh-CN.json",
    ...(await inputReader.listMarkdownSources(docsDirectory)),
    "ARCHITECTURE.md",
    "ARCHITECTURE.zh-CN.md",
  ].sort(compareCodePoints);
  const records = await Promise.all(
    sources.map((source) => loadSourceRecord(inputReader, source)),
  );
  const catalog = compileDocumentationCatalogFromSources({
    sources: records,
    navigationRoutes,
    allowedRepositoryMarkdownTargets,
  });

  if (verifyTranslationLock) {
    const { content: lockRaw } = await inputReader.readUtf8Path(
      translationLockPath,
      "Translation lock",
    );
    validateTranslationLock(catalog, parseTranslationLock(lockRaw));
  }
  await inputReader.assertRepositoryStable();

  return {
    ...catalog,
    repositoryRoot,
    translationLockPath,
  };
}

export function resolveCatalogSourceLink(catalog, source, markdownTarget) {
  const sourceEntry = catalog.bySource.get(normalizedSourcePath(source));
  if (!sourceEntry) return null;
  const targetSource = posix.normalize(
    posix.join(posix.dirname(sourceEntry.source), markdownTarget),
  );
  if (targetSource === ".." || targetSource.startsWith("../")) return null;
  const targetEntry = catalog.bySource.get(targetSource);
  if (!targetEntry) return null;
  if (
    targetEntry.kind !== "repository-only" &&
    targetEntry.kind !== "repository-readme"
  ) {
    return targetEntry;
  }
  return catalog.entries.find(
    (entry) => entry.kind === "homepage" && entry.locale === sourceEntry.locale,
  );
}
