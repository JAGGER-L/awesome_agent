import { z } from "zod";

import { boundedText } from "./base.js";
import { providerIdentifierSchema } from "./model-catalog.js";

export const modelIdentitySchema = z
  .strictObject({
    provider: providerIdentifierSchema,
    configured_model: boundedText(1, 200),
    effective_model: boundedText(1, 200),
    runtime_name: z.literal("Awesome Agent"),
    fallback_active: z.boolean(),
    fallback_from: boundedText(1, 200).nullable().optional(),
  })
  .superRefine((identity, context) => {
    const fallbackActive =
      identity.configured_model !== identity.effective_model;
    if (identity.fallback_active !== fallbackActive) {
      context.addIssue({
        code: "custom",
        path: ["fallback_active"],
        message:
          "Fallback state must match the configured and effective models",
      });
    }
    const fallbackFrom = identity.fallback_from ?? null;
    const expectedFrom = fallbackActive ? identity.configured_model : null;
    if (fallbackFrom !== expectedFrom) {
      context.addIssue({
        code: "custom",
        path: ["fallback_from"],
        message: "Fallback source must match the configured model",
      });
    }
  });

export type ModelIdentitySnapshot = z.infer<typeof modelIdentitySchema>;
