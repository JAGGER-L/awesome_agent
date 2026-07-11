export type LaunchIntent =
  | { readonly kind: "new" }
  | { readonly kind: "continue" }
  | { readonly kind: "resume-picker" }
  | { readonly kind: "resume"; readonly threadId: string };

export class LaunchArgumentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LaunchArgumentError";
  }
}

export function parseLaunchIntent(argv: readonly string[]): LaunchIntent {
  if (argv.length === 0) return { kind: "new" };
  if (argv.length === 1 && argv[0] === "--continue") {
    return { kind: "continue" };
  }
  if (argv[0] === "--resume") {
    if (argv.length === 1) return { kind: "resume-picker" };
    if (argv.length === 2 && argv[1]) {
      return { kind: "resume", threadId: argv[1] };
    }
  }
  throw new LaunchArgumentError(
    "Usage: awesome-tui [--continue | --resume [thread_id]]",
  );
}
