import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { App } from "../../src/app/App.js";
import { CommandController } from "../../src/commands/controller.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";
import { createSurfaceStore } from "../../src/state/store.js";

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
  it("resumes from a saved credential into the model picker without rendering it", async () => {
    let configured = false;
    const secret = "deepseek-secret-never-render";
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        if (method === "provider.credential.set") {
          configured = true;
          return {
            ok: true,
            value: {
              provider: "deepseek",
              status: "saved",
              source: "user_env_file",
              code: "credential_saved",
            },
          } as never;
        }
        if (method === "application.getState") {
          return { ok: true, value: applicationState(configured) } as never;
        }
        if (method === "command.execute") {
          const arguments_ =
            "arguments" in params ? params.arguments : undefined;
          if (!arguments_?.length) {
            return {
              ok: true,
              value: {
                status: "success",
                content: "",
                data: {},
                selection: {
                  prompt: "Select Provider",
                  options: [
                    { value: "deepseek", label: "DeepSeek", selected: true },
                    { value: "kimi", label: "Kimi", selected: false },
                  ],
                },
              },
            } as never;
          }
          if (!configured) {
            return {
              ok: true,
              value: {
                status: "success",
                content: "",
                data: {},
                secret_prompt: {
                  provider: "deepseek",
                  action: "add",
                  label: "DeepSeek API Key",
                  environment_variable: "DEEPSEEK_API_KEY",
                  help_url: "https://example.com",
                },
              },
            } as never;
          }
          return {
            ok: true,
            value: {
              status: "success",
              content: "",
              data: {},
              selection: {
                prompt: "Select DeepSeek Model",
                options: [
                  {
                    value: "deepseek/deepseek-chat",
                    label: "deepseek/deepseek-chat",
                    selected: true,
                  },
                ],
              },
            },
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
        width={80}
        providerSetupRequired
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
    await new Promise<void>((resolve) => setTimeout(resolve, 10));
    view.stdin.write(secret);
    await eventually(() => expect(view.lastFrame()).toContain("•"));
    expect(view.lastFrame()).not.toContain(secret);
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Select DeepSeek Model"),
    );
    expect(view.frames.join("\n")).not.toContain(secret);
  });
});

function applicationState(configured: boolean) {
  return {
    initialized: true,
    session_id: "session_1",
    workspace_key: "workspace_1",
    workspace: { display_path: "E:\\workspace" },
    workspace_trusted: true,
    current_thread_id: "thread_1",
    current_model: "deepseek/deepseek-chat",
    thinking_enabled: false,
    skill_mode: "auto",
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
        source: configured ? ("user_env_file" as const) : ("missing" as const),
        mutable: true,
      },
      kimi: {
        provider: "kimi" as const,
        environment_variable: "MOONSHOT_API_KEY",
        source: "missing" as const,
        mutable: true,
      },
    },
    memory_status: {},
    mcp_status: [],
    usage: {},
    configuration_diagnostics: [],
  };
}
