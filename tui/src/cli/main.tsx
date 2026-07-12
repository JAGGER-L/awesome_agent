import { render, type Instance } from "ink";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import {
  createClipboardAdapter,
  type ClipboardAdapter,
} from "../adapters/clipboard.js";
import { App } from "../app/App.js";
import { CommandController } from "../commands/controller.js";
import { LocalCommandService } from "../commands/local.js";
import { AppErrorBoundary } from "../components/AppErrorBoundary.js";
import { FatalScreen } from "../components/FatalScreen.js";
import { Picker } from "../components/Picker.js";
import { ThemeProvider } from "../components/theme.js";
import { TrustPrompt } from "../components/TrustPrompt.js";
import { CoreSpawnError } from "../core/errors.js";
import type { CoreLaunchOptions } from "../core/process.js";
import { CancellationController } from "../lifecycle/cancellation.js";
import { ExitController, type ExitReason } from "../lifecycle/exit.js";
import {
  toFatalState,
  type FatalState,
  RenderFailure,
} from "../lifecycle/fatal.js";
import { InteractionController } from "../lifecycle/interactions.js";
import {
  routeTerminalKey,
  type TerminalKey,
} from "../interaction/key-router.js";
import type { TerminalUiState, UiMode } from "../interaction/model.js";
import { initialTerminalUiState } from "../interaction/reducer.js";
import { TerminalInput } from "../interaction/TerminalInput.js";
import { useTerminalUi } from "../interaction/use-terminal-ui.js";
import { resolveAwesomeHome } from "../preferences/paths.js";
import { loadPreferences, savePreferences } from "../preferences/store.js";
import {
  detectColorCapability,
  resolveTheme,
  type ThemePreference,
} from "../preferences/theme.js";
import type { SurfaceStore } from "../state/index.js";
import {
  connectSurface,
  type ConnectedSurface,
} from "../surface/controller.js";
import {
  beginStartup,
  respondStartupTrust,
  selectStartupThread,
  type StartupResult,
} from "../surface/startup.js";
import { PRODUCT_VERSION } from "../version.js";
import {
  CLI_HELP,
  LaunchArgumentError,
  parseCliIntent,
  type LaunchIntent,
} from "./args.js";
import {
  assertInteractiveTerminal,
  assertSupportedNode,
  resolveCoreExecutable,
  RuntimeCheckError,
} from "./runtime-checks.js";

export type CliRenderOutcome =
  | { readonly kind: "quit"; readonly exitCode: 0 }
  | { readonly kind: "trust_denied"; readonly exitCode: 0 }
  | { readonly kind: "fatal"; readonly exitCode: 1 | 2 };

export type StartupRenderState =
  | { readonly kind: "startup"; readonly startup: StartupResult }
  | { readonly kind: "fatal"; readonly fatal: FatalState };

export interface CliRenderRequest {
  readonly surface: ConnectedSurface;
  readonly intent: LaunchIntent;
  readonly state: StartupRenderState;
  readonly cwd: string;
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly stdoutIsTTY: boolean;
  readonly stdoutColorDepth: number | undefined;
  readonly clipboard: ClipboardAdapter;
}

export interface CliDependencies {
  readonly argv: readonly string[];
  readonly cwd: () => string;
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly nodeVersion: string;
  readonly stdinIsTTY: boolean;
  readonly stdoutIsTTY: boolean;
  readonly stdoutColorDepth: number | undefined;
  readonly coreExecutable: string;
  readonly writeStdout: (value: string) => void;
  readonly writeStderr: (value: string) => void;
  readonly startSurface: (
    options: CoreLaunchOptions,
  ) => Promise<ConnectedSurface>;
  readonly startApplication: (
    surface: ConnectedSurface,
    intent: LaunchIntent,
  ) => Promise<StartupResult>;
  readonly renderApplication: (
    request: CliRenderRequest,
  ) => Promise<CliRenderOutcome>;
}

