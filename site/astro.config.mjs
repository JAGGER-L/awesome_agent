import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import starlight from "@astrojs/starlight";
import { docsSidebar } from "./docs-navigation.mjs";

const siteOrigin = process.env.SITE_URL ?? "https://jagger-l.github.io";
const basePath =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH || "/";
const socialImage = new URL(
  `${basePath === "/" ? "" : basePath.replace(/\/$/, "")}/og-v2.png`,
  siteOrigin,
).href;
const basePrefix = basePath === "/" ? "/" : `${basePath.replace(/\/$/, "")}/`;

function routeWithinBase(page) {
  const pathname = new URL(page).pathname;
  if (!pathname.startsWith(basePrefix)) return null;
  return pathname.slice(basePrefix.length).replace(/\/$/, "");
}

export default defineConfig({
  site: siteOrigin,
  base: basePath,
  trailingSlash: "always",
  integrations: [
    sitemap({
      filter(page) {
        const route = routeWithinBase(page);
        return route !== null && route !== "404";
      },
    }),
    starlight({
      title: {
        en: "Awesome Docs",
        "zh-CN": "Awesome 文档",
      },
      description:
        "Documentation for Awesome, the AI coding assistant that works in your terminal.",
      favicon: "/favicon.svg",
      disable404Route: true,
      head: [
        { tag: "meta", attrs: { property: "og:image", content: socialImage } },
        { tag: "meta", attrs: { property: "og:image:width", content: "1731" } },
        { tag: "meta", attrs: { property: "og:image:height", content: "909" } },
        { tag: "meta", attrs: { name: "twitter:card", content: "summary_large_image" } },
        { tag: "meta", attrs: { name: "twitter:image", content: socialImage } },
      ],
      locales: {
        root: { label: "English", lang: "en" },
        "zh-cn": { label: "简体中文", lang: "zh-CN" },
      },
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/JAGGER-L/awesome_agent",
        },
      ],
      customCss: ["./src/styles/signal.css"],
      components: {
        Head: "./src/components/Head.astro",
        Hero: "./src/components/Hero.astro",
        LanguageSelect: "./src/components/LanguageSelect.astro",
        SiteTitle: "./src/components/SiteTitle.astro",
        ThemeSelect: "./src/components/ThemeSelect.astro",
      },
      lastUpdated: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      sidebar: docsSidebar,
    }),
  ],
});
