import { lstat, realpath } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";

import { markdownLinks } from "./markdown-ast.mjs";
import { parseSitemapXml } from "./semantic-xml.mjs";

export class BuiltSiteContractError extends Error {
  constructor(message) {
    super(message);
    this.name = "BuiltSiteContractError";
  }
}

export function normalizeBasePath(configuredBasePath) {
  if (configuredBasePath === undefined || configuredBasePath === null) {
    return "/awesome_agent";
  }
  if (configuredBasePath === "" || configuredBasePath === "/") return "";
  if (typeof configuredBasePath !== "string" || !configuredBasePath.startsWith("/")) {
    throw new BuiltSiteContractError("BASE_PATH must be empty, '/', or an absolute URL path.");
  }
  if (
    configuredBasePath.includes("\\") ||
    configuredBasePath.includes("%") ||
    configuredBasePath.includes("?") ||
    configuredBasePath.includes("#") ||
    /[\u0000-\u001f\u007f]/u.test(configuredBasePath)
  ) {
    throw new BuiltSiteContractError("BASE_PATH contains an unsafe character.");
  }

  const normalized = configuredBasePath.replace(/\/+$/u, "");
  const segments = normalized.slice(1).split("/");
  if (
    segments.some(
      (segment) =>
        !segment ||
        segment === "." ||
        segment === ".." ||
        segment.includes(":") ||
        /[. ]$/u.test(segment) ||
        !/^[A-Za-z0-9][A-Za-z0-9._-]*$/u.test(segment),
    )
  ) {
    throw new BuiltSiteContractError(
      "BASE_PATH contains an empty, dot, reserved, or non-portable segment.",
    );
  }
  return normalized;
}

function assertCanonicalRoute(route) {
  if (typeof route !== "string" || !route || route !== route.trim()) {
    throw new BuiltSiteContractError(`Invalid canonical route: ${JSON.stringify(route)}`);
  }
  if (
    route.startsWith("/") ||
    route.endsWith("/") ||
    route.includes("\\") ||
    route.includes("%") ||
    route.includes("?") ||
    route.includes("#") ||
    /[\u0000-\u001f\u007f]/u.test(route)
  ) {
    throw new BuiltSiteContractError(`Unsafe canonical route: ${JSON.stringify(route)}`);
  }
  const segments = route.split("/");
  if (
    segments.some(
      (segment) =>
        !segment ||
        segment === "." ||
        segment === ".." ||
        segment.includes(":") ||
        /[. ]$/u.test(segment) ||
        !/^[a-z0-9]+(?:-[a-z0-9]+)*$/u.test(segment),
    )
  ) {
    throw new BuiltSiteContractError(
      `Unsafe or non-canonical route segment: ${JSON.stringify(route)}`,
    );
  }
}

function assertPortableFilePath(filePath) {
  if (typeof filePath !== "string" || !filePath || filePath !== filePath.trim()) {
    throw new BuiltSiteContractError(`Invalid public file path: ${JSON.stringify(filePath)}`);
  }
  if (
    filePath.startsWith("/") ||
    filePath.endsWith("/") ||
    filePath.includes("\\") ||
    filePath.includes("%") ||
    filePath.includes("?") ||
    filePath.includes("#") ||
    /[\u0000-\u001f\u007f]/u.test(filePath)
  ) {
    throw new BuiltSiteContractError(`Unsafe public file path: ${JSON.stringify(filePath)}`);
  }
  if (
    filePath.split("/").some(
      (segment) =>
        !/^[a-z0-9][a-z0-9._-]*$/u.test(segment) ||
        segment === "." ||
        segment === ".." ||
        /[. ]$/u.test(segment),
    )
  ) {
    throw new BuiltSiteContractError(
      `Unsafe or non-portable public file path: ${JSON.stringify(filePath)}`,
    );
  }
}

function exactPublicUrl(pathname, origin) {
  const url = new URL(pathname, origin);
  if (url.pathname !== pathname || url.search || url.hash) {
    throw new BuiltSiteContractError(
      `Public URL normalization changed the configured path: ${pathname}`,
    );
  }
  return url.href;
}

