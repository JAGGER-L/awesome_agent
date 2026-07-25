import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import {
  applicationResultSchema,
  commandNameSchema,
  commandOwners,
  eventEnvelopeSchema,
  eventTypes,
  jsonValueSchema,
  methodNames,
  methodSchemas,
  statusSnapshotSchema,
} from "../../src/protocol/index.js";
import { PRODUCT_VERSION } from "../../src/version.js";
import { defaultFixtureRoot, loadFixtureCorpus } from "./fixture-loader.js";

type MethodCase = {
  name: string;
  method: string;
  params: unknown;
  result?: unknown;
  expected?: { kind: string; code: string | number };
};

function cases(value: unknown): MethodCase[] {
  return (value as { cases: MethodCase[] }).cases;
}

describe("shared Python fixture corpus", () => {
  it("resolves from import.meta.url rather than process.cwd", async () => {
    const previous = process.cwd();
    const directory = await mkdtemp(join(tmpdir(), "awesome-fixture-cwd-"));
    try {
      process.chdir(directory);
      const corpus = await loadFixtureCorpus();
      expect(corpus.manifest.fixture_version).toBe(1);
    } finally {
      process.chdir(previous);
    }
  });

  it("matches product and literal inventories", async () => {
    const corpus = await loadFixtureCorpus();
    const packageJson = JSON.parse(
      await readFile(new URL("../../package.json", import.meta.url), "utf8"),
    ) as { version: string };
    expect(corpus.manifest.product_version).toBe(PRODUCT_VERSION);
    expect(packageJson.version).toBe(PRODUCT_VERSION);
    expect(corpus.manifest.methods).toEqual(methodNames);
    expect(corpus.manifest.event_types).toEqual(eventTypes);
    expect(corpus.manifest.command_owners).toEqual(commandOwners);
  });

  it("validates every method parameter and result sample", async () => {
    const corpus = await loadFixtureCorpus();
    for (const fixture of cases(corpus.files["methods.valid.json"])) {
      expect(fixture.method in methodSchemas, fixture.name).toBe(true);
      const method = fixture.method as keyof typeof methodSchemas;
      expect(
        methodSchemas[method].params.safeParse(fixture.params).success,
        fixture.name,
      ).toBe(true);
      expect(
        methodSchemas[method].result.safeParse(fixture.result).success,
        fixture.name,
      ).toBe(true);
    }
  });

  it("accepts Python-produced float usage and null model fallback", async () => {
    const corpus = await loadFixtureCorpus();
    const methods = cases(corpus.files["methods.valid.json"]);
    const application = methods.find(
      (fixture) => fixture.name === "application.get_state",
    )?.result as { value?: { usage?: unknown } } | undefined;
    expect(application?.value?.usage).toEqual({
      active_execution_seconds: 0.5,
    });
    expect(
      methodSchemas["application.getState"].result.safeParse(application)
        .success,
    ).toBe(true);

    const status = methods.find((fixture) => fixture.name === "command.execute")
      ?.result as { value?: { payload?: { snapshot?: unknown } } } | undefined;
    expect(
      statusSnapshotSchema.safeParse(status?.value?.payload?.snapshot).success,
    ).toBe(true);
  });

  it("distinguishes invalid params, unknown methods, and product failures", async () => {
    const corpus = await loadFixtureCorpus();
    for (const fixture of cases(corpus.files["methods.invalid.json"])) {
      const schema =
        methodSchemas[fixture.method as keyof typeof methodSchemas];
      if (fixture.expected?.code === -32601) {
        expect(schema, fixture.name).toBeUndefined();
      } else if (fixture.name === "initialize.protocol_incompatible") {
        expect(schema?.params.safeParse(fixture.params).success).toBe(false);
      } else if (fixture.expected?.kind === "product_error") {
        expect(
          schema?.params.safeParse(fixture.params).success,
          fixture.name,
        ).toBe(true);
      } else {
        expect(
          schema?.params.safeParse(fixture.params).success,
          fixture.name,
        ).toBe(false);
      }
    }
  });

  it("validates every Event and product failure", async () => {
    const corpus = await loadFixtureCorpus();
    const validEvents = (
      corpus.files["events.valid.json"] as { events: unknown[] }
    ).events;
    const invalidEvents = (
      corpus.files["events.invalid.json"] as { cases: { event: unknown }[] }
    ).cases;
    for (const event of validEvents)
      expect(eventEnvelopeSchema.safeParse(event).success).toBe(true);
    for (const { event } of invalidEvents)
      expect(eventEnvelopeSchema.safeParse(event).success).toBe(false);
    const failureSchema = applicationResultSchema(jsonValueSchema);
    for (const fixture of cases(corpus.files["results.failures.json"])) {
      expect(
        failureSchema.safeParse(fixture.result).success,
        fixture.name,
      ).toBe(true);
    }
  });

  it("validates the command inventory", async () => {
    const corpus = await loadFixtureCorpus();
    const commands = (
      corpus.files["commands.json"] as {
        commands: { name: string; owner: string }[];
      }
    ).commands;
    for (const command of commands) {
      expect(commandNameSchema.safeParse(command.name).success).toBe(true);
      expect(commandOwners[command.name as keyof typeof commandOwners]).toBe(
        command.owner,
      );
    }
  });
});

