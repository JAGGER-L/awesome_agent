import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { z } from "zod";

const fileNames = [
  "commands.json",
  "events.invalid.json",
  "events.valid.json",
  "methods.invalid.json",
  "methods.valid.json",
  "results.failures.json",
] as const;

const manifestSchema = z.strictObject({
  fixture_version: z.literal(1),
  product_version: z.string(),
  protocol_version: z.literal(1),
  methods: z.array(z.string()),
  event_types: z.array(z.string()),
  command_owners: z.record(z.string(), z.string()),
  files: z.record(z.enum(fileNames), z.string().regex(/^[a-f0-9]{64}$/)),
});

export type FixtureManifest = z.infer<typeof manifestSchema>;

export interface FixtureCorpus {
  readonly manifest: FixtureManifest;
  readonly files: Readonly<Record<(typeof fileNames)[number], unknown>>;
}

export const defaultFixtureRoot = new URL(
  "../../../protocol/fixtures/v1/",
  import.meta.url,
);

function parseJson(bytes: Uint8Array, name: string): unknown {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) {
    throw new Error(`Invalid fixture JSON: ${name}`, { cause: error });
  }
}

function assertUniqueCaseIds(value: unknown, fileName: string): void {
  if (typeof value !== "object" || value === null) {
    return;
  }
  const record = value as Record<string, unknown>;
  const cases = record.cases ?? record.events ?? record.commands;
  if (!Array.isArray(cases)) {
    return;
  }
  const ids = cases.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const candidate = item as Record<string, unknown>;
    const id = candidate.name ?? candidate.event_id;
    return typeof id === "string" ? [id] : [];
  });
  if (new Set(ids).size !== ids.length) {
    throw new Error(`Duplicate fixture case ID in ${fileName}`);
  }
}

export async function loadFixtureCorpus(
  fixtureRoot: URL = defaultFixtureRoot,
): Promise<FixtureCorpus> {
  let manifestBytes: Uint8Array;
  try {
    manifestBytes = await readFile(new URL("manifest.json", fixtureRoot));
  } catch (error) {
    throw new Error("Protocol fixture manifest is missing", { cause: error });
  }
  const manifest = manifestSchema.parse(
    parseJson(manifestBytes, "manifest.json"),
  );
  const files = {} as Record<(typeof fileNames)[number], unknown>;
  for (const fileName of fileNames) {
    const bytes = await readFile(new URL(fileName, fixtureRoot));
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (digest !== manifest.files[fileName]) {
      throw new Error(`Fixture hash mismatch: ${fileName}`);
    }
    const value = parseJson(bytes, fileName);
    assertUniqueCaseIds(value, fileName);
    files[fileName] = value;
  }
  return { manifest, files };
}
