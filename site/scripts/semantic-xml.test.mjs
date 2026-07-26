import assert from "node:assert/strict";
import test from "node:test";

import { parseSitemapXml, SemanticXmlError } from "./semantic-xml.mjs";

const namespace = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"';

test("parses semantic sitemap locations and ignores commented markup", () => {
  const parsed = parseSitemapXml(
    `<urlset ${namespace}>
      <!-- <url><loc>https://wrong.example/</loc></url> -->
      <url><loc>https://example.test/a/?x=1&amp;y=2</loc></url>
      <url><loc><![CDATA[https://example.test/b/]]></loc></url>
    </urlset>`,
  );
  assert.deepEqual(parsed, {
    root: "urlset",
    locations: [
      "https://example.test/a/?x=1&y=2",
      "https://example.test/b/",
    ],
  });
});

test("rejects malformed, namespaceless, misplaced, and doctype XML", () => {
  for (const xml of [
    "<urlset><url><loc>https://example.test/</loc></url></urlset>",
    `<urlset ${namespace}><loc>https://example.test/</loc></urlset>`,
    `<urlset ${namespace}><url><loc><b>bad</b></loc></url></urlset>`,
    `<!DOCTYPE urlset><urlset ${namespace}></urlset>`,
    `<urlset ${namespace}><url></urlset>`,
  ]) {
    assert.throws(() => parseSitemapXml(xml), SemanticXmlError);
  }
});
