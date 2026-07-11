export type GlobalKeyAction = { readonly kind: "cancel" };

export function mapGlobalKey({
  input,
  key,
  activeOperation,
}: {
  readonly input: string;
  readonly key: { readonly ctrl: boolean };
  readonly activeOperation: boolean;
  readonly composerEmpty: boolean;
}): GlobalKeyAction | undefined {
  return input.toLowerCase() === "c" && key.ctrl && activeOperation
    ? { kind: "cancel" }
    : undefined;
}
