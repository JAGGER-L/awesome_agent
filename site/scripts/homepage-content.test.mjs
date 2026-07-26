import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  HOMEPAGE_CONTENT_SOURCE_PATHS,
  HOMEPAGE_RESOURCE_ROUTES,
  HomepageContentContractError,
  compileHomepageContentSources,
  validateHomepageContentSources,
} from "../homepage-content.mjs";

function readSource(name) {
  return JSON.parse(readFileSync(new URL(`../${name}`, import.meta.url), "utf8"));
}

const homepageContentSources = {
  en: readSource("homepage-content.en.json"),
  "zh-CN": readSource("homepage-content.zh-CN.json"),
};
const homepageContentByLocale = compileHomepageContentSources({
  english: homepageContentSources.en,
  chinese: homepageContentSources["zh-CN"],
});

function sourceCopies() {
  return {
    english: structuredClone(homepageContentSources.en),
    chinese: structuredClone(homepageContentSources["zh-CN"]),
  };
}

test("canonical homepage sources compile to six aligned bilingual resource cards", () => {
  assert.doesNotThrow(() => validateHomepageContentSources(sourceCopies()));
  assert.deepEqual(HOMEPAGE_CONTENT_SOURCE_PATHS, {
    en: "site/homepage-content.en.json",
    "zh-CN": "site/homepage-content.zh-CN.json",
  });
  assert.equal(homepageContentByLocale.en.locale, "en");
  assert.equal(homepageContentByLocale["zh-CN"].locale, "zh-CN");
  assert.equal(homepageContentByLocale.en.resources.length, 6);
  assert.deepEqual(
    homepageContentByLocale.en.resources.map(({ id }) => id),
    ["get-started", "daily-work", "control", "extend", "understand", "contribute"],
  );
  for (let index = 0; index < 6; index += 1) {
    const english = homepageContentByLocale.en.resources[index];
    const chinese = homepageContentByLocale["zh-CN"].resources[index];
    assert.equal(chinese.id, english.id);
    assert.equal(chinese.eyebrow, english.eyebrow);
    assert.equal(chinese.href, english.href);
    assert.equal(english.href, HOMEPAGE_RESOURCE_ROUTES[english.id]);
  }
  assert.equal(
    homepageContentByLocale.en.resources[0].href,
    "./getting-started/quickstart/",
  );
  assert(Object.isFrozen(homepageContentByLocale.en));
  assert(Object.isFrozen(homepageContentByLocale.en.resources));
});

test("rejects missing, extra, and nested target fields", () => {
  const missing = sourceCopies();
  delete missing.english.title;
  assert.throws(
    () => compileHomepageContentSources(missing),
    /English homepage fields must be exactly/u,
  );

  const extra = sourceCopies();
  extra.chinese.unreviewed = "未审查内容";
  assert.throws(
    () => compileHomepageContentSources(extra),
    /Simplified Chinese homepage fields must be exactly/u,
  );

  const localizedTarget = sourceCopies();
  localizedTarget.chinese.resources[0].href = "./getting-started/";
  assert.throws(
    () => compileHomepageContentSources(localizedTarget),
    /resources\[0\] fields must be exactly/u,
  );
});

test("rejects array, stable ID, and shared presentation drift", () => {
  const missingCard = sourceCopies();
  missingCard.chinese.resources.pop();
  assert.throws(
    () => compileHomepageContentSources(missingCard),
    /resources must contain exactly 6 items/u,
  );

  const changedId = sourceCopies();
  changedId.chinese.resources[0].id = "quickstart";
  assert.throws(
    () => compileHomepageContentSources(changedId),
    /resources\[0\]\.id must be get-started/u,
  );

  const changedEyebrow = sourceCopies();
  changedEyebrow.chinese.resources[0].eyebrow = "QUICKSTART";
  assert.throws(
    () => compileHomepageContentSources(changedEyebrow),
    /resources\[0\]\.eyebrow must match across locales/u,
  );
});

test("rejects missing, extra, or changed shared resource targets", () => {
  const sources = sourceCopies();
  assert.throws(
    () =>
      compileHomepageContentSources({
        ...sources,
        resourceRoutes: {
          ...HOMEPAGE_RESOURCE_ROUTES,
          "get-started": "./getting-started/",
        },
      }),
    /resource route get-started must be \.\/getting-started\/quickstart\//u,
  );

  const missing = { ...HOMEPAGE_RESOURCE_ROUTES };
  delete missing.contribute;
  assert.throws(
    () => compileHomepageContentSources({ ...sourceCopies(), resourceRoutes: missing }),
    /resource routes fields must be exactly/u,
  );

  assert.throws(
    () =>
      compileHomepageContentSources({
        ...sourceCopies(),
        resourceRoutes: { ...HOMEPAGE_RESOURCE_ROUTES, legacy: "./legacy/" },
      }),
    /resource routes fields must be exactly/u,
  );
});

test("rejects English, Simplified Chinese, and fallback-language drift", () => {
  const englishDrift = sourceCopies();
  englishDrift.english.title = "经过验证的修改";
  assert.throws(
    () => compileHomepageContentSources(englishDrift),
    /English homepage\.title must not contain Simplified Chinese/u,
  );

  const chineseFallback = sourceCopies();
  chineseFallback.chinese.resources[0].description =
    chineseFallback.english.resources[0].description;
  assert.throws(
    () => compileHomepageContentSources(chineseFallback),
    /must contain Simplified Chinese; English fallback is forbidden/u,
  );

  const fallbackMarker = sourceCopies();
  fallbackMarker.chinese.lead += " English fallback";
  assert.throws(
    () => compileHomepageContentSources(fallbackMarker),
    /forbidden untranslated fallback marker/u,
  );

  const mostlyEnglish = sourceCopies();
  mostlyEnglish.chinese.title =
    "中 This homepage is still an English fallback with one token added.";
  assert.throws(
    () => compileHomepageContentSources(mostlyEnglish),
    /title has too little Simplified Chinese prose/u,
  );
});

test("uses a stable typed contract error for invalid source locale", () => {
  const changed = sourceCopies();
  changed.chinese.locale = "zh-cn";
  assert.throws(
    () => compileHomepageContentSources(changed),
    (error) =>
      error instanceof HomepageContentContractError &&
      /locale must be zh-CN/u.test(error.message),
  );
});
