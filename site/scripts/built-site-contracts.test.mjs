import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import { docsSidebar, sidebarRoutes } from "../docs-navigation.mjs";
import {
  BuiltSiteContractError,
  buildExpectedSiteContract,
  exactCollectionFailures,
  extractMarkdownLinkTargets,
  extractXmlLocations,
  normalizeBasePath,
  resolveBuiltOutputPath,
} from "./built-site-contracts.mjs";

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "awesome-built-site-contract-"));
  const outputDirectory = join(root, "dist");
  await mkdir(join(outputDirectory, "docs"), { recursive: true });
  await mkdir(join(outputDirectory, "_astro"), { recursive: true });
  await writeFile(join(outputDirectory, "index.html"), "home", "utf8");
  await writeFile(join(outputDirectory, "docs", "index.html"), "docs", "utf8");
  await writeFile(join(outputDirectory, "asset.js"), "asset", "utf8");
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, outputDirectory };
}

test("normalizes only explicit safe deployment bases", () => {
  assert.equal(normalizeBasePath(undefined), "/awesome_agent");
  assert.equal(normalizeBasePath(""), "");
  assert.equal(normalizeBasePath("/"), "");
  assert.equal(normalizeBasePath("/awesome_agent/"), "/awesome_agent");
  for (const invalid of [
    "awesome_agent",
    "/a//b",
    "/a/../b",
    "/a\\b",
    "/a:b",
    "/a%2fb",
    "/a. ",
    "/文档",
  ]) {
    assert.throws(() => normalizeBasePath(invalid), BuiltSiteContractError);
  }
});

test("resolves only ordinary files inside the configured base and output root", async (t) => {
  const { outputDirectory } = await fixture(t);
  const options = { basePath: "/awesome_agent", outputDirectory };

  assert.equal(
    await resolveBuiltOutputPath({ pathname: "/awesome_agent/", ...options }),
    resolve(outputDirectory, "index.html"),
  );
  assert.equal(
    await resolveBuiltOutputPath({ pathname: "/awesome_agent/docs/", ...options }),
    resolve(outputDirectory, "docs", "index.html"),
  );
  assert.equal(
    await resolveBuiltOutputPath({ pathname: "/awesome_agent/docs", ...options }),
    resolve(outputDirectory, "docs", "index.html"),
  );
  assert.equal(
    await resolveBuiltOutputPath({ pathname: "/awesome_agent/asset.js", ...options }),
    resolve(outputDirectory, "asset.js"),
  );
  assert.equal(
    await resolveBuiltOutputPath({
      pathname: "/docs/",
      basePath: "/",
      outputDirectory,
    }),
    resolve(outputDirectory, "docs", "index.html"),
  );
});

test(
  "rejects malformed, encoded-separator, dot-segment, base-escape, and directory URLs",
  async (t) => {
    const { outputDirectory } = await fixture(t);
    const options = { basePath: "/awesome_agent", outputDirectory };
    const rejected = [
      "/awesome_agent/%ZZ",
      "/awesome_agent/%2fetc",
      "/awesome_agent/%5cetc",
      "/awesome_agent/%252fetc",
      "/awesome_agent/%00",
      "/awesome_agent/file. ",
      "/awesome_agent/../asset.js",
      "/awesome_agent/%2e%2e/asset.js",
      "/awesome_agent/%2e%2e%2fasset.js",
      "/other/asset.js",
      "/awesome_agent/_astro",
      "/awesome_agent/missing.html",
    ];
    for (const pathname of rejected) {
      await assert.rejects(
        resolveBuiltOutputPath({ pathname, ...options }),
        BuiltSiteContractError,
        pathname,
      );
    }
  },
);

test("rejects a target whose real identity escapes through a link or reparse point", async () => {
  const outputDirectory = resolve("virtual", "dist");
  const externalTarget = resolve("virtual", "outside", "sentinel.html");
  const fileSystem = {
    async lstat() {
      return {
        isDirectory: () => false,
        isFile: () => true,
      };
    },
    async realpath(path) {
      return path === resolve(outputDirectory) ? resolve(outputDirectory) : externalTarget;
    },
  };

  await assert.rejects(
    resolveBuiltOutputPath({
      pathname: "/awesome_agent/page.html",
      basePath: "/awesome_agent",
      outputDirectory,
      fileSystem,
    }),
    /identity escapes the built output directory/u,
  );
});

