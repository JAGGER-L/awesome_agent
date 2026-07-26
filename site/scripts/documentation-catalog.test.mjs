import assert from "node:assert/strict";
import test from "node:test";

import {
  compareCodePoints,
  compileDocumentationCatalog,
  compileDocumentationCatalogFromSources,
  createTranslationLock,
  normalizedSourceHash,
  normalizeSourceText,
  translationContractFailures,
  validateTranslationLock,
} from "../documentation-catalog.mjs";

const ENGLISH_GUIDE = `# Guide

This guide explains safe workflows, exact recovery, and the public \`operation_busy\`
contract. Read [the example](https://example.com/guide) before changing state.

\`\`\`python
print("catalog")
\`\`\`
`;

const CHINESE_GUIDE = `# 指南

本指南说明安全工作流、精确恢复和公共 \`operation_busy\` 契约。更改状态前，请先阅读
[示例](https://example.com/guide)，并确认当前操作边界。

\`\`\`python
print("catalog")
\`\`\`
`;

function baseSources() {
  return [
    {
      source: "README.md",
      content:
        "# Awesome\n\nRead the [documentation](https://jagger-l.github.io/awesome_agent/) and the [guide](docs/guide.md).\n",
    },
    {
      source: "README.zh-CN.md",
      content:
        "# Awesome\n\n阅读完整的[中文文档](https://jagger-l.github.io/awesome_agent/zh-cn/)和[指南](docs/guide.zh-CN.md)，了解所有工作流。\n",
    },
    {
      source: "site/content/index.mdx",
      content:
        "---\ntitle: Awesome\ndescription: Terminal documentation home.\n---\n",
    },
    {
      source: "site/content/index.zh-cn.mdx",
      content:
        "---\ntitle: Awesome\ndescription: 终端文档首页与完整使用说明。\n---\n",
    },
    {
      source: "docs/README.md",
      content:
        "# Documentation sources\n\nRepository-only documentation ownership index.\n",
    },
    {
      source: "docs/README.zh-CN.md",
      content:
        "# 文档源文件\n\n这是仅供仓库阅读的文档所有权索引，说明完整维护边界。\n",
    },
    { source: "docs/guide.md", content: ENGLISH_GUIDE },
    { source: "docs/guide.zh-CN.md", content: CHINESE_GUIDE },
    {
      source: "ARCHITECTURE.md",
      content:
        "# Architecture\n\nThis architecture defines dependency direction and ownership.\n",
    },
    {
      source: "ARCHITECTURE.zh-CN.md",
      content:
        "# 架构\n\n本架构定义依赖方向、组件所有权以及必须保持的系统边界。\n",
    },
  ];
}

function compile(
  sources = baseSources(),
  navigationRoutes = ["guide", "architecture/overview"],
  allowedRepositoryMarkdownTargets = [],
) {
  return compileDocumentationCatalogFromSources({
    sources,
    navigationRoutes,
    allowedRepositoryMarkdownTargets,
  });
}

test("normalizes BOM and Windows newlines before hashing", () => {
  assert.equal(
    normalizeSourceText("\uFEFFone\r\ntwo\rthree\n"),
    "one\ntwo\nthree\n",
  );
  assert.equal(
    normalizedSourceHash("\uFEFFone\r\ntwo\rthree\n"),
    normalizedSourceHash("one\ntwo\nthree\n"),
  );
});

test("sorts identities by fixed Unicode code-point order", () => {
  assert.deepEqual(["\u{10000}", "\uE000", "a"].sort(compareCodePoints), [
    "a",
    "\uE000",
    "\u{10000}",
  ]);
});

