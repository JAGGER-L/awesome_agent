import { render, type Instance } from "ink";
import { useCallback, useMemo, useState, useSyncExternalStore } from "react";

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

export interface CliRenderRequest {
  readonly surface: ConnectedSurface;
  readonly intent: LaunchIntent;
  readonly startup: StartupResult;
  readonly cwd: string;
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly clipboard: ClipboardAdapter;
}

export interface CliDependencies {
  readonly argv: readonly string[];
  readonly cwd: () => string;
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly nodeVersion: string;
  readonly stdinIsTTY: boolean;
  readonly stdoutIsTTY: boolean;
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

  try {
    const startup = await dependencies.startApplication(surface, intent);
    const outcome = await dependencies.renderApplication({
      surface,
      intent,
      startup,
      cwd,
      env: dependencies.env,
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

function CliApplication({
  surface,
  intent,
  startup: initialStartup,
  env,
  clipboard,
  awesomeHome,
  initialTheme,
  preferenceWarning,
  onFinish,
  unmount,
}: CliRenderRequest & {
  readonly awesomeHome: string;
  readonly initialTheme: ThemePreference;
  readonly preferenceWarning?: string;
  readonly onFinish: (outcome: CliRenderOutcome) => void;
  readonly unmount: () => void;
}) {
  const [startup, setStartup] = useState(initialStartup);
  const [themePreference, setThemePreference] = useState(initialTheme);
  const [fatal, setFatal] = useState<FatalState>();
  const state = useSyncExternalStore(
    surface.store.subscribe,
    surface.store.getState,
    surface.store.getState,
  );
  const theme = resolveTheme(
    themePreference,
    detectColorCapability(env, process.stdout.isTTY === true),
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

  return (
    <ThemeProvider value={theme}>
      <AppErrorBoundary
        fallback={null}
        onError={(error) => {
          const classified = toFatalState(error, surface.session);
          if (classified) setFatal(classified);
        }}
      >
        {renderFailure ? (
          <FatalScreen
            fatal={renderFailure}
            onReconnect={() => undefined}
            onQuit={() => void requestExit("quit_command")}
          />
        ) : startup.kind === "trust_required" ? (
          <TrustPrompt
            workspacePath={startup.workspacePath}
            onDecision={(decision) => {
              void respondStartupTrust(
                surface,
                intent,
                startup.interactionId,
                decision,
              ).then((result) => {
                if (result.kind === "denied") {
                  void requestExit("trust_denied");
                } else {
                  setStartup(result);
                }
              });
            }}
          />
        ) : startup.kind === "denied" ? null : startup.thread.kind ===
          "selection_required" ? (
          <Picker
            blocking
            selection={startup.thread.selection}
            onClose={() => undefined}
            onSelect={(threadId) => {
              void selectStartupThread(surface, threadId).then((thread) =>
                setStartup({ ...startup, thread }),
              );
            }}
          />
        ) : (
          <App
            store={surface.store}
            controller={commandController}
            localCommands={localCommands}
            cancellation={cancellationSnapshot}
            lifecycle={{
              cancelActiveOperation: () => cancellation.cancelActiveOperation(),
              requestExit,
            }}
            interactionResponder={interactions}
            welcome={{
              workspacePath: startup.application.workspace.display_path,
              branch: startup.application.workspace.branch,
              thread:
                intent.kind === "new"
                  ? { kind: "new" }
                  : {
                      kind: "resumed",
                      title: startup.thread.thread.view.thread.title,
                    },
              model:
                startup.application.current_model ?? "model not configured",
              thinkingEnabled: startup.application.thinking_enabled,
              localMemoryEnabled: memoryEnabled(
                startup.application.memory_status,
                "local",
              ),
              mem0Enabled: memoryEnabled(
                startup.application.memory_status,
                "mem0",
              ),
              credentialMissing: startup.readiness === "diagnostics_ready",
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
