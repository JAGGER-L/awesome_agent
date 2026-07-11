export type LaunchIntent =
  | { readonly kind: "new" }
  | { readonly kind: "continue" }
  | { readonly kind: "resume-picker" }
  | { readonly kind: "resume"; readonly threadId: string };

export type CliMetaIntent =
  | { readonly kind: "help" }
  | { readonly kind: "version" };

export type CliIntent = LaunchIntent | CliMetaIntent;

export const CLI_HELP = `Usage: awesome [--continue | --resume [thread_id]]

Options:
  --continue            Resume the most recent thread in this workspace
  --resume [thread_id]  Choose a recent thread or resume the given thread
  -V, --version         Print the installed product version
  -h, --help            Show this help
`;

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
  if (intent.kind === "help" || intent.kind === "version") {
    throw new LaunchArgumentError(CLI_HELP.trimEnd());
  }
  return intent;
}
