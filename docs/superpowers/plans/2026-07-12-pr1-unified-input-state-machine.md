# Unified Input State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one explicit UI mode and one Ink input listener the sole authority for terminal keys.

**Architecture:** A pure reducer owns composer and overlay state. One root `TerminalInput` hook converts Ink keys into normalized intents and dispatches them to the reducer. Render components become controlled views with no `useInput`, submission refs, or hidden focus flags.

**Tech Stack:** TypeScript 7, React 19, Ink 7, Vitest, Zod.

## Global Constraints

- Do not change Python runtime behavior or JSON-RPC product semantics in this PR.
- Do not keep the old component-level input listeners beside the new router.
- Do not add a general event bus, global state library, or React context framework.
- Preserve Ctrl+C cancellation/clear/double-exit and Ctrl+D empty-exit behavior.
- Every modal transition must have a deterministic success, failure, cancel, and focus-restoration state.

---

### Task 1: Define the terminal interaction model

**Files:**
- Create: `tui/src/interaction/model.ts`
- Create: `tui/src/interaction/reducer.ts`
- Create: `tui/tests/interaction/reducer.test.ts`

**Interfaces:**
- Produces: `TerminalUiState`, `UiMode`, `TerminalUiAction`, `initialTerminalUiState()`, `terminalUiReducer()`.
- Consumes: existing composer reducer concepts and protocol selection/interaction projections.

- [ ] **Step 1: Write failing reducer tests**

```ts
it("allows exactly one active input owner", () => {
  const state = terminalUiReducer(initialTerminalUiState(), {
    type: "mode.open",
    mode: { kind: "picker", owner: "command", selected: 0 },
  });
  expect(state.mode).toEqual({ kind: "picker", owner: "command", selected: 0 });
});

it("restores the composer after cancelling a modal", () => {
  const opened = terminalUiReducer(initialTerminalUiState(), {
    type: "mode.open",
    mode: { kind: "secret", provider: "deepseek", value: "" },
  });
  expect(terminalUiReducer(opened, { type: "mode.cancel" }).mode).toEqual({
    kind: "composer",
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npm --prefix tui run test -- tests/interaction/reducer.test.ts`

Expected: FAIL because `src/interaction/reducer.ts` does not exist.

- [ ] **Step 3: Implement the explicit union and reducer**

```ts
export type UiMode =
  | { readonly kind: "composer" }
  | { readonly kind: "command_menu"; readonly selected: number }
  | { readonly kind: "picker"; readonly owner: "command" | "theme" | "thread"; readonly selected: number }
  | { readonly kind: "secret"; readonly provider: "deepseek" | "kimi"; readonly value: string; readonly submitting?: boolean; readonly message?: string }
  | { readonly kind: "approval"; readonly interactionId: string; readonly selected: number; readonly submitting?: boolean; readonly message?: string }
  | { readonly kind: "permission_confirmation"; readonly selected: number };

export interface TerminalUiState {
  readonly mode: UiMode;
  readonly composer: ComposerState;
  readonly notice?: string;
}
```

The reducer must reject impossible transitions in development tests rather than silently combining modes.

- [ ] **Step 4: Run the reducer tests and verify GREEN**

Run: `npm --prefix tui run test -- tests/interaction/reducer.test.ts`

Expected: PASS.

### Task 2: Normalize and route keys once

**Files:**
- Create: `tui/src/interaction/key-router.ts`
- Create: `tui/src/interaction/TerminalInput.tsx`
- Create: `tui/tests/interaction/key-router.test.ts`
- Modify: `tui/src/app/global-keys.ts`

**Interfaces:**
- Produces: `TerminalKey`, `TerminalIntent`, `routeTerminalKey(state, key)` and one component containing the only production `useInput` call.
- Consumes: `TerminalUiState` from Task 1.

- [ ] **Step 1: Write the routing matrix tests**

```ts
it.each([
  ["approval", { return: true }, "approval.confirm"],
  ["secret", { escape: true }, "mode.cancel"],
  ["command_menu", { tab: true }, "command.complete"],
  ["composer", { return: true }, "composer.submit"],
])("routes %s keys only to its owner", (kind, key, expected) => {
  expect(routeTerminalKey(stateWithMode(kind), terminalKey(key)).type).toBe(expected);
});
```

- [ ] **Step 2: Verify RED**

Run: `npm --prefix tui run test -- tests/interaction/key-router.test.ts`

Expected: FAIL because the router is absent.

- [ ] **Step 3: Implement the priority table**

The router order must be `secret → approval → permission_confirmation → picker → command_menu → composer → lifecycle`. It returns one intent or `undefined`; it never calls product services.

- [ ] **Step 4: Verify GREEN**

Run: `npm --prefix tui run test -- tests/interaction/key-router.test.ts`

Expected: PASS for Enter, Tab, Esc, arrows, Ctrl+C, and Ctrl+D cases.

### Task 3: Convert App and views to controlled rendering

**Files:**
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/cli/main.tsx`
- Modify: `tui/src/components/Composer.tsx`
- Modify: `tui/src/components/Picker.tsx`
- Modify: `tui/src/components/SecretInput.tsx`
- Modify: `tui/src/components/InteractionPrompt.tsx`
- Delete: component-level `useInput` branches from all four components.
- Test: `tui/tests/app/global-keys-app.test.tsx`
- Test: `tui/tests/components/composer.test.tsx`
- Test: `tui/tests/components/picker.test.tsx`
- Test: `tui/tests/components/secret-input.test.tsx`
- Test: `tui/tests/components/interaction-prompt.test.tsx`

**Interfaces:**
- `CliApplication` owns `useReducer(terminalUiReducer, ...)` and renders exactly one `TerminalInput` above both startup and ready screens; `App` receives controlled state/actions for the ready product surface.
- Views receive `state` plus explicit `onIntent`; they do not read stdin.

- [ ] **Step 1: Change tests to assert one owner**

Add a test where Approval is visible, send Enter, and assert only `interactionResponder.respond` runs; Composer submit and lifecycle actions remain untouched.

- [ ] **Step 2: Verify RED against the current multi-listener tree**

Run: `npm --prefix tui run test -- tests/app/global-keys-app.test.tsx tests/components/interaction-prompt.test.tsx`

Expected: at least the new ownership assertion fails.

- [ ] **Step 3: Replace local modal state and refs**

Remove `picker`, `credentialFlow`, `helpCommand`, and `commandInputBlocked` as independent input authorities. Move the reducer and sole input listener to `CliApplication` so PR2 can route startup Trust through the same owner. Keep product-call orchestration in focused callbacks, but every result must dispatch a reducer action.

- [ ] **Step 4: Delete old listeners and obsolete tests**

Search: `rg -n "useInput|commandInputBlocked" tui/src`

Expected: only `interaction/TerminalInput.tsx` contains `useInput`; `commandInputBlocked` has zero matches.

- [ ] **Step 5: Run PR validation**

```bash
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/interaction tests/app/global-keys-app.test.tsx tests/components/composer.test.tsx tests/components/picker.test.tsx tests/components/secret-input.test.tsx tests/components/interaction-prompt.test.tsx
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add tui/src tui/tests
git commit -m "refactor: unify terminal input ownership"
```
