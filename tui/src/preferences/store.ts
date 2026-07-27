import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { z } from "zod";

import {
  UI_PREFERENCES_CURRENT,
  UI_PREFERENCES_READABLE_VERSIONS,
} from "../contract-versions.js";

export const uiPreferencesSchema = z.strictObject({
  schema_version: z.literal(UI_PREFERENCES_CURRENT),
  theme: z.enum(["system", "dark", "light"]),
});

export type UiPreferencesV1 = z.infer<typeof uiPreferencesSchema>;

export interface PreferenceWarning {
  readonly code: "ui_preferences_invalid" | "ui_preferences_unreadable";
  readonly message: string;
}

export interface PreferenceIo {
  readFile(path: string, encoding: "utf8"): Promise<string>;
  mkdir(path: string, options: { recursive: true }): Promise<unknown>;
  writeFile(path: string, content: string, encoding: "utf8"): Promise<unknown>;
  rename(source: string, destination: string): Promise<unknown>;
  rm(path: string, options: { force: true }): Promise<unknown>;
  temporaryName(): string;
}

function isReadablePreferencesVersion(value: unknown): boolean {
  return (
    typeof value === "number" &&
    UI_PREFERENCES_READABLE_VERSIONS.some((version) => version === value)
  );
}

const defaultPreferences: UiPreferencesV1 = {
  schema_version: UI_PREFERENCES_CURRENT,
  theme: "system",
};

const defaultIo: PreferenceIo = {
  readFile,
  mkdir,
  writeFile,
  rename,
  rm,
  temporaryName: () =>
    `ui.json.${process.pid}.${Date.now().toString(36)}.${Math.random().toString(36).slice(2)}.tmp`,
};

export async function loadPreferences(
  awesomeHome: string,
  overrides: Partial<PreferenceIo> = {},
): Promise<{
  readonly preferences: UiPreferencesV1;
  readonly warnings: readonly PreferenceWarning[];
}> {
  const io = { ...defaultIo, ...overrides };
  let content: string;
  try {
    content = await io.readFile(join(awesomeHome, "ui.json"), "utf8");
  } catch (error) {
    if (errorCode(error) === "ENOENT") {
      return { preferences: defaultPreferences, warnings: [] };
    }
    return {
      preferences: defaultPreferences,
      warnings: [
        {
          code: "ui_preferences_unreadable",
          message: "Unable to read ui.json.",
        },
      ],
    };
  }

  try {
    const parsed: unknown = JSON.parse(content);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      !("schema_version" in parsed) ||
      !isReadablePreferencesVersion(parsed.schema_version)
    ) {
      throw new Error("unsupported UI preferences version");
    }
    const preferences = uiPreferencesSchema.parse(parsed);
    return { preferences, warnings: [] };
  } catch {
    return {
      preferences: defaultPreferences,
      warnings: [
        {
          code: "ui_preferences_invalid",
          message: "Invalid ui.json; using system theme.",
        },
      ],
    };
  }
}

export async function savePreferences(
  awesomeHome: string,
  value: UiPreferencesV1,
  overrides: Partial<PreferenceIo> = {},
): Promise<void> {
  const preferences = uiPreferencesSchema.parse(value);
  const io = { ...defaultIo, ...overrides };
  const destination = join(awesomeHome, "ui.json");
  const temporary = join(awesomeHome, io.temporaryName());
  await io.mkdir(awesomeHome, { recursive: true });
  try {
    await io.writeFile(
      temporary,
      `${JSON.stringify(preferences, null, 2)}\n`,
      "utf8",
    );
    await io.rename(temporary, destination);
  } catch (error) {
    await io.rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

function errorCode(error: unknown): string | undefined {
  return typeof error === "object" && error !== null && "code" in error
    ? String(error.code)
    : undefined;
}
