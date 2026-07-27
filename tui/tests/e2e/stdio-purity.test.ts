import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { startCore } from "../../src/core/process.js";
import { PRODUCT_VERSION } from "../../src/version.js";
import { createCoreWrapper } from "../fixtures/core-wrapper.js";

const temporary: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporary
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

describe("real Python stdio channel ownership", () => {
  it.each([
    "deepseek",
    "kimi",
  ])("keeps %s protocol on Core stdout and logs on Core stderr", async (provider) => {
    const root = await mkdtemp(join(tmpdir(), "awesome-stdio-"));
    temporary.push(root);
    const home = join(root, "home");
    const workspace = join(root, "workspace");
    const wrappers = join(root, "bin");
    await mkdir(workspace);
    await writeFile(join(workspace, "sample.txt"), "fixture source", "utf8");
    const wrapper = await createCoreWrapper({
      directory: wrappers,
      repository: resolve(".."),
    });
    const session = await startCore({
      executable:
        process.platform === "win32"
          ? join(wrappers, "awesome-core.cmd")
          : "awesome-core",
      cwd: workspace,
      env: {
        ...wrapper.environment,
        AWESOME_HOME: home,
        AWESOME_WORKSPACE: workspace,
        AWESOME_FAKE_PROVIDER: provider,
        AWESOME_FAKE_SCENARIO: "stderr-log",
        PYTHONUNBUFFERED: "1",
      },
    });

    try {
      const initialized = await session.rpc.request("initialize", {
        protocol_version: 4,
        client_name: "awesome",
        client_version: PRODUCT_VERSION,
      });
      expect(initialized.ok).toBe(true);
      if (!initialized.ok) throw new Error(initialized.error.message);
      expect(initialized.value.status).toBe("trust_required");
      expect(initialized.value.interaction_id).toBeTruthy();
      const trusted = await session.rpc.request("interaction.respond", {
        interaction_id: initialized.value.interaction_id ?? "",
        decision: "trust",
      });
      expect(trusted.ok).toBe(true);
      const shutdown = await session.rpc.request("shutdown", {});
      expect(shutdown).toEqual({ ok: true, value: { stopped: true } });
      await expect(session.exit).resolves.toMatchObject({ code: 0 });

      const stderr = new TextDecoder().decode(session.stderrTail());
      expect(stderr).toContain("fixture core log");
      expect(stderr).not.toContain("fake-key");
      expect(stderr).not.toContain('"jsonrpc"');
    } catch (error) {
      throw new Error(
        `${error instanceof Error ? error.message : String(error)}\n${new TextDecoder().decode(session.stderrTail())}`,
      );
    } finally {
      await session.terminate().catch(() => undefined);
      await session.exit;
    }
  }, 30_000);
});
