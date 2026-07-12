import { chmod, mkdir, writeFile } from "node:fs/promises";
import { delimiter, join } from "node:path";

export interface CoreWrapper {
  readonly directory: string;
  readonly environment: Readonly<Record<string, string>>;
}

export async function createCoreWrapper({
  directory,
  repository,
  environment = {},
}: {
  readonly directory: string;
  readonly repository: string;
  readonly environment?: Readonly<Record<string, string>>;
}): Promise<CoreWrapper> {
  await mkdir(directory, { recursive: true });
  if (process.platform === "win32") {
    await writeFile(
      join(directory, "awesome-core.cmd"),
      [
        "@echo off",
        "echo fixture core log 1>&2",
        `uv --directory "${repository}" run python -m tests.fixtures.stdio_fake_services`,
        "",
      ].join("\r\n"),
      "utf8",
    );
  } else {
    const target = join(directory, "awesome-core");
    const escaped = repository.replaceAll("'", "'\\''");
    await writeFile(
      target,
      `#!/bin/sh\necho 'fixture core log' >&2\nexec uv --directory '${escaped}' run python -m tests.fixtures.stdio_fake_services\n`,
      "utf8",
    );
    await chmod(target, 0o755);
  }
  const pathKey =
    Object.keys(process.env).find((key) => key.toLowerCase() === "path") ??
    "PATH";
  return {
    directory,
    environment: {
      ...environment,
      [pathKey]: `${directory}${delimiter}${process.env[pathKey] ?? ""}`,
    },
  };
}
