import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const execFileAsync = promisify(execFile);
const ADAPTER_ROOT = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE_PATH = path.join(ADAPTER_ROOT, "bridge.py");

const MEMORY_SEARCH_SCHEMA = {
  type: "object",
  properties: {
    query: { type: "string" },
    scope: { type: "string", enum: ["user", "session", "repository"] },
    namespace: { type: "string" },
    limit: { type: "integer", minimum: 1 },
    maxResults: { type: "integer", minimum: 1 },
    include_episodes: { type: "boolean" },
  },
  required: ["query"],
  additionalProperties: false,
};

const MEMORY_GET_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string" },
    from: { type: "integer", minimum: 1 },
    lines: { type: "integer", minimum: 1 },
    scope: { type: "string", enum: ["user", "session", "repository"] },
    namespace: { type: "string" },
    topic: { type: "string" },
    field: { type: "string" },
    memory_type: { type: "string" },
  },
  additionalProperties: false,
};

function resolveRuntimeConfig(ctx) {
  return ctx?.getRuntimeConfig?.() ?? ctx?.runtimeConfig ?? ctx?.config ?? {};
}

function resolvePluginConfig(ctx) {
  const runtimeConfig = resolveRuntimeConfig(ctx);
  return (
    runtimeConfig?.plugins?.entries?.["ai-memory"] ??
    runtimeConfig?.plugins?.entries?.aiMemory ??
    runtimeConfig?.aiMemory ??
    {}
  );
}

function resolveWorkspaceRoot(ctx, pluginConfig) {
  const runtimeConfig = resolveRuntimeConfig(ctx);
  return path.resolve(
    pluginConfig.workspaceRoot ??
      runtimeConfig?.workspaceRoot ??
      runtimeConfig?.workspace?.root ??
      process.env.OPENCLAW_WORKSPACE ??
      process.cwd()
  );
}

function resolvePythonCommand() {
  const configured = process.env.MEMORY_SERVICE_PYTHON;
  if (configured && configured.trim()) {
    return { command: configured.trim(), prefixArgs: [] };
  }
  if (process.platform === "win32") {
    return { command: "py", prefixArgs: ["-3"] };
  }
  return { command: "python3", prefixArgs: [] };
}

async function runBridge(action, ctx, toolParams) {
  const pluginConfig = resolvePluginConfig(ctx);
  const workspaceRoot = resolveWorkspaceRoot(ctx, pluginConfig);
  const { command, prefixArgs } = resolvePythonCommand();
  const args = [
    ...prefixArgs,
    BRIDGE_PATH,
    action,
    "--workspace-root",
    workspaceRoot,
    "--payload",
    JSON.stringify(toolParams ?? {}),
  ];
  const dataDir = pluginConfig.data_dir ?? pluginConfig.dataDir;
  if (dataDir) {
    args.push("--data-dir", String(dataDir));
  }
  if (pluginConfig.namespace) {
    args.push("--namespace", String(pluginConfig.namespace));
  }
  if (pluginConfig.import_workspace_markdown ?? pluginConfig.importWorkspaceMarkdown) {
    args.push("--import-workspace-markdown");
  }
  if (ctx?.sessionKey) {
    args.push("--session-key", String(ctx.sessionKey));
  }
  try {
    const { stdout } = await execFileAsync(command, args, {
      cwd: workspaceRoot,
      env: process.env,
      encoding: "utf-8",
      maxBuffer: 2 * 1024 * 1024,
    });
    return JSON.parse(stdout || "{}");
  } catch (error) {
    const message = error?.stderr?.trim?.() || error?.message || String(error);
    if (action === "memory_get") {
      return {
        path: String(toolParams?.path ?? ""),
        text: "",
        disabled: true,
        code: "integration_failed",
        error: `integration_failed: ${message}`,
      };
    }
    return {
      results: [],
      disabled: true,
      code: "integration_failed",
      error: `integration_failed: ${message}`,
    };
  }
}

function createMemorySearchTool(ctx) {
  return {
    label: "Memory Search",
    name: "memory_search",
    description:
      "Search governed memory-service records through the active OpenClaw memory slot. Returns OpenClaw-compatible snippets with path, score, and line metadata.",
    parameters: MEMORY_SEARCH_SCHEMA,
    execute: async (_toolCallId, params) => await runBridge("memory_search", ctx, params),
  };
}

function createMemoryGetTool(ctx) {
  return {
    label: "Memory Get",
    name: "memory_get",
    description:
      "Read a governed memory record by subject key or by path alias such as memory/<topic>/<field>.md inside the workspace sandbox.",
    parameters: MEMORY_GET_SCHEMA,
    execute: async (_toolCallId, params) => await runBridge("memory_get", ctx, params),
  };
}

export default definePluginEntry({
  id: "ai-memory",
  name: "AI Memory",
  description: "OpenClaw memory plugin backed by memory-service",
  kind: "memory",
  register(api) {
    api.registerTool((ctx) => createMemorySearchTool(ctx), {
      names: ["memory_search"],
    });
    api.registerTool((ctx) => createMemoryGetTool(ctx), {
      names: ["memory_get"],
    });
  },
});