export async function runCli(
  dependencies: CliDependencies = productionDependencies(),
): Promise<0 | 1 | 2> {
  let intent: LaunchIntent;
  try {
    const parsed = parseCliIntent(dependencies.argv);
    if (parsed.kind === "version") {
      dependencies.writeStdout(`${PRODUCT_VERSION}\n`);
      return 0;
    }
    if (parsed.kind === "help") {
      dependencies.writeStdout(CLI_HELP);
      return 0;
    }
    intent = parsed;
    assertSupportedNode(dependencies.nodeVersion);
    assertInteractiveTerminal(
      dependencies.stdinIsTTY,
      dependencies.stdoutIsTTY,
    );
  } catch (error) {
    if (
      error instanceof LaunchArgumentError ||
      error instanceof RuntimeCheckError
    ) {
      dependencies.writeStderr(`${error.message}\n`);
      return 2;
    }
    throw error;
  }

  const cwd = dependencies.cwd();
  let surface: ConnectedSurface;
  try {
    surface = await dependencies.startSurface({
      executable: dependencies.coreExecutable,
      cwd,
      env: dependencies.env,
    });
  } catch (error) {
    if (error instanceof CoreSpawnError) {
      dependencies.writeStderr(
        "Awesome Core could not be started. Reinstall AWESOME and try again.\n",
      );
      return 2;
    }
    dependencies.writeStderr("Unable to start the Awesome Core process.\n");
    return 1;
  }

  let state: StartupRenderState;
  try {
    state = {
      kind: "startup",
      startup: await dependencies.startApplication(surface, intent),
    };
  } catch (error) {
    const classified = toFatalState(error, surface.session);
    state = {
      kind: "fatal",
      fatal: classified ?? {
        kind: "render",
        message: "The terminal interface failed unexpectedly.",
      },
    };
  }
  try {
    const outcome = await dependencies.renderApplication({
      surface,
      intent,
      state,
      cwd,
      env: dependencies.env,
      stdoutIsTTY: dependencies.stdoutIsTTY,
      stdoutColorDepth: dependencies.stdoutColorDepth,
      clipboard: createClipboardAdapter(),
    });
    return outcome.exitCode;
  } catch {
    await surface.close().catch(() => undefined);
    dependencies.writeStderr("The terminal interface failed unexpectedly.\n");
    return 1;
  }
}

function productionDependencies(): CliDependencies {
  return {
    argv: process.argv.slice(2),
    cwd: () => process.cwd(),
    env: process.env,
    nodeVersion: process.versions.node,
    stdinIsTTY: process.stdin.isTTY === true,
    stdoutIsTTY: process.stdout.isTTY === true,
    stdoutColorDepth:
      typeof process.stdout.getColorDepth === "function"
        ? process.stdout.getColorDepth()
        : undefined,
    coreExecutable: resolveCoreExecutable(process.env),
    writeStdout: (value) => process.stdout.write(value),
    writeStderr: (value) => process.stderr.write(value),
    startSurface: connectSurface,
    startApplication: beginStartup,
    renderApplication: renderInkApplication,
  };
}

async function renderInkApplication(
  request: CliRenderRequest,
): Promise<CliRenderOutcome> {
  const awesomeHome = resolveAwesomeHome({ environ: request.env });
  const loaded = await loadPreferences(awesomeHome);
  let instance: Instance | undefined;
  let settled = false;
  return await new Promise<CliRenderOutcome>((resolve) => {
    const finish = (outcome: CliRenderOutcome) => {
      if (settled) return;
      settled = true;
      instance?.unmount();
      resolve(outcome);
    };
    instance = render(
      <CliApplication
        {...request}
        awesomeHome={awesomeHome}
        initialTheme={loaded.preferences.theme}
        {...(loaded.warnings[0]?.message
          ? { preferenceWarning: loaded.warnings[0].message }
          : {})}
        onFinish={finish}
        unmount={() => instance?.unmount()}
      />,
      { exitOnCtrlC: false },
    );
  });
}

type CliApplicationProps = CliRenderRequest & {
  readonly awesomeHome: string;
  readonly initialTheme: ThemePreference;
  readonly preferenceWarning?: string;
  readonly onFinish: (outcome: CliRenderOutcome) => void;
  readonly unmount: () => void;
};

