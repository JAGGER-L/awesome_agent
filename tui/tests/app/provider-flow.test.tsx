import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { CommandController } from "../../src/commands/controller.js";
import { RpcProtocolError } from "../../src/protocol/client.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../../src/protocol/methods.js";
import { createSurfaceStore } from "../../src/state/store.js";
import { freshModelCatalog } from "../fixtures/model-catalog.js";

async function eventually(assertion: () => void): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      assertion();
      return;
    } catch {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  assertion();
}

describe("Provider setup flow", () => {
  it("uses the catalog credential association to decide whether setup is required", () => {
    const application = applicationState(false);
    const [deepseekProvider] = application.model_catalog.providers;
    if (!deepseekProvider) throw new Error("DeepSeek fixture is missing");
    deepseekProvider.credential_id = "kimi";
    application.provider_credentials.kimi.awesome_configured = true;
    application.provider_credentials.kimi.selected_source = "awesome";
    const store = createSurfaceStore();
    store.dispatch({ type: "hydrate.application", application });

    const view = render(
      <App
        store={store}
        reportFatal={() => undefined}
        width={80}
        providerSetupRequired
      />,
    );

    expect(view.lastFrame()).not.toContain(
      "Choose a model Provider to get started.",
    );
    view.unmount();
  });

  it("validates a masked key and resumes /model after explicit unverified confirmation", async () => {
    let configured = false;
    let credentialAttempts = 0;
    const secret = "deepseek-secret-never-render";
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        if (method === "provider.credential.set") {
          credentialAttempts += 1;
          const allowUnverified =
            "allow_unverified" in params && params.allow_unverified === true;
          if (allowUnverified) configured = true;
          return {
            ok: true,
            value: {
              provider: "deepseek",
              status: allowUnverified
                ? "configured"
                : credentialAttempts === 1
                  ? "invalid"
                  : "confirm_unverified",
              source: allowUnverified ? "awesome" : null,
              code: allowUnverified
                ? "credential_saved_unverified"
                : credentialAttempts === 1
                  ? "credential_invalid"
                  : "credential_validation_unavailable",
            },
          } as never;
        }
        if (method === "application.getState") {
          return { ok: true, value: applicationState(configured) } as never;
        }
        if (method === "command.execute") {
          const arguments_ =
            "arguments" in params ? (params.arguments ?? []) : [];
          if (arguments_.length === 0) {
            return {
              ok: true,
              value: selectionOutcome(providerSelection("Select Provider")),
            } as never;
          }
          if (!configured) {
            return {
              ok: true,
              value: secretOutcome(secretPrompt("add")),
            } as never;
          }
          return {
            ok: true,
            value: selectionOutcome({
              prompt: "Select DeepSeek Model",
              options: [
                {
                  value: "deepseek/deepseek-v4-flash",
                  label: "deepseek/deepseek-v4-flash",
                  selected: true,
                },
              ],
            }),
          } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    });
    const store = createSurfaceStore();
    store.dispatch({
      type: "hydrate.application",
      application: applicationState(false),
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
        providerSetupRequired
      />,
    );
    const submit = vi.spyOn(controller, "submit");

    view.stdin.write("/model");
    view.stdin.write("\r");
    await eventually(() => expect(submit).toHaveBeenCalledOnce());
    await eventually(() =>
      expect(view.lastFrame()).toContain("Select Provider"),
    );
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("DeepSeek API Key"),
    );

    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("API key is required"),
    );
    expect(credentialAttempts).toBe(0);

    view.stdin.write(secret);
    await eventually(() => expect(view.lastFrame()).toContain("•"));
    expect(view.lastFrame()).not.toContain(secret);
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("API key was rejected"),
    );

    view.stdin.write(secret);
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Save this key anyway?"),
    );
    view.stdin.write("\u001b[B");
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Select DeepSeek Model"),
    );

    expect(credentialAttempts).toBe(3);
    expect(view.frames.join("\n")).not.toContain(secret);
    expect(JSON.stringify(store.getState())).not.toContain(secret);
  });

  it("contains a credential RPC failure and permits a second submission", async () => {
    let configured = false;
    let credentialAttempts = 0;
    const secret = "retry-secret-never-render";
    const request = vi.fn(
      async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        if (method === "provider.credential.set") {
          credentialAttempts += 1;
          if (credentialAttempts === 1) {
            throw new RpcProtocolError(-32603, "Internal error", {
              diagnostic_code: "core_request_failed",
            });
          }
          configured = true;
          return {
            ok: true,
            value: {
              provider: "deepseek",
              status: "configured",
              source: "awesome",
              code: "credential_saved",
            },
          } as never;
        }
        if (method === "application.getState") {
          return { ok: true, value: applicationState(configured) } as never;
        }
        if (method === "command.execute") {
          const arguments_ =
            "arguments" in params ? (params.arguments ?? []) : [];
          return {
            ok: true,
            value:
              arguments_.length === 0
                ? selectionOutcome(providerSelection("Select Provider"))
                : configured
                  ? selectionOutcome({
                      prompt: "Select DeepSeek Model",
                      options: [
                        {
                          value: "deepseek/deepseek-v4-flash",
                          label: "deepseek/deepseek-v4-flash",
                          selected: true,
                        },
                      ],
                    })
                  : secretOutcome(secretPrompt("add")),
          } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    );
    const reportFatal = vi.fn();
    const store = createSurfaceStore();
    store.dispatch({
      type: "hydrate.application",
      application: applicationState(false),
    });
    const view = render(
      <App
        store={store}
        controller={new CommandController({ request })}
        reportFatal={reportFatal}
        width={80}
      />,
    );

    view.stdin.write("/model");
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Select Provider"),
    );
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("DeepSeek API Key"),
    );
    view.stdin.write(secret);
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain(
        "Awesome could not complete this request. You can retry.",
      ),
    );
    expect(view.frames.join("\n")).not.toContain(secret);
    expect(reportFatal).not.toHaveBeenCalled();

    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Select DeepSeek Model"),
    );
    expect(credentialAttempts).toBe(2);
  });

  it("replaces and deletes a user-managed credential through one RPC contract", async () => {
    let configured = true;
    const secret = "replacement-secret-never-render";
    const credentialRequests: unknown[] = [];
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        if (method === "application.getState") {
          return { ok: true, value: applicationState(configured) } as never;
        }
        if (method === "provider.credential.set") {
          credentialRequests.push(params);
          const action = "action" in params ? params.action : undefined;
          configured = action !== "delete";
          return {
            ok: true,
            value: {
              provider: "deepseek",
              status: action === "delete" ? "deleted" : "configured",
              source: "awesome",
              code:
                action === "delete" ? "credential_deleted" : "credential_saved",
            },
          } as never;
        }
        if (method === "command.execute") {
          const arguments_ =
            "arguments" in params ? (params.arguments ?? []) : [];
          const selection =
            arguments_.length === 0
              ? providerSelection("Provider Authentication")
              : arguments_.length === 1
                ? {
                    prompt: "DeepSeek Authentication",
                    options: [
                      {
                        value: "replace",
                        label: "Replace API key",
                        selected: true,
                      },
                      {
                        value: "delete",
                        label: "Delete API key",
                        selected: false,
                      },
                      { value: "back", label: "Back", selected: false },
                    ],
                  }
                : arguments_[1] === "replace"
                  ? undefined
                  : {
                      prompt: "Delete DeepSeek API key?",
                      options: [
                        { value: "back", label: "Cancel", selected: true },
                        { value: "confirm", label: "Delete", selected: false },
                      ],
                    };
          return {
            ok: true,
            value:
              arguments_[1] === "replace"
                ? secretOutcome(secretPrompt("replace"))
                : selectionOutcome(requireSelection(selection)),
          } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    });
    const store = createSurfaceStore();
    store.dispatch({
      type: "hydrate.application",
      application: applicationState(true),
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    await openAuthProvider(view);
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("DeepSeek API Key"),
    );
    view.stdin.write(secret);
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("DeepSeek credential configured"),
    );
    expect(view.frames.join("\n")).not.toContain(secret);

    await openAuthProvider(view);
    view.stdin.write("\u001b[B");
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Delete DeepSeek API key?"),
    );
    view.stdin.write("\u001b[B");
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("DeepSeek credential deleted"),
    );

    expect(credentialRequests).toEqual([
      {
        provider: "deepseek",
        action: "replace",
        api_key: secret,
        allow_unverified: false,
      },
      { provider: "deepseek", action: "delete" },
    ]);
    expect(view.lastFrame()).toContain("Message");
  });

  it("cancels secret entry with Esc and restores the Composer", async () => {
    const controller = modelControllerWithoutCredential();
    const store = createSurfaceStore();
    store.dispatch({
      type: "hydrate.application",
      application: applicationState(false),
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/model");
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Select Provider"),
    );
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("DeepSeek API Key"),
    );
    view.stdin.write("\u001b");

    await eventually(() => expect(view.lastFrame()).toContain("Message"));
    expect(view.lastFrame()).not.toContain("DeepSeek API Key");
  });
});