test("derives the exact bilingual HTML and public URL contract from canonical routes", () => {
  const contract = buildExpectedSiteContract(["start", "guide/install"], {
    basePath: "/awesome_agent",
    origin: "https://example.test",
  });
  assert.deepEqual(contract.canonicalRoutes, [
    "",
    "start",
    "guide/install",
    "zh-cn",
    "zh-cn/start",
    "zh-cn/guide/install",
  ]);
  assert.deepEqual(contract.htmlPaths, [
    "index.html",
    "start/index.html",
    "guide/install/index.html",
    "zh-cn/index.html",
    "zh-cn/start/index.html",
    "zh-cn/guide/install/index.html",
    "404.html",
  ]);
  assert.equal(contract.canonicalUrls.length, 6);
  const rootContract = buildExpectedSiteContract(["start"], {
    basePath: "/",
    origin: "https://example.test",
  });
  assert.deepEqual(rootContract.canonicalUrls, [
    "https://example.test/",
    "https://example.test/start/",
    "https://example.test/zh-cn/",
    "https://example.test/zh-cn/start/",
  ]);
  assert.throws(
    () =>
      buildExpectedSiteContract(["start", "start"], {
        basePath: "/awesome_agent",
        origin: "https://example.test",
      }),
    /Duplicate canonical route/u,
  );
  assert.throws(
    () =>
      buildExpectedSiteContract(["zh-cn"], {
        basePath: "/awesome_agent",
        origin: "https://example.test",
      }),
    /reserved site path/u,
  );
  for (const route of ["%2E", "%2e%2e", "Guide", "guide_file", "中文"]) {
    assert.throws(
      () =>
        buildExpectedSiteContract([route], {
          basePath: "/awesome_agent",
          origin: "https://example.test",
        }),
      BuiltSiteContractError,
    );
  }
});

test("the current sidebar compiles to 86 canonical URLs and 87 exact HTML files", () => {
  const routes = sidebarRoutes(docsSidebar);
  const contract = buildExpectedSiteContract(routes, {
    basePath: "/awesome_agent",
    origin: "https://example.test",
  });
  assert.equal(routes.length, 42);
  assert.equal(new Set(routes).size, 42);
  assert.equal(contract.canonicalUrls.length, 86);
  assert.equal(new Set(contract.canonicalUrls).size, 86);
  assert.equal(contract.htmlPaths.length, 87);
  assert.equal(new Set(contract.htmlPaths).size, 87);
});

test("exact collection comparison rejects missing, extra, and duplicate values", () => {
  const expected = ["a", "b"];
  assert.deepEqual(exactCollectionFailures(["a", "b"], expected, "pages"), []);
  assert.deepEqual(exactCollectionFailures(["a"], expected, "pages"), [
    "pages: missing b",
  ]);
  assert.deepEqual(exactCollectionFailures(["a", "b", "c"], expected, "pages"), [
    "pages: unexpected c",
  ]);
  assert.deepEqual(exactCollectionFailures(["a", "a", "b"], expected, "pages"), [
    "pages: duplicate a (2 copies)",
  ]);
});

test("extracts sitemap and llms link targets without hiding malformed llms entries", () => {
  assert.deepEqual(
    extractXmlLocations(
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
        "<!-- <url><loc>https://wrong.example/</loc></url> -->" +
        "<url><loc>https://example.test/a/</loc></url>" +
        "<url><loc>https://example.test/b/?x=1&amp;y=2</loc></url></urlset>",
    ),
    ["https://example.test/a/", "https://example.test/b/?x=1&y=2"],
  );
  assert.deepEqual(
    extractMarkdownLinkTargets(
      "# [Docs](https://example.test/docs/)\n\n" +
        "- [Home](https://example.test/)\n" +
        "<!-- [Hidden](https://wrong.example/) -->\n" +
        "[Reference][home]\n\n[home]: https://example.test/reference/\n",
    ),
    {
      targets: ["https://example.test/docs/", "https://example.test/"],
      invalidLines: ["line 7: reference definitions are not allowed"],
    },
  );
});
