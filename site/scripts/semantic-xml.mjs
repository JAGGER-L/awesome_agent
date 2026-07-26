import { SaxesParser } from "saxes";

const SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9";

export class SemanticXmlError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "SemanticXmlError";
  }
}

export function parseSitemapXml(xml) {
  const stack = [];
  const locations = [];
  let root = null;
  let locationText = null;
  const parser = new SaxesParser({ xmlns: true });

  parser.on("doctype", () => {
    throw new SemanticXmlError("Sitemap XML must not contain a doctype.");
  });
  parser.on("opentag", (tag) => {
    if (tag.uri !== SITEMAP_NAMESPACE) {
      throw new SemanticXmlError(`Unexpected sitemap namespace on <${tag.name}>.`);
    }
    if (stack.length === 0) {
      if (root !== null || (tag.local !== "sitemapindex" && tag.local !== "urlset")) {
        throw new SemanticXmlError(`Unexpected sitemap root element <${tag.name}>.`);
      }
      root = tag.local;
    }
    if (locationText !== null) {
      throw new SemanticXmlError("Sitemap <loc> values must contain text only.");
    }
    const parent = stack.at(-1)?.local ?? null;
    if (tag.local === "loc") {
      const expectedParent = root === "sitemapindex" ? "sitemap" : "url";
      if (parent !== expectedParent) {
        throw new SemanticXmlError(`Sitemap <loc> must be inside <${expectedParent}>.`);
      }
      locationText = "";
    }
    stack.push(tag);
  });
  const appendText = (value) => {
    if (locationText !== null) locationText += value;
  };
  parser.on("text", appendText);
  parser.on("cdata", appendText);
  parser.on("closetag", (tag) => {
    const current = stack.pop();
    if (current?.name !== tag.name) {
      throw new SemanticXmlError(`Unexpected sitemap closing element </${tag.name}>.`);
    }
    if (tag.local === "loc") {
      const value = locationText?.trim() ?? "";
      if (!value) throw new SemanticXmlError("Sitemap <loc> must not be empty.");
      locations.push(value);
      locationText = null;
    }
  });

  try {
    parser.write(String(xml)).close();
  } catch (error) {
    if (error instanceof SemanticXmlError) throw error;
    throw new SemanticXmlError(`Sitemap XML is not well formed: ${error.message}`, {
      cause: error,
    });
  }
  if (root === null || stack.length !== 0 || locationText !== null) {
    throw new SemanticXmlError("Sitemap XML is incomplete.");
  }
  return { root, locations };
}