async function openAuthProvider(
  view: ReturnType<typeof render>,
): Promise<void> {
  view.stdin.write("/auth");
  view.stdin.write("\r");
  await eventually(() =>
    expect(view.lastFrame()).toContain("Provider Authentication"),
  );
  view.stdin.write("\r");
  await eventually(() =>
    expect(view.lastFrame()).toContain("DeepSeek Authentication"),
  );
}

function providerSelection(prompt: string) {
  return {
    prompt,
    options: [
      { value: "deepseek", label: "DeepSeek", selected: true },
      { value: "kimi", label: "Kimi", selected: false },
    ],
  };
}

function secretPrompt(action: "add" | "replace") {
  return {
    provider: "deepseek" as const,
    action,
    label: "DeepSeek API Key",
    environment_variable: "DEEPSEEK_API_KEY",
    help_url: "https://example.com",
  };
}

function selectionOutcome(selection: {
  prompt: string;
  options: Array<{
    value: string;
    label: string;
    selected: boolean;
  }>;
}) {
  return {
    kind: "interaction" as const,
    interaction: { kind: "selection" as const, ...selection },
  };
}

function requireSelection<T>(selection: T | undefined): T {
  if (selection === undefined) throw new Error("Selection is required.");
  return selection;
}

function secretOutcome(prompt: ReturnType<typeof secretPrompt>) {
  return {
    kind: "interaction" as const,
    interaction: { kind: "secret" as const, ...prompt },
  };
}

