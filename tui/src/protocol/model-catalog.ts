import { z } from "zod";

import { boundedText, safeIntegerSchema } from "./base.js";

export const providerIdentifierSchema = boundedText(1, 64).regex(
  /^[a-z][a-z0-9_-]{0,63}$/u,
);

const regionIdentifierSchema = boundedText(1, 64).regex(
  /^[a-z][a-z0-9_-]{0,63}$/u,
);

export const modelProfileSchema = z.strictObject({
  id: boundedText(1, 200),
  context_limit: safeIntegerSchema.min(1),
  supports_tools: z.boolean(),
  supports_reasoning: z.boolean(),
  is_default: z.boolean(),
});

export const providerDescriptorSchema = z
  .strictObject({
    id: providerIdentifierSchema,
    credential_id: boundedText(1, 64).regex(/^[a-z][a-z0-9_-]{0,63}$/u),
    supported_regions: z.array(regionIdentifierSchema).max(16),
    default_region: regionIdentifierSchema.nullable().optional(),
    models: z.array(modelProfileSchema).min(1).max(64),
  })
  .superRefine((provider, context) => {
    const regions = new Set(provider.supported_regions);
    if (regions.size !== provider.supported_regions.length) {
      context.addIssue({
        code: "custom",
        path: ["supported_regions"],
        message: "Provider regions must be unique",
      });
    }
    if (
      provider.default_region !== undefined &&
      provider.default_region !== null &&
      !regions.has(provider.default_region)
    ) {
      context.addIssue({
        code: "custom",
        path: ["default_region"],
        message: "Provider default region must be supported",
      });
    }
    if (
      provider.supported_regions.length > 0 &&
      (provider.default_region === undefined ||
        provider.default_region === null)
    ) {
      context.addIssue({
        code: "custom",
        path: ["default_region"],
        message: "A regional Provider requires a default region",
      });
    }
    if (
      provider.supported_regions.length === 0 &&
      provider.default_region !== undefined &&
      provider.default_region !== null
    ) {
      context.addIssue({
        code: "custom",
        path: ["default_region"],
        message: "A non-regional Provider cannot declare a default region",
      });
    }

    const modelIds = new Set<string>();
    let defaultCount = 0;
    for (const [index, model] of provider.models.entries()) {
      if (modelIds.has(model.id)) {
        context.addIssue({
          code: "custom",
          path: ["models", index, "id"],
          message: "Provider model identifiers must be unique",
        });
      }
      modelIds.add(model.id);
      if (!model.id.startsWith(`${provider.id}/`)) {
        context.addIssue({
          code: "custom",
          path: ["models", index, "id"],
          message: "Model identifier must belong to its Provider",
        });
      }
      if (model.is_default) defaultCount += 1;
    }
    if (defaultCount !== 1) {
      context.addIssue({
        code: "custom",
        path: ["models"],
        message: "Each Provider must declare exactly one default model",
      });
    }
  });

export const modelCatalogSchema = z
  .strictObject({
    providers: z.array(providerDescriptorSchema).min(1).max(32),
  })
  .superRefine(({ providers }, context) => {
    const providerIds = new Set<string>();
    const modelIds = new Set<string>();
    for (const [providerIndex, provider] of providers.entries()) {
      if (providerIds.has(provider.id)) {
        context.addIssue({
          code: "custom",
          path: ["providers", providerIndex, "id"],
          message: "Provider identifiers must be unique",
        });
      }
      providerIds.add(provider.id);
      for (const [modelIndex, model] of provider.models.entries()) {
        if (modelIds.has(model.id)) {
          context.addIssue({
            code: "custom",
            path: ["providers", providerIndex, "models", modelIndex, "id"],
            message: "Model identifiers must be globally unique",
          });
        }
        modelIds.add(model.id);
      }
    }
  });

export type ModelProfile = z.infer<typeof modelProfileSchema>;
export type ProviderDescriptor = z.infer<typeof providerDescriptorSchema>;
export type ModelCatalog = z.infer<typeof modelCatalogSchema>;