export function publicUrlForRoute(route, { basePath, origin }) {
  if (route) assertCanonicalRoute(route);
  const normalizedBase = normalizeBasePath(basePath);
  const suffix = route ? `/${route}/` : "/";
  return exactPublicUrl(`${normalizedBase}${suffix}`, origin);
}

export function publicUrlForFile(filePath, { basePath, origin }) {
  assertPortableFilePath(filePath);
  const normalizedBase = normalizeBasePath(basePath);
  return exactPublicUrl(`${normalizedBase}/${filePath}`, origin);
}

export function buildExpectedSiteContract(routes, { basePath, origin }) {
  const routeCounts = new Map();
  for (const route of routes) {
    assertCanonicalRoute(route);
    if (route === "404" || route === "zh-cn" || route.startsWith("zh-cn/")) {
      throw new BuiltSiteContractError(`Canonical route uses a reserved site path: ${route}`);
    }
    routeCounts.set(route, (routeCounts.get(route) ?? 0) + 1);
  }
  const duplicateRoutes = [...routeCounts]
    .filter(([, count]) => count !== 1)
    .map(([route]) => route);
  if (duplicateRoutes.length > 0) {
    throw new BuiltSiteContractError(
      `Duplicate canonical route(s): ${duplicateRoutes.sort().join(", ")}`,
    );
  }

  const canonicalRoutes = [
    "",
    ...routes,
    "zh-cn",
    ...routes.map((route) => `zh-cn/${route}`),
  ];
  const duplicatePublicRoutes = [...countValues(canonicalRoutes)]
    .filter(([, count]) => count !== 1)
    .map(([route]) => route);
  if (duplicatePublicRoutes.length > 0) {
    throw new BuiltSiteContractError(
      `Duplicate public route(s): ${duplicatePublicRoutes.sort().join(", ")}`,
    );
  }
  const canonicalUrls = canonicalRoutes.map((route) =>
    publicUrlForRoute(route, { basePath, origin }),
  );
  const duplicateCanonicalUrls = [...countValues(canonicalUrls)]
    .filter(([, count]) => count !== 1)
    .map(([url]) => url);
  if (duplicateCanonicalUrls.length > 0) {
    throw new BuiltSiteContractError(
      `Canonical URL collision(s): ${duplicateCanonicalUrls.join(", ")}`,
    );
  }
  const htmlPaths = canonicalRoutes.map((route) =>
    route ? `${route}/index.html` : "index.html",
  );
  htmlPaths.push("404.html");

  return {
    canonicalRoutes,
    canonicalUrls,
    htmlPaths,
    routeToUrl: new Map(
      canonicalRoutes.map((route, index) => [route, canonicalUrls[index]]),
    ),
  };
}

function countValues(values) {
  const counts = new Map();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return counts;
}

export function exactCollectionFailures(actualValues, expectedValues, label) {
  const actual = countValues(actualValues);
  const expected = countValues(expectedValues);
  const failures = [];

  for (const [value, count] of expected) {
    if (count !== 1) {
      throw new BuiltSiteContractError(
        `${label} expected contract contains ${count} copies of ${JSON.stringify(value)}.`,
      );
    }
    const actualCount = actual.get(value) ?? 0;
    if (actualCount === 0) {
      failures.push(`${label}: missing ${value}`);
    } else if (actualCount !== 1) {
      failures.push(`${label}: duplicate ${value} (${actualCount} copies)`);
    }
  }

  for (const [value, count] of actual) {
    if (!expected.has(value)) failures.push(`${label}: unexpected ${value}`);
    if (!expected.has(value) && count !== 1) {
      failures.push(`${label}: duplicate unexpected ${value} (${count} copies)`);
    }
  }
  return failures;
}

export function extractXmlLocations(xml) {
  return parseSitemapXml(xml).locations;
}

export function extractMarkdownLinkTargets(markdown) {
  const links = markdownLinks(markdown);
  return {
    targets: links.filter((link) => link.type === "link").map((link) => link.destination),
    invalidLines: links
      .filter((link) => link.type !== "link")
      .map((link) => `line ${link.line}: reference definitions are not allowed`),
  };
}

