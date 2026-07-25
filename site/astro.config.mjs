import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

const siteOrigin = process.env.SITE_URL ?? "https://jagger-l.github.io";
const basePath =
  process.env.BASE_PATH === undefined ? "/awesome_agent" : process.env.BASE_PATH || "/";
const socialImage = new URL(
  `${basePath === "/" ? "" : basePath.replace(/\/$/, "")}/og-v2.png`,
  siteOrigin,
).href;

const group = (label, translation, items, collapsed = false) => ({
  label,
  translations: { "zh-CN": translation },
  items,
  collapsed,
});

export default defineConfig({
  site: siteOrigin,
  base: basePath,
  trailingSlash: "always",
  integrations: [
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
        Hero: "./src/components/Hero.astro",
        LanguageSelect: "./src/components/LanguageSelect.astro",
        SiteTitle: "./src/components/SiteTitle.astro",
        ThemeSelect: "./src/components/ThemeSelect.astro",
      },
      lastUpdated: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      sidebar: [
        group("Get started", "快速开始", ["getting-started/quickstart"]),
        group("Use Awesome", "使用 Awesome", [
          "user-guide/commands",
          "user-guide/configuration",
          "user-guide/workspace-and-tools",
          "user-guide/memory-skills-mcp",
          "user-guide/troubleshooting",
        ]),
        group(
          "Understand Awesome",
          "理解 Awesome",
          [
            "architecture",
            "architecture/overview",
            "architecture/agent-core",
            "architecture/application-and-langgraph",
            "architecture/protocol-and-ink",
            "architecture/storage",
            "architecture/security",
          ],
          true,
        ),
        group(
          "Contribute",
          "参与贡献",
          [
            "development",
            "development/testing",
            "development/command-regression",
            "development/release",
          ],
          true,
        ),
        { slug: "roadmap" },
      ],
    }),
  ],
});
