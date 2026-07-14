import { z } from "zod";

import { boundedText } from "./base.js";

export const modelIdentitySchema = z.strictObject({
  provider: z.enum(["deepseek", "kimi"]),
  configured_model: boundedText(1, 200),
  effective_model: boundedText(1, 200),
  runtime_name: z.literal("Awesome Agent"),
  fallback_active: z.boolean(),
  fallback_from: boundedText(1, 200).nullable().optional(),
});

export type ModelIdentitySnapshot = z.infer<typeof modelIdentitySchema>;