function CliApplication(props: CliApplicationProps) {
  if (props.state.kind === "fatal") {
    return <StartupFatalApplication {...props} state={props.state} />;
  }
  return <RunningCliApplication {...props} state={props.state} />;
}

function StartupFatalApplication({
  surface,
  state,
  env,
  stdoutIsTTY,
  stdoutColorDepth,
  initialTheme,
  onFinish,
}: CliApplicationProps & {
  readonly state: Extract<StartupRenderState, { kind: "fatal" }>;
}) {
  const initial = initialTerminalUiState();
  const terminal = useTerminalUi({
    ...initial,
    mode: { kind: "fatal", selected: 0 },
  });
  const theme = resolveTheme(
    initialTheme,
    detectColorCapability(env, stdoutIsTTY, stdoutColorDepth),
  );
  const quit = useCallback(() => {
    void surface
      .close()
      .catch(() => undefined)
      .then(() => onFinish({ kind: "fatal", exitCode: 1 }));
  }, [onFinish, surface]);
  const handleInput = useCallback(
    (input: string, key: TerminalKey) => {
      const routed = routeTerminalKey(terminal.current.current, input, key);
      if (!routed) return;
      if (routed.type === "selection.confirm") {
        quit();
        return;
      }
      if (routed.type === "lifecycle.evaluate") quit();
    },
    [quit, terminal.current],
  );
  return (
    <ThemeProvider value={theme}>
      <TerminalInput onInput={handleInput} />
      <FatalScreen
        fatal={state.fatal}
        selected={
          terminal.state.mode.kind === "fatal"
            ? terminal.state.mode.selected
            : 0
        }
        startup
      />
    </ThemeProvider>
  );
}