test("rejects non-portable identities before path or URL normalization", () => {
  for (const invalidSource of [
    "docs/%2e%2e/guide.md",
    "docs/section/../guide.md",
    "docs/guide\\copy.md",
    "docs/guide\u0000copy.md",
    "docs/指南.md",
  ]) {
    const sources = baseSources().map((entry) =>
      entry.source === "docs/guide.md"
        ? { ...entry, source: invalidSource }
        : entry,
    );
    assert.throws(
      () => compile(sources),
      /Documentation source path (?:contains|is not)/u,
      invalidSource,
    );
  }

  assert.throws(
    () => compile(baseSources(), ["%67uide", "architecture/overview"]),
    /Sidebar route contains percent encoding/u,
  );

  const uppercaseRouteSources = baseSources().map((entry) => {
    if (entry.source === "docs/guide.md") {
      return { ...entry, source: "docs/Guide.md" };
    }
    if (entry.source === "docs/guide.zh-CN.md") {
      return { ...entry, source: "docs/Guide.zh-CN.md" };
    }
    return entry;
  });
  assert.throws(
    () => compile(uppercaseRouteSources, ["Guide", "architecture/overview"]),
    /is not a portable ASCII slug path/u,
  );

  assert.throws(
    () =>
      compile([
        ...baseSources(),
        { source: "docs/GUIDE.md", content: ENGLISH_GUIDE },
      ]),
    /source portability collision "docs\/guide\.md" and "docs\/GUIDE\.md"/u,
  );
});

test("rejects a missing Simplified Chinese page", () => {
  const sources = baseSources().filter(
    (entry) => entry.source !== "docs/guide.zh-CN.md",
  );
  assert.throws(() => compile(sources), /has no Simplified Chinese source/u);
});

test("rejects an orphan Simplified Chinese page", () => {
  const sources = baseSources().filter(
    (entry) => entry.source !== "docs/guide.md",
  );
  assert.throws(() => compile(sources), /orphan Simplified Chinese source/u);
});

test("rejects duplicate source identities", () => {
  const sources = baseSources();
  sources.push({
    ...sources.find((entry) => entry.source === "docs/guide.md"),
  });
  assert.throws(() => compile(sources), /source collision "docs\/guide\.md"/u);
});

test("rejects canonical route collisions", () => {
  const sources = baseSources();
  sources.push(
    { source: "docs/guide/README.md", content: ENGLISH_GUIDE },
    { source: "docs/guide/README.zh-CN.md", content: CHINESE_GUIDE },
  );
  assert.throws(() => compile(sources), /route collision "guide"/u);
});

test("rejects output collisions independently of route collisions", () => {
  const sources = baseSources();
  sources.push(
    { source: "docs/guide/README.md", content: ENGLISH_GUIDE },
    { source: "docs/guide/README.zh-CN.md", content: CHINESE_GUIDE },
    { source: "docs/guide/index.md", content: ENGLISH_GUIDE },
    { source: "docs/guide/index.zh-CN.md", content: CHINESE_GUIDE },
  );
  assert.throws(
    () => compile(sources, ["guide", "guide/index", "architecture/overview"]),
    /output collision "guide\/index\.md"/u,
  );
});

test("rejects a stale English or Simplified Chinese hash", () => {
  const catalog = compile();
  const lock = createTranslationLock(catalog);
  const changedSources = baseSources().map((entry) =>
    entry.source === "docs/guide.md"
      ? { ...entry, content: `${entry.content}\nOne additional sentence.\n` }
      : entry,
  );
  const changedCatalog = compile(changedSources);
  assert.throws(
    () => validateTranslationLock(changedCatalog, lock),
    /docs\/guide\.md English hash is stale/u,
  );

  const changedChineseSources = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? { ...entry, content: `${entry.content}\n补充一句说明。\n` }
      : entry,
  );
  const changedChineseCatalog = compile(changedChineseSources);
  assert.throws(
    () => validateTranslationLock(changedChineseCatalog, lock),
    /docs\/guide\.zh-CN\.md Simplified Chinese hash is stale/u,
  );
});

test("rejects missing and other-pair cross-locale Markdown links outside code", () => {
  const missing = baseSources().map((entry) =>
    entry.source === "docs/guide.md"
      ? { ...entry, content: `${entry.content}\n[Missing](missing.md)\n` }
      : entry,
  );
  assert.throws(
    () => compile(missing),
    /local Markdown target is not in the catalog or allowlist: docs\/missing\.md/u,
  );

  const crossLocale = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? {
          ...entry,
          content: `${entry.content}\n[English](../ARCHITECTURE.md)\n`,
        }
      : entry,
  );
  assert.throws(
    () => compile(crossLocale),
    /local Markdown link crosses locale \(zh-CN -> en\): ARCHITECTURE\.md/u,
  );
});

