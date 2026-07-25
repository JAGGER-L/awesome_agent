const group = (label, translation, items, collapsed = true) => ({
  label,
  translations: { "zh-CN": translation },
  items,
  collapsed,
});

export const docsSidebar = [
  group(
    "Start here",
    "从这里开始",
    ["getting-started", "getting-started/installation", "getting-started/quickstart"],
    false,
  ),
  group("Core concepts", "核心概念", [
    "concepts",
    "concepts/workspace-thread-turn",
    "concepts/context-and-instructions",
    "concepts/changes-and-recovery",
  ]),
  group("Use Awesome", "使用 Awesome", [
    "user-guide",
    "user-guide/commands",
    "user-guide/permissions",
    "user-guide/tools-and-shell",
    "user-guide/changes",
    "user-guide/configuration",
    "user-guide/troubleshooting",
  ]),
  group("Extend Awesome", "扩展 Awesome", [
    "extensions",
    "extensions/memory",
    "extensions/skills",
    "extensions/mcp",
  ]),
  group(
    "Reference",
    "参考手册",
    [
      "reference",
      "reference/cli",
      "reference/commands",
      "reference/configuration",
      "reference/built-in-tools",
      "reference/permission-modes",
      "reference/files-and-state",
      "reference/protocol",
    ],
    true,
  ),
  group(
    "Architecture",
    "系统架构",
    [
      "architecture",
      "architecture/overview",
      "architecture/request-lifecycles",
      "architecture/application-and-agent",
      "architecture/context-model-and-extensions",
      "architecture/tools-and-changes",
      "architecture/storage-and-recovery",
      "architecture/protocol-and-tui",
      "architecture/security-and-dependencies",
    ],
    true,
  ),
  group(
    "Contribute",
    "参与贡献",
    [
      "development",
      "development/setup",
      "development/testing",
      "development/extending-awesome",
      "development/contracts-and-documentation",
      "development/release",
    ],
    true,
  ),
  group("Project", "项目", ["roadmap"], true),
];

// Only these routes have independently maintained English and Chinese content.
// Every other zh-cn route is Starlight's English-content fallback.
export const translatedRoutes = new Set(["", "getting-started/quickstart"]);

const movedRoutes = [
  ["user-guide/workspace-and-tools", "user-guide/tools-and-shell"],
  ["user-guide/memory-skills-mcp", "extensions"],
  ["architecture/agent-core", "architecture/application-and-agent"],
  ["architecture/application-and-langgraph", "architecture/application-and-agent"],
  ["architecture/protocol-and-ink", "architecture/protocol-and-tui"],
  ["architecture/storage", "architecture/storage-and-recovery"],
  ["architecture/security", "architecture/security-and-dependencies"],
  ["development/command-regression", "development/testing"],
];

export const docsRedirects = Object.fromEntries(
  movedRoutes.flatMap(([source, destination]) => [
    [`/${source}`, `/${destination}/`],
    [`/zh-cn/${source}`, `/zh-cn/${destination}/`],
  ]),
);

export function redirectsForBase(basePath, redirects = docsRedirects) {
  const normalizedBase = String(basePath ?? "").replace(/^\/+|\/+$/g, "");
  const prefix = normalizedBase ? `/${normalizedBase}` : "";
  return Object.fromEntries(
    Object.entries(redirects).map(([source, destination]) => [
      source,
      `${prefix}${destination}`,
    ]),
  );
}

export function sidebarRoutes(sidebar = docsSidebar) {
  const routes = [];
  const visit = (item) => {
    if (typeof item === "string") {
      routes.push(item);
      return;
    }
    if (typeof item?.slug === "string") routes.push(item.slug);
    if (Array.isArray(item?.items)) item.items.forEach(visit);
  };
  sidebar.forEach(visit);
  return routes;
}