function RunningCliApplication({
  surface,
  intent,
  state: { startup: initialStartup },
  env,
  stdoutIsTTY,
  stdoutColorDepth,
  clipboard,
  awesomeHome,
  initialTheme,
  preferenceWarning,
  onFinish,
  unmount,
}: CliApplicationProps & {
  readonly state: Extract<StartupRenderState, { kind: "startup" }>;
}) {
  const [startup, setStartup] = useState(initialStartup);
  const terminal = useTerminalUi(initialStartupUi(initialStartup));
  const terminalUi = terminal.state;
  const terminalUiRef = terminal.current;
  const dispatchTerminal = terminal.dispatch;
  const [themePreference, setThemePreference] = useState(initialTheme);
  const [fatal, setFatal] = useState<FatalState>();
  const state = useSyncExternalStore(
    surface.store.subscribe,
    surface.store.getState,
    surface.store.getState,
  );
  const theme = resolveTheme(
    themePreference,
    detectColorCapability(env, stdoutIsTTY, stdoutColorDepth),
  );
  const cancellation = useMemo(
    () =>
      new CancellationController({
        getState: surface.store.getState,
        subscribe: surface.store.subscribe,
        request: (params) => surface.request("operation.cancel", params),
      }),
    [surface],
  );
  const cancellationSnapshot = useSyncExternalStore(
    cancellation.subscribe,
    cancellation.snapshot,
    cancellation.snapshot,
  );
  const interactions = useMemo(
    () =>
      new InteractionController({
        getState: surface.store.getState,
        subscribe: surface.store.subscribe,
        request: (params) => surface.request("interaction.respond", params),
      }),
    [surface],
  );
  const commandController = useMemo(
    () => new CommandController(surface),
    [surface],
  );
  const exitController = useMemo(
    () =>
      new ExitController(surface.session, {
        disableInput: () => undefined,
        cleanupTerminal: unmount,
      }),
    [surface, unmount],
  );
  const requestExit = useCallback(
    async (reason: ExitReason) => {
      const outcome = await exitController.requestExit(reason);
      onFinish({
        kind: reason === "trust_denied" ? "trust_denied" : "quit",
        exitCode: outcome.exitCode,
      });
      return outcome;
    },
    [exitController, onFinish],
  );
  const localCommands = useMemo(
    () =>
      new LocalCommandService({
        clipboard,
        getThread: () => surface.store.getState().thread,
        getTheme: () => themePreference,
        setTheme: setThemePreference,
        saveTheme: async (selected) =>
          await savePreferences(awesomeHome, {
            schema_version: 1,
            theme: selected,
          }),
      }),
    [awesomeHome, clipboard, surface, themePreference],
  );

  if (!fatal && state.core_exit) {
    const classified = toFatalState(
      { ...state.core_exit, shutdown_requested: false },
      surface.session,
    );
    if (classified) queueMicrotask(() => setFatal(classified));
  }

  const renderFailure =
    fatal ??
    (state.fatal
      ? toFatalState(new RenderFailure(state.fatal.message), surface.session)
      : undefined);

  useEffect(() => {
    const mode = startupUiMode(startup, renderFailure !== undefined);
    if (mode && !sameStartupMode(terminalUi.mode, mode)) {
      dispatchTerminal({ type: "mode.open", mode });
    }
  }, [dispatchTerminal, renderFailure, startup, terminalUi.mode]);

  const submitStartupTrust = useCallback(
    (decision: "trust" | "deny") => {
      if (startup.kind !== "trust_required") return;
      dispatchTerminal({ type: "mode.trust.submitting", submitting: true });
      void respondStartupTrust(surface, intent, startup.interactionId, decision)
        .then((result) => {
          if (result.kind === "denied") {
            void requestExit("trust_denied");
          } else {
            setStartup(result);
          }
        })
        .catch((error: unknown) => {
          dispatchTerminal({
            type: "mode.trust.submitting",
            submitting: false,
          });
          dispatchTerminal({
            type: "mode.trust.message",
            message: error instanceof Error ? error.message : "Trust failed.",
          });
        });
    },
    [dispatchTerminal, intent, requestExit, startup, surface],
  );

  const handleStartupInput = useCallback(
    (input: string, key: TerminalKey) => {
      const routed = routeTerminalKey(terminalUiRef.current, input, key);
      if (!routed) return;
      if (routed.type === "selection.move") {
        dispatchTerminal({ type: "mode.select", delta: routed.delta });
        return;
      }
      if (routed.type === "selection.set") {
        dispatchTerminal({ type: "mode.set", selected: routed.selected });
        return;
      }
      if (routed.type === "trust.deny") {
        submitStartupTrust("deny");
        return;
      }
      if (routed.type !== "selection.confirm") return;
      const mode = terminalUiRef.current.mode;
      if (mode.kind === "fatal") {
        if (mode.selected === 0) return;
        void requestExit("quit_command");
        return;
      }
      if (
        mode.kind === "workspace_trust" &&
        startup.kind === "trust_required"
      ) {
        submitStartupTrust(mode.selected === 0 ? "trust" : "deny");
        return;
      }
      if (
        mode.kind === "picker" &&
        mode.owner.kind === "thread" &&
        startup.kind === "ready" &&
        startup.thread.kind === "selection_required"
      ) {
        const threadId = mode.selection.options[mode.selected]?.value;
        if (!threadId) return;
        void selectStartupThread(surface, threadId).then((thread) =>
          setStartup({ ...startup, thread }),
        );
      }
    },
    [
      dispatchTerminal,
      requestExit,
      startup,
      submitStartupTrust,
      surface,
      terminalUiRef,
    ],
  );

  const startupInputActive =
    renderFailure !== undefined ||
    startup.kind === "trust_required" ||
    (startup.kind === "ready" && startup.thread.kind === "selection_required");

  return (
    <ThemeProvider value={theme}>
      <AppErrorBoundary
        fallback={null}
        onError={(error) => {
          const classified = toFatalState(error, surface.session);
          if (classified) setFatal(classified);
        }}
      >
        {startupInputActive ? (
          <TerminalInput onInput={handleStartupInput} />
        ) : null}
        {renderFailure ? (
          <FatalScreen
            fatal={renderFailure}
            selected={
              terminalUi.mode.kind === "fatal" ? terminalUi.mode.selected : 0
            }
          />
        ) : startup.kind === "trust_required" ? (
          <TrustPrompt
            workspacePath={startup.workspacePath}
            selected={
              terminalUi.mode.kind === "workspace_trust"
                ? terminalUi.mode.selected
                : 0
            }
            submitting={
              terminalUi.mode.kind === "workspace_trust"
                ? terminalUi.mode.submitting
                : false
            }
            {...(terminalUi.mode.kind === "workspace_trust" &&
            terminalUi.mode.message !== undefined
              ? { message: terminalUi.mode.message }
              : {})}
          />
        ) : startup.kind === "denied" ? null : startup.thread.kind ===
          "selection_required" ? (
          <Picker
            selected={
              terminalUi.mode.kind === "picker" ? terminalUi.mode.selected : 0
            }
            selection={startup.thread.selection}
          />
        ) : (
          <App
            store={surface.store}
            controller={commandController}
            reportFatal={(error) => {
              const classified = toFatalState(error, surface.session);
              if (classified) setFatal(classified);
            }}
            localCommands={localCommands}
            cancellation={cancellationSnapshot}
            lifecycle={{
              cancelActiveOperation: () => cancellation.cancelActiveOperation(),
              resetThreadScope: () => {
                cancellation.reset();
                interactions.reset();
              },
              requestExit,
            }}
            interactionResponder={interactions}
            providerSetupRequired={startup.readiness === "diagnostics_ready"}
            welcome={{
              workspacePath: startup.application.workspace.display_path,
              thread:
                intent.kind === "new"
                  ? { kind: "new" }
                  : {
                      kind: "resumed",
                      title: startup.thread.thread.view.thread.title,
                    },
              model:
                startup.application.model_identity?.effective_model ??
                "model not configured",
              thinkingEnabled: startup.application.thinking_enabled,
              localMemoryEnabled: memoryEnabled(
                startup.application.memory_status,
                "local",
              ),
              mem0Enabled: memoryEnabled(
                startup.application.memory_status,
                "mem0",
              ),
              permissionMode: startup.application.permission_mode,
              theme,
            }}
          />
        )}
        {preferenceWarning ? null : null}
      </AppErrorBoundary>
    </ThemeProvider>
  );
}

