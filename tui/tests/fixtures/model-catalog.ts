import type { ModelCatalog } from "../../src/protocol/model-catalog.js";

export const modelCatalogFixture: ModelCatalog = {
  providers: [
    {
      id: "deepseek",
      credential_id: "deepseek",
      supported_regions: [],
      models: [
        {
          id: "deepseek/deepseek-v4-flash",
          context_limit: 262_144,
          supports_tools: true,
          supports_reasoning: true,
          is_default: true,
        },
        {
          id: "deepseek/deepseek-v4-pro",
          context_limit: 262_144,
          supports_tools: true,
          supports_reasoning: true,
          is_default: false,
        },
      ],
    },
    {
      id: "kimi",
      credential_id: "kimi",
      supported_regions: ["cn", "global"],
      default_region: "cn",
      models: [
        {
          id: "kimi/kimi-k2.6",
          context_limit: 262_144,
          supports_tools: true,
          supports_reasoning: true,
          is_default: true,
        },
        {
          id: "kimi/kimi-k2.5",
          context_limit: 262_144,
          supports_tools: true,
          supports_reasoning: true,
          is_default: false,
        },
      ],
    },
  ],
};

export function freshModelCatalog(): ModelCatalog {
  return structuredClone(modelCatalogFixture);
}
