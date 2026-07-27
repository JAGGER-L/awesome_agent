export type LaunchIntent =
  | { readonly kind: "new" }
  | { readonly kind: "continue" }
  | { readonly kind: "resume-picker" }
  | { readonly kind: "resume"; readonly threadId: string };

export type HeadlessFormat = "text" | "json";

export type HeadlessPermissionMode =
  | "request_approval"
  | "accept_edits"
  | "full_access";

export type HeadlessThreadTarget =
  | { readonly kind: "new" }
  | { readonly kind: "thread"; readonly threadId: string };

export interface HeadlessRunIntent {
  readonly kind: "run";
  readonly prompt: string;
  readonly target: HeadlessThreadTarget;
  readonly format: HeadlessFormat;
  readonly trustWorkspace: boolean;
  readonly permissionMode?: HeadlessPermissionMode;
  readonly allowNetwork: boolean;
}

export type SkillCommandIntent =
  | { readonly kind: "skills"; readonly action: "list" }
  | {
      readonly kind: "skills";
      readonly action: "install";
      readonly sourcePath: string;
      readonly replace: boolean;
    }
  | {
      readonly kind: "skills";
      readonly action: "remove";
      readonly name: string;
      readonly yes: boolean;
    };

export type CliMetaIntent =
  | { readonly kind: "help" }
  | { readonly kind: "version" };

export type CliIntent =
  | LaunchIntent
  | HeadlessRunIntent
  | SkillCommandIntent
  | CliMetaIntent;

export const CLI_HELP = `Usage: awesome [--continue | --resume [thread_id]]
       awesome run <prompt> [--new | --thread <id>] [options]
       awesome skills list
       awesome skills install <local-directory-or-zip> [--replace]
       awesome skills remove <name> [--yes]

Options:
  --continue            Resume the most recent thread in this workspace
  --resume [thread_id]  Choose a recent thread or resume the given thread
  -V, --version         Print the installed product version
  -h, --help            Show this help

Headless run options:
  --new                  Create a new thread (default)
  --thread <id>          Run in the selected existing thread
  --format <text|json>   Select final output format (default: text)
  --trust-workspace      Trust this workspace for the current startup flow
  --permission-mode <request_approval|accept_edits|full_access>
                         Select the process-local permission mode
  --allow-network        Declare network intent for this process only

Skill management:
  list                   List installed User Skills
  install <path>         Install a local directory or ZIP as a User Skill
  --replace              Replace an installed Skill with the same name
  remove <name>          Remove an installed User Skill
  --yes                  Confirm removal without an interactive prompt
`;

const MAX_PROMPT_LENGTH = 200_000;
const MAX_THREAD_ID_LENGTH = 128;
const MAX_SKILL_SOURCE_PATH_LENGTH = 4_096;
const SKILL_NAME = /^[a-z][a-z0-9-]{0,63}$/u;
const HEADLESS_FORMATS = new Set<HeadlessFormat>(["text", "json"]);
const HEADLESS_PERMISSION_MODES = new Set<HeadlessPermissionMode>([
  "request_approval",
  "accept_edits",
  "full_access",
]);

export class LaunchArgumentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LaunchArgumentError";
  }
}

export function parseCliIntent(argv: readonly string[]): CliIntent {
  if (argv.length === 0) return { kind: "new" };
  if (argv.length === 1 && ["--version", "-V"].includes(argv[0] ?? "")) {
    return { kind: "version" };
  }
  if (argv.length === 1 && ["--help", "-h"].includes(argv[0] ?? "")) {
    return { kind: "help" };
  }
  if (argv.length === 1 && argv[0] === "--continue") {
    return { kind: "continue" };
  }
  if (argv[0] === "run") return parseHeadlessRunIntent(argv.slice(1));
  if (argv[0] === "skills") return parseSkillCommandIntent(argv.slice(1));
  if (argv[0] === "--resume") {
    if (argv.length === 1) return { kind: "resume-picker" };
    if (argv.length === 2 && argv[1]) {
      return { kind: "resume", threadId: argv[1] };
    }
  }
  throw new LaunchArgumentError(CLI_HELP.trimEnd());
}

export function parseLaunchIntent(argv: readonly string[]): LaunchIntent {
  const intent = parseCliIntent(argv);
  if (
    intent.kind === "help" ||
    intent.kind === "version" ||
    intent.kind === "run" ||
    intent.kind === "skills"
  ) {
    throw new LaunchArgumentError(CLI_HELP.trimEnd());
  }
  return intent;
}