test("allows a direct language switch only to the same catalog pair", () => {
  const sources = baseSources().map((entry) => {
    if (entry.source === "docs/guide.md") {
      return {
        ...entry,
        content: `${entry.content}\n[简体中文](guide.zh-CN.md)\n`,
      };
    }
    if (entry.source === "docs/guide.zh-CN.md") {
      return { ...entry, content: `${entry.content}\n[English](guide.md)\n` };
    }
    return entry;
  });
  assert.doesNotThrow(() => compile(sources));
});

test("preserves local Markdown target pairs and counts while allowing localized anchors", () => {
  const targetSources = [
    {
      source: "docs/b.md",
      content:
        "# B\n\nComplete guidance for the B workflow and its recovery boundary.\n",
    },
    {
      source: "docs/b.zh-CN.md",
      content: "# 乙\n\n完整说明乙工作流、恢复方式、设计边界和操作契约。\n",
    },
    {
      source: "docs/c.md",
      content:
        "# C\n\nComplete guidance for the C workflow and its recovery boundary.\n",
    },
    {
      source: "docs/c.zh-CN.md",
      content: "# 丙\n\n完整说明丙工作流、恢复方式、设计边界和操作契约。\n",
    },
  ];
  const navigation = ["guide", "b", "c", "architecture/overview"];
  const linkedSources = (englishLinks, chineseLinks) => [
    ...baseSources().map((entry) => {
      if (entry.source === "docs/guide.md") {
        return { ...entry, content: `${entry.content}\n${englishLinks}\n` };
      }
      if (entry.source === "docs/guide.zh-CN.md") {
        return { ...entry, content: `${entry.content}\n${chineseLinks}\n` };
      }
      return entry;
    }),
    ...targetSources,
  ];

  assert.doesNotThrow(() =>
    compile(
      linkedSources("[B](b.md#setup)", "[乙](b.zh-CN.md#安装)"),
      navigation,
    ),
  );
  assert.throws(
    () => compile(linkedSources("[B](b.md)", "[丙](c.zh-CN.md)"), navigation),
    /does not preserve local Markdown target pairs with duplicate counts/u,
  );
  assert.throws(
    () =>
      compile(
        linkedSources("[B](b.md) and [B again](b.md)", "[乙](b.zh-CN.md)"),
        navigation,
      ),
    /does not preserve local Markdown target pairs with duplicate counts/u,
  );
});

test("rejects route-shaped local Markdown links but ignores fragments and images", () => {
  const bypass = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? {
          ...entry,
          content: `${entry.content}\n[English route](../../english-route/)\n`,
        }
      : entry,
  );
  assert.throws(
    () => compile(bypass),
    /local Markdown link must target an explicit \.md source: \.\.\/\.\.\/english-route\//u,
  );

  const ignored = baseSources().map((entry) => {
    if (entry.source === "docs/guide.md") {
      return {
        ...entry,
        content: `${entry.content}\n[Section](#english-anchor)\n![Diagram](diagram.png)\n`,
      };
    }
    if (entry.source === "docs/guide.zh-CN.md") {
      return {
        ...entry,
        content: `${entry.content}\n[章节](#中文锚点)\n![图示](diagram.png)\n`,
      };
    }
    return entry;
  });
  assert.doesNotThrow(() => compile(ignored));
});

test("ignores code examples and resolves root READMEs through the catalog", () => {
  const codeExamples = baseSources().map((entry) =>
    entry.source === "docs/guide.md" || entry.source === "docs/guide.zh-CN.md"
      ? { ...entry, content: `${entry.content}\n\`[Example](missing.md)\`\n` }
      : entry,
  );
  assert.doesNotThrow(() => compile(codeExamples));

  const externalTargets = baseSources().map((entry) => {
    if (entry.source === "docs/guide.md") {
      return {
        ...entry,
        content: `${entry.content}\n[Repository](../README.md)\n`,
      };
    }
    if (entry.source === "docs/guide.zh-CN.md") {
      return {
        ...entry,
        content: `${entry.content}\n[仓库说明](../README.zh-CN.md)\n`,
      };
    }
    return entry;
  });
  assert.doesNotThrow(() => compile(externalTargets));
});