async function copyFixtureCorpus(target: string): Promise<URL> {
  await mkdir(target, { recursive: true });
  for (const fileName of [
    "manifest.json",
    "commands.json",
    "command-results.invalid.json",
    "command-results.valid.json",
    "events.invalid.json",
    "events.valid.json",
    "methods.invalid.json",
    "methods.valid.json",
    "results.failures.json",
  ]) {
    const source = new URL(fileName, defaultFixtureRoot);
    const destination = join(target, fileName);
    await mkdir(dirname(destination), { recursive: true });
    await writeFile(destination, await readFile(source));
  }
  return pathToFileURL(`${target}/`);
}

describe("fixture loader failures", () => {
  it("rejects a missing manifest", async () => {
    const directory = await mkdtemp(join(tmpdir(), "awesome-empty-fixtures-"));
    await expect(
      loadFixtureCorpus(pathToFileURL(`${directory}/`)),
    ).rejects.toThrow("missing");
  });

  it("rejects unsupported versions and hash mismatches", async () => {
    const directory = await mkdtemp(
      join(tmpdir(), "awesome-mutated-fixtures-"),
    );
    const root = await copyFixtureCorpus(directory);
    const manifestPath = fileURLToPath(new URL("manifest.json", root));
    const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as Record<
      string,
      unknown
    >;
    await writeFile(
      manifestPath,
      JSON.stringify({ ...manifest, fixture_version: 2 }),
    );
    await expect(loadFixtureCorpus(root)).rejects.toThrow();

    const restored = await copyFixtureCorpus(directory);
    await writeFile(new URL("commands.json", restored), "{}\n");
    await expect(loadFixtureCorpus(restored)).rejects.toThrow("hash mismatch");
  });

  it("rejects duplicate case identifiers", async () => {
    const directory = await mkdtemp(
      join(tmpdir(), "awesome-duplicate-fixtures-"),
    );
    const root = await copyFixtureCorpus(directory);
    const methodsPath = new URL("methods.valid.json", root);
    const methods = JSON.parse(await readFile(methodsPath, "utf8")) as {
      cases: unknown[];
    };
    methods.cases.push(methods.cases[0]);
    await writeFile(methodsPath, `${JSON.stringify(methods)}\n`);
    const manifestPath = new URL("manifest.json", root);
    const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as {
      files: Record<string, string>;
    };
    const { createHash } = await import("node:crypto");
    manifest.files["methods.valid.json"] = createHash("sha256")
      .update(await readFile(methodsPath))
      .digest("hex");
    await writeFile(manifestPath, `${JSON.stringify(manifest)}\n`);
    await expect(loadFixtureCorpus(root)).rejects.toThrow("Duplicate");
  });
});
