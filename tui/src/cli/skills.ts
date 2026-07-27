import { createInterface } from "node:readline/promises";

import type { ConnectedSurface } from "../surface/controller.js";
import type { SkillCommandIntent } from "./args.js";

export type SkillCommandExitCode = 0 | 1;

export interface SkillCommandIo {
  readonly writeStdout: (value: string) => void;
  readonly writeStderr: (value: string) => void;
}

export async function runSkillCommand(
  surface: ConnectedSurface,
  intent: SkillCommandIntent,
  io: SkillCommandIo,
): Promise<SkillCommandExitCode> {
  if (intent.action === "list") {
    const response = await surface.request("skill.list", {});
    if (!response.ok) return reportFailure(response.error.message, io);
    io.writeStdout(formatSkillList(response.value.skills));
    return 0;
  }

  const response =
    intent.action === "install"
      ? await surface.request("skill.install", {
          source_path: intent.sourcePath,
          replace: intent.replace,
        })
      : await surface.request("skill.remove", { name: intent.name });
  if (!response.ok) return reportFailure(response.error.message, io);
  const verb =
    response.value.status === "installed"
      ? "Installed"
      : response.value.status === "replaced"
        ? "Replaced"
        : "Removed";
  io.writeStdout(
    `${verb} Skill ${response.value.name}. Restart Awesome to use this change.\n`,
  );
  return 0;
}

function reportFailure(message: string, io: SkillCommandIo): 1 {
  io.writeStderr(`${message}\n`);
  return 1;
}

export async function confirmSkillRemoval(name: string): Promise<boolean> {
  const terminal = createInterface({
    input: process.stdin,
    output: process.stderr,
    terminal: true,
  });
  try {
    const answer = await terminal.question(`Remove Skill ${name}? [y/N] `);
    return acceptsRemovalConfirmation(answer);
  } finally {
    terminal.close();
  }
}

export function acceptsRemovalConfirmation(answer: string): boolean {
  return /^(?:y|yes)$/iu.test(answer.trim());
}

export function formatSkillList(
  skills: readonly { readonly name: string; readonly description: string }[],
): string {
  if (skills.length === 0) return "No User Skills are installed.\n";
  return `Installed User Skills:\n${skills
    .map((skill) => `- ${skill.name}: ${safeDescription(skill.description)}\n`)
    .join("")}`;
}

function safeDescription(value: string): string {
  const normalized = value
    .replace(/[\p{Cc}\p{Cf}]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  return normalized || "(no description)";
}