function parseSkillCommandIntent(argv: readonly string[]): SkillCommandIntent {
  const action = argv[0];
  if (action === "list" && argv.length === 1) {
    return { kind: "skills", action: "list" };
  }
  if (action === "install") {
    const sourcePath = argv[1];
    if (!validSkillSourcePath(sourcePath)) throw invalidArguments();
    if (argv.length === 2) {
      return { kind: "skills", action: "install", sourcePath, replace: false };
    }
    if (argv.length === 3 && argv[2] === "--replace") {
      return { kind: "skills", action: "install", sourcePath, replace: true };
    }
    throw invalidArguments();
  }
  if (action === "remove") {
    const name = argv[1];
    if (name === undefined || !SKILL_NAME.test(name)) throw invalidArguments();
    if (argv.length === 2) {
      return { kind: "skills", action: "remove", name, yes: false };
    }
    if (argv.length === 3 && argv[2] === "--yes") {
      return { kind: "skills", action: "remove", name, yes: true };
    }
  }
  throw invalidArguments();
}

function validSkillSourcePath(value: string | undefined): value is string {
  return (
    value !== undefined &&
    value.length > 0 &&
    value === value.trim() &&
    !value.startsWith("--") &&
    !/[\0\r\n]/u.test(value) &&
    Array.from(value).length <= MAX_SKILL_SOURCE_PATH_LENGTH
  );
}

function parseHeadlessRunIntent(argv: readonly string[]): HeadlessRunIntent {
  const prompt = argv[0];
  if (
    prompt === undefined ||
    prompt.trim().length === 0 ||
    prompt.startsWith("--") ||
    Array.from(prompt).length > MAX_PROMPT_LENGTH
  ) {
    throw invalidArguments();
  }

  let target: HeadlessThreadTarget = { kind: "new" };
  let targetSelected = false;
  let format: HeadlessFormat = "text";
  let formatSelected = false;
  let trustWorkspace = false;
  let permissionMode: HeadlessPermissionMode | undefined;
  let allowNetwork = false;

  for (let index = 1; index < argv.length; index += 1) {
    const argument = argv[index];
    switch (argument) {
      case "--new":
        if (targetSelected) throw invalidArguments();
        target = { kind: "new" };
        targetSelected = true;
        break;
      case "--thread": {
        if (targetSelected) throw invalidArguments();
        const threadId = optionValue(argv, index + 1);
        if (Array.from(threadId).length > MAX_THREAD_ID_LENGTH) {
          throw invalidArguments();
        }
        target = { kind: "thread", threadId };
        targetSelected = true;
        index += 1;
        break;
      }
      case "--format": {
        if (formatSelected) throw invalidArguments();
        const value = optionValue(argv, index + 1);
        if (!HEADLESS_FORMATS.has(value as HeadlessFormat)) {
          throw invalidArguments();
        }
        format = value as HeadlessFormat;
        formatSelected = true;
        index += 1;
        break;
      }
      case "--trust-workspace":
        if (trustWorkspace) throw invalidArguments();
        trustWorkspace = true;
        break;
      case "--permission-mode": {
        if (permissionMode !== undefined) throw invalidArguments();
        const value = optionValue(argv, index + 1);
        if (!HEADLESS_PERMISSION_MODES.has(value as HeadlessPermissionMode)) {
          throw invalidArguments();
        }
        permissionMode = value as HeadlessPermissionMode;
        index += 1;
        break;
      }
      case "--allow-network":
        if (allowNetwork) throw invalidArguments();
        allowNetwork = true;
        break;
      default:
        throw invalidArguments();
    }
  }

  return {
    kind: "run",
    prompt,
    target,
    format,
    trustWorkspace,
    ...(permissionMode === undefined ? {} : { permissionMode }),
    allowNetwork,
  };
}

function optionValue(argv: readonly string[], index: number): string {
  const value = argv[index];
  if (
    value === undefined ||
    value.trim().length === 0 ||
    value.startsWith("--")
  ) {
    throw invalidArguments();
  }
  return value;
}

function invalidArguments(): LaunchArgumentError {
  return new LaunchArgumentError(CLI_HELP.trimEnd());
}
