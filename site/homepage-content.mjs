const TOP_LEVEL_FIELDS = [
  "schemaVersion",
  "locale",
  "kicker",
  "title",
  "lead",
  "primaryAction",
  "secondaryAction",
  "copy",
  "copied",
  "installPlatform",
  "installReview",
  "previewLabel",
  "userPrompt",
  "assistantReply",
  "boundariesLabel",
  "proofPills",
  "boundaries",
  "loopEyebrow",
  "loopTitle",
  "loopLead",
  "loop",
  "resourcesEyebrow",
  "resourcesTitle",
  "resourcesLead",
  "resources",
  "closingTitle",
  "closingLead",
  "closingAction",
];
const PROOF_PILL_FIELDS = ["id", "text"];
const BOUNDARY_FIELDS = ["id", "eyebrow", "title", "description"];
const LOOP_FIELDS = ["id", "number", "title", "description"];
const RESOURCE_FIELDS = ["id", "eyebrow", "title", "description"];
const PROOF_PILL_IDS = ["approval-default", "single-operation", "change-journal"];
const BOUNDARY_IDS = ["core", "control", "verify", "recover"];
const LOOP_IDS = ["understand", "plan", "change", "verify"];
const RESOURCE_IDS = [
  "get-started",
  "daily-work",
  "control",
  "extend",
  "understand",
  "contribute",
];
const LOCALIZED_TOP_LEVEL_FIELDS = [
  "kicker",
  "title",
  "lead",
  "primaryAction",
  "secondaryAction",
  "copy",
  "copied",
  "installPlatform",
  "installReview",
  "previewLabel",
  "userPrompt",
  "assistantReply",
  "boundariesLabel",
  "loopTitle",
  "loopLead",
  "resourcesTitle",
  "resourcesLead",
  "closingTitle",
  "closingLead",
  "closingAction",
];
const SHARED_TOP_LEVEL_FIELDS = ["loopEyebrow", "resourcesEyebrow"];
const CJK_PATTERN = /[\u3400-\u9fff]/u;
const LATIN_PATTERN = /[A-Za-z]/u;
const FORBIDDEN_CHINESE_FALLBACKS = [
  /untranslated/iu,
  /only available in english/iu,
  /english fallback/iu,
  /英文回退/u,
  /尚未翻译/u,
];

export const HOMEPAGE_CONTENT_SOURCE_PATHS = Object.freeze({
  en: "site/homepage-content.en.json",
  "zh-CN": "site/homepage-content.zh-CN.json",
});

const CANONICAL_RESOURCE_ROUTES = Object.freeze({
  "get-started": "./getting-started/quickstart/",
  "daily-work": "./user-guide/",
  control: "./user-guide/permissions/",
  extend: "./extensions/",
  understand: "./architecture/overview/",
  contribute: "./development/",
});

export const HOMEPAGE_RESOURCE_ROUTES = CANONICAL_RESOURCE_ROUTES;

export class HomepageContentContractError extends Error {
  constructor(message) {
    super(`Homepage content contract failed: ${message}`);
    this.name = "HomepageContentContractError";
  }
}

function fail(message) {
  throw new HomepageContentContractError(message);
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function assertRecord(value, label) {
  if (!isRecord(value)) fail(`${label} must be an object`);
}

function assertExactFields(value, expectedFields, label) {
  assertRecord(value, label);
  const actual = Object.keys(value).sort();
  const expected = [...expectedFields].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    fail(
      `${label} fields must be exactly ${expected.join(", ")}; ` +
        `received ${actual.join(", ")}`,
    );
  }
}

function assertString(value, label) {
  if (typeof value !== "string" || value.trim() !== value || value.length === 0) {
    fail(`${label} must be a non-empty, trimmed string`);
  }
}

function assertCollection(value, expectedIds, fields, label) {
  if (!Array.isArray(value)) fail(`${label} must be an array`);
  if (value.length !== expectedIds.length) {
    fail(`${label} must contain exactly ${expectedIds.length} items`);
  }
  const seen = new Set();
  for (let index = 0; index < value.length; index += 1) {
    const item = value[index];
    assertExactFields(item, fields, `${label}[${index}]`);
    for (const field of fields) assertString(item[field], `${label}[${index}].${field}`);
    if (seen.has(item.id)) fail(`${label} contains duplicate id ${item.id}`);
    seen.add(item.id);
    if (item.id !== expectedIds[index]) {
      fail(`${label}[${index}].id must be ${expectedIds[index]}; received ${item.id}`);
    }
  }
}

function localizedStrings(source) {
  const strings = LOCALIZED_TOP_LEVEL_FIELDS.map((field) => ({
    path: field,
    value: source[field],
  }));
  for (const [index, item] of source.proofPills.entries()) {
    strings.push({ path: `proofPills[${index}].text`, value: item.text });
  }
  for (const [collectionName, fields] of [
    ["boundaries", ["title", "description"]],
    ["loop", ["title", "description"]],
    ["resources", ["title", "description"]],
  ]) {
    for (const [index, item] of source[collectionName].entries()) {
      for (const field of fields) {
        strings.push({
          path: `${collectionName}[${index}].${field}`,
          value: item[field],
        });
      }
    }
  }
  return strings;
}

