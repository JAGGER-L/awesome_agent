import assert from "node:assert/strict";
import test from "node:test";

import { analyzeHtmlDocument } from "./semantic-html.mjs";

test("analyzes real HTML nodes and ignores markup hidden in comments or scripts", () => {
  const analysis = analyzeHtmlDocument(`<!doctype html>
<html lang="zh-CN"><head>
  <!-- <link rel="canonical" href="https://wrong.example/"> -->
  <script>const fake = '<meta name="robots" content="noindex">';</script>
  <link rel="canonical" href="https://example.test/zh-cn/">
  <link rel="alternate" hreflang="en" href="https://example.test/">
  <meta name="description" content="中文说明">
  <meta name="robots" content="index,follow">
</head><body>
  <main id="content"><h1>中文首页</h1><p>完整的中文正文。</p></main>
  <a href="/zh-cn/start/">开始</a>
  <time datetime="2026-07-26">最近更新</time>
</body></html>`);

  assert.deepEqual(analysis.htmlLanguages, ["zh-CN"]);
  assert.deepEqual(analysis.canonicalLinks, ["https://example.test/zh-cn/"]);
  assert.deepEqual(analysis.alternates, [
    { href: "https://example.test/", language: "en" },
  ]);
  assert.deepEqual(analysis.descriptions, ["中文说明"]);
  assert.deepEqual(analysis.robots, ["index,follow"]);
  assert.deepEqual(analysis.mainTexts, ["中文首页 完整的中文正文。"]);
  assert.deepEqual(analysis.timeDatetimes, ["2026-07-26"]);
  assert.deepEqual(analysis.localReferences, [
    "https://example.test/zh-cn/",
    "https://example.test/",
    "/zh-cn/start/",
  ]);
  assert.equal(analysis.refreshMetas, 0);
  assert(analysis.ids.has("content"));
  assert(!analysis.documentText.includes("noindex"));
});

test("reports only actual refresh metadata", () => {
  assert.equal(
    analyzeHtmlDocument(
      '<!-- <meta http-equiv="refresh"> --><meta http-equiv="refresh" content="0">',
    ).refreshMetas,
    1,
  );
});