test("requires each root README to link to its localized documentation home", () => {
  const staleChineseHome = baseSources().map((entry) =>
    entry.source === "README.zh-CN.md"
      ? {
          ...entry,
          content: entry.content.replace(
            "https://jagger-l.github.io/awesome_agent/zh-cn/",
            "https://jagger-l.github.io/awesome_agent/",
          ),
        }
      : entry,
  );
  assert.throws(
    () => compile(staleChineseHome),
    /must preserve the localized documentation-home URL count/u,
  );
});

test("rejects missing, extra, unknown, and duplicate lock semantics", () => {
  const catalog = compile();
  const lock = createTranslationLock(catalog);

  assert.throws(
    () =>
      validateTranslationLock(catalog, { ...lock, pairs: lock.pairs.slice(1) }),
    /missing translation lock entry/u,
  );
  assert.throws(
    () =>
      validateTranslationLock(catalog, {
        ...lock,
        pairs: [
          ...lock.pairs,
          {
            english_source: "zzz.md",
            chinese_source: "zzz.zh-CN.md",
            english_sha256: "0".repeat(64),
            chinese_sha256: "1".repeat(64),
          },
        ],
      }),
    /unexpected translation lock entry/u,
  );
  assert.throws(
    () => validateTranslationLock(catalog, { ...lock, unknown: true }),
    /translation lock fields must be exactly/u,
  );
  assert.throws(
    () =>
      validateTranslationLock(catalog, {
        ...lock,
        pairs: [...lock.pairs, lock.pairs[0]],
      }),
    /duplicate lock English source/u,
  );
});

test("rejects a translated summary that drops source content", () => {
  const sources = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? {
          ...entry,
          content: '# 指南\n\n简短摘要。\n\n```python\nprint("catalog")\n```\n',
        }
      : entry,
  );
  assert.throws(
    () => compile(sources),
    /too short to preserve the English source/u,
  );
});

test("rejects an English shell under a translated heading", () => {
  const sources = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? { ...entry, content: ENGLISH_GUIDE.replace("# Guide", "# 指南") }
      : entry,
  );
  assert.throws(() => compile(sources), /too little Simplified Chinese prose/u);
});

test("rejects changed executable code, URLs, and inline-code multiplicity", () => {
  const changedCode = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? {
          ...entry,
          content: entry.content.replace(
            'print("catalog")',
            'print("changed")',
          ),
        }
      : entry,
  );
  assert.throws(() => compile(changedCode), /changes non-Mermaid fenced code/u);

  const changedUrl = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? {
          ...entry,
          content: entry.content.replace(
            "example.com/guide",
            "example.com/other",
          ),
        }
      : entry,
  );
  assert.throws(() => compile(changedUrl), /does not preserve external URLs/u);

  const changedInline = baseSources().map((entry) =>
    entry.source === "docs/guide.zh-CN.md"
      ? {
          ...entry,
          content: entry.content.replace(
            "`operation_busy`",
            "`operation_idle`",
          ),
        }
      : entry,
  );
  assert.throws(
    () => compile(changedInline),
    /does not preserve inline-code literals/u,
  );
});

test("allows localized Mermaid prose but preserves critical code spans", () => {
  const english = `# Flow

\`\`\`mermaid
flowchart LR
  Start[Read workspace/AGENTS.md] --> Done[Return result]
\`\`\`
`;
  const localized = `# 流程

\`\`\`mermaid
flowchart LR
  Start[读取 workspace/AGENTS.md] --> Done[返回结果]
\`\`\`
`;
  assert.deepEqual(
    translationContractFailures(english, localized, "mermaid fixture"),
    [],
  );
  assert.match(
    translationContractFailures(
      english,
      localized.replace("workspace/AGENTS.md", "workspace/OTHER.md"),
      "mermaid fixture",
    ).join("\n"),
    /changes technical literals in fenced block/u,
  );
});

test("the repository catalog contains the complete 46-pair translation lock", async () => {
  const catalog = await compileDocumentationCatalog();
  assert.equal(catalog.lockPairs.length, 46);
  assert.equal(catalog.pages.length, 86);
  assert.equal(catalog.navigationRoutes.length, 42);
  assert.ok(
    catalog.lockPairs.some(
      (pair) => pair.english.source === "site/content/index.mdx",
    ),
    "public homepage metadata must remain part of the translation lock",
  );
});