function modelControllerWithoutCredential(): CommandController {
  return new CommandController({
    request: async <Method extends MethodName>(
      method: Method,
      params: MethodParams[Method],
    ) => {
      if (method !== "command.execute") {
        throw new Error(`Unexpected method ${method}`);
      }
      const arguments_ = "arguments" in params ? (params.arguments ?? []) : [];
      return {
        ok: true,
        value:
          arguments_.length === 0
            ? selectionOutcome(providerSelection("Select Provider"))
            : secretOutcome(secretPrompt("add")),
      } as never;
    },
  });
}

function applicationState(
  configured: boolean,
): MethodValue["application.getState"] {
  return {
    initialized: true,
    session_id: "session_1",
    workspace_key: "workspace_1",
    workspace: { display_path: "E:\\workspace" },
    workspace_trusted: true,
    current_thread_id: "thread_1",
    model_catalog: freshModelCatalog(),
    model_identity: {
      provider: "deepseek" as const,
      configured_model: "deepseek/deepseek-v4-flash",
      effective_model: "deepseek/deepseek-v4-flash",
      runtime_name: "Awesome Agent" as const,
      fallback_active: false,
    },
    thinking_enabled: false,
    skill_mode: "auto",
    permission_mode: "request_approval" as const,
    configuration_valid: true,
    secret_status: {
      deepseek_api_key: configured,
      moonshot_api_key: false,
      mem0_api_key: false,
    },
    provider_credentials: {
      deepseek: {
        provider: "deepseek" as const,
        environment_variable: "DEEPSEEK_API_KEY",
        environment_configured: false,
        awesome_configured: configured,
        selected_source: configured ? ("awesome" as const) : null,
      },
      kimi: {
        provider: "kimi" as const,
        environment_variable: "MOONSHOT_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: null,
      },
      mem0: {
        provider: "mem0" as const,
        environment_variable: "MEM0_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: null,
      },
      tavily: {
        provider: "tavily" as const,
        environment_variable: "TAVILY_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: "environment" as const,
      },
    },
    memory_status: {},
    mcp_status: [],
    usage: {},
    configuration_diagnostics: [],
  };
}