function memoryEnabled(
  status: Readonly<Record<string, unknown>>,
  key: string,
): boolean {
  const value = status[key];
  if (typeof value === "boolean") return value;
  if (typeof value === "object" && value !== null && "enabled" in value) {
    return value.enabled === true;
  }
  return false;
}

export type { SurfaceStore };

function initialStartupUi(startup: StartupResult): TerminalUiState {
  const state = initialTerminalUiState();
  const mode = startupUiMode(startup, false);
  return mode ? { ...state, mode } : state;
}

function startupUiMode(
  startup: StartupResult,
  fatal: boolean,
): Exclude<UiMode, { kind: "composer" }> | undefined {
  if (fatal) return { kind: "fatal", selected: 0 };
  if (startup.kind === "trust_required") {
    return {
      kind: "workspace_trust",
      workspacePath: startup.workspacePath,
      selected: 0,
      submitting: false,
    };
  }
  if (
    startup.kind === "ready" &&
    startup.thread.kind === "selection_required"
  ) {
    return {
      kind: "picker",
      owner: { kind: "thread" },
      selection: startup.thread.selection,
      selected: Math.max(
        0,
        startup.thread.selection.options.findIndex((option) => option.selected),
      ),
      blocking: true,
    };
  }
  return undefined;
}

function sameStartupMode(current: UiMode, next: UiMode): boolean {
  if (current.kind !== next.kind) return false;
  if (current.kind === "workspace_trust" && next.kind === "workspace_trust") {
    return current.workspacePath === next.workspacePath;
  }
  if (current.kind === "picker" && next.kind === "picker") {
    return current.owner.kind === next.owner.kind;
  }
  return current.kind === "fatal" && next.kind === "fatal";
}