function validateLanguage(source, expectedLocale, label) {
  const strings = localizedStrings(source);
  for (const entry of strings) {
    assertString(entry.value, `${label}.${entry.path}`);
    if (expectedLocale === "en") {
      if (CJK_PATTERN.test(entry.value)) {
        fail(`${label}.${entry.path} must not contain Simplified Chinese`);
      }
      if (!LATIN_PATTERN.test(entry.value)) {
        fail(`${label}.${entry.path} must contain English prose`);
      }
    } else if (!CJK_PATTERN.test(entry.value)) {
      fail(`${label}.${entry.path} must contain Simplified Chinese; English fallback is forbidden`);
    } else {
      const cjkCount = [...entry.value.matchAll(/[\u3400-\u9fff]/gu)].length;
      const latinCount = [...entry.value.matchAll(/[A-Za-z]/gu)].length;
      if (cjkCount / (cjkCount + latinCount) < 0.1) {
        fail(`${label}.${entry.path} has too little Simplified Chinese prose`);
      }
    }
  }

  if (expectedLocale === "zh-CN") {
    const prose = strings.map((entry) => entry.value).join(" ");
    for (const pattern of FORBIDDEN_CHINESE_FALLBACKS) {
      if (pattern.test(prose)) fail(`${label} contains a forbidden untranslated fallback marker`);
    }
    const cjkCount = [...prose.matchAll(/[\u3400-\u9fff]/gu)].length;
    const latinCount = [...prose.matchAll(/[A-Za-z]/gu)].length;
    if (cjkCount / (cjkCount + latinCount) < 0.25) {
      fail(`${label} has too little Simplified Chinese prose`);
    }
  }
}

function validateSource(source, expectedLocale, label) {
  assertExactFields(source, TOP_LEVEL_FIELDS, label);
  if (source.schemaVersion !== 1) fail(`${label}.schemaVersion must be 1`);
  if (source.locale !== expectedLocale) {
    fail(`${label}.locale must be ${expectedLocale}; received ${source.locale}`);
  }
  for (const field of [...LOCALIZED_TOP_LEVEL_FIELDS, ...SHARED_TOP_LEVEL_FIELDS]) {
    assertString(source[field], `${label}.${field}`);
  }
  assertCollection(source.proofPills, PROOF_PILL_IDS, PROOF_PILL_FIELDS, `${label}.proofPills`);
  assertCollection(source.boundaries, BOUNDARY_IDS, BOUNDARY_FIELDS, `${label}.boundaries`);
  assertCollection(source.loop, LOOP_IDS, LOOP_FIELDS, `${label}.loop`);
  assertCollection(source.resources, RESOURCE_IDS, RESOURCE_FIELDS, `${label}.resources`);
  validateLanguage(source, expectedLocale, label);
}

function assertSharedParity(english, chinese) {
  for (const field of SHARED_TOP_LEVEL_FIELDS) {
    if (english[field] !== chinese[field]) fail(`${field} must match across locales`);
  }
  for (const [collectionName, sharedFields] of [
    ["proofPills", ["id"]],
    ["boundaries", ["id", "eyebrow"]],
    ["loop", ["id", "number"]],
    ["resources", ["id", "eyebrow"]],
  ]) {
    for (let index = 0; index < english[collectionName].length; index += 1) {
      for (const field of sharedFields) {
        if (english[collectionName][index][field] !== chinese[collectionName][index][field]) {
          fail(`${collectionName}[${index}].${field} must match across locales`);
        }
      }
    }
  }
}

function validateResourceRoutes(resourceRoutes) {
  assertExactFields(resourceRoutes, RESOURCE_IDS, "resource routes");
  for (const id of RESOURCE_IDS) {
    assertString(resourceRoutes[id], `resource routes.${id}`);
    if (resourceRoutes[id] !== CANONICAL_RESOURCE_ROUTES[id]) {
      fail(
        `resource route ${id} must be ${CANONICAL_RESOURCE_ROUTES[id]}; ` +
          `received ${resourceRoutes[id]}`,
      );
    }
  }
}

function deepFreeze(value) {
  if (!isRecord(value) && !Array.isArray(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function materializeSource(source, resourceRoutes) {
  const content = structuredClone(source);
  content.resources = content.resources.map((resource) => ({
    ...resource,
    href: resourceRoutes[resource.id],
  }));
  return deepFreeze(content);
}

export function validateHomepageContentSources({
  english,
  chinese,
  resourceRoutes = HOMEPAGE_RESOURCE_ROUTES,
}) {
  validateSource(english, "en", "English homepage");
  validateSource(chinese, "zh-CN", "Simplified Chinese homepage");
  assertSharedParity(english, chinese);
  validateResourceRoutes(resourceRoutes);
}

export function compileHomepageContentSources({
  english,
  chinese,
  resourceRoutes = HOMEPAGE_RESOURCE_ROUTES,
}) {
  validateHomepageContentSources({ english, chinese, resourceRoutes });
  return deepFreeze({
    en: materializeSource(english, resourceRoutes),
    "zh-CN": materializeSource(chinese, resourceRoutes),
  });
}
