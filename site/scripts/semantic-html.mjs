import { fromHtml } from "hast-util-from-html";
import { visit } from "unist-util-visit";

const NON_VISIBLE_ELEMENTS = new Set(["script", "style", "template", "svg"]);

function propertyText(value) {
  if (value === undefined || value === null) return null;
  if (Array.isArray(value)) return value.map(String).join(" ");
  return String(value);
}

function relationTokens(value) {
  return (Array.isArray(value) ? value : String(value ?? "").split(/\s+/u))
    .map((item) => String(item).toLowerCase())
    .filter(Boolean);
}

function visibleText(node) {
  if (node.type === "text") return node.value;
  if (node.type !== "root" && node.type !== "element") return "";
  if (node.type === "element" && NON_VISIBLE_ELEMENTS.has(node.tagName)) return "";
  return (node.children ?? []).map(visibleText).join(" ");
}

function normalizedVisibleText(node) {
  return visibleText(node).replace(/\s+/gu, " ").trim();
}

export function analyzeHtmlDocument(html) {
  const tree = fromHtml(String(html));
  const analysis = {
    alternates: [],
    canonicalLinks: [],
    descriptions: [],
    documentText: normalizedVisibleText(tree),
    htmlLanguages: [],
    ids: new Set(),
    localReferences: [],
    mainTexts: [],
    refreshMetas: 0,
    robots: [],
    timeDatetimes: [],
  };

  visit(tree, "element", (node) => {
    const properties = node.properties ?? {};
    const id = propertyText(properties.id);
    if (id !== null) analysis.ids.add(id);

    for (const property of ["href", "src"]) {
      const value = propertyText(properties[property]);
      if (value !== null) analysis.localReferences.push(value);
    }

    if (node.tagName === "html") {
      const language = propertyText(properties.lang);
      if (language !== null) analysis.htmlLanguages.push(language);
    } else if (node.tagName === "main") {
      analysis.mainTexts.push(normalizedVisibleText(node));
    } else if (node.tagName === "time") {
      const datetime = propertyText(properties.dateTime);
      if (datetime !== null) analysis.timeDatetimes.push(datetime);
    } else if (node.tagName === "link") {
      const relations = relationTokens(properties.rel);
      const href = propertyText(properties.href);
      if (relations.includes("canonical")) analysis.canonicalLinks.push(href);
      if (relations.includes("alternate")) {
        analysis.alternates.push({
          href,
          language: propertyText(properties.hrefLang),
        });
      }
    } else if (node.tagName === "meta") {
      const name = propertyText(properties.name)?.toLowerCase();
      const content = propertyText(properties.content);
      if (name === "description") analysis.descriptions.push(content);
      if (name === "robots") analysis.robots.push(content);
      if (propertyText(properties.httpEquiv)?.toLowerCase() === "refresh") {
        analysis.refreshMetas += 1;
      }
    }
  });

  return analysis;
}