function assertContained(root, target, label) {
  const relativeTarget = relative(root, target);
  if (
    relativeTarget === ".." ||
    relativeTarget.startsWith(`..${sep}`) ||
    isAbsolute(relativeTarget)
  ) {
    throw new BuiltSiteContractError(`${label} escapes the built output directory.`);
  }
}

function decodePathname(pathname) {
  if (typeof pathname !== "string" || !pathname.startsWith("/")) {
    throw new BuiltSiteContractError("Local URL pathname must be absolute.");
  }
  if (/%(?:2f|5c)/iu.test(pathname)) {
    throw new BuiltSiteContractError("Encoded slash or backslash is not allowed in a local URL.");
  }

  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    throw new BuiltSiteContractError("Local URL pathname contains invalid percent encoding.");
  }
  if (
    decoded.includes("\\") ||
    /[\u0000-\u001f\u007f]/u.test(decoded) ||
    /%[0-9a-f]{2}/iu.test(decoded)
  ) {
    throw new BuiltSiteContractError(
      "Decoded local URL pathname contains a control, backslash, or ambiguous escape.",
    );
  }
  return decoded;
}

async function ordinaryFile(path, label, fileSystem) {
  let metadata;
  try {
    metadata = await fileSystem.lstat(path);
  } catch {
    throw new BuiltSiteContractError(`${label} does not exist.`);
  }
  if (!metadata.isFile()) {
    throw new BuiltSiteContractError(`${label} is not an ordinary file.`);
  }
}

async function metadataFor(path, fileSystem) {
  try {
    return await fileSystem.lstat(path);
  } catch {
    return null;
  }
}

export async function resolveBuiltOutputPath({
  pathname,
  basePath,
  outputDirectory,
  fileSystem = { lstat, realpath },
}) {
  const normalizedBase = normalizeBasePath(basePath);
  const decodedPathname = decodePathname(pathname);
  if (
    normalizedBase &&
    decodedPathname !== normalizedBase &&
    !decodedPathname.startsWith(`${normalizedBase}/`)
  ) {
    throw new BuiltSiteContractError("Local URL escapes the configured deployment base.");
  }

  const relativeUrlPath = normalizedBase
    ? decodedPathname.slice(normalizedBase.length).replace(/^\//u, "")
    : decodedPathname.replace(/^\//u, "");
  const hasTrailingSlash = decodedPathname.endsWith("/");
  const segments = relativeUrlPath ? relativeUrlPath.split("/") : [];
  if (
    segments.some(
      (segment, index) =>
        (!segment && index !== segments.length - 1) ||
        segment === "." ||
        segment === ".." ||
        segment.includes(":") ||
        /[. ]$/u.test(segment),
    )
  ) {
    throw new BuiltSiteContractError("Local URL contains an empty, dot, or reserved segment.");
  }
  if (segments.at(-1) === "") segments.pop();

  const outputRoot = resolve(outputDirectory);
  let candidate = resolve(outputRoot, ...segments);
  assertContained(outputRoot, candidate, "Local URL target");

  if (segments.length === 0 || hasTrailingSlash) {
    const directoryMetadata = await metadataFor(candidate, fileSystem);
    if (!directoryMetadata?.isDirectory()) {
      throw new BuiltSiteContractError("Local URL directory target does not exist.");
    }
    candidate = resolve(candidate, "index.html");
  } else {
    const directMetadata = await metadataFor(candidate, fileSystem);
    if (directMetadata?.isDirectory()) candidate = resolve(candidate, "index.html");
  }

  assertContained(outputRoot, candidate, "Resolved local URL target");
  await ordinaryFile(candidate, "Resolved local URL target", fileSystem);

  let realOutputRoot;
  let realCandidate;
  try {
    [realOutputRoot, realCandidate] = await Promise.all([
      fileSystem.realpath(outputRoot),
      fileSystem.realpath(candidate),
    ]);
  } catch {
    throw new BuiltSiteContractError("Unable to resolve the built output target identity.");
  }
  assertContained(realOutputRoot, realCandidate, "Resolved local URL target identity");
  return realCandidate;
}
