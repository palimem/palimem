#!/usr/bin/env node
/**
 * Host smoke: exercise OpenClaw index.js bridge dispatch without the OpenClaw runtime.
 * Mirrors runBridge() argv construction from adapters/openclaw/index.js.
 */
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const ADAPTER_ROOT = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE_PATH = path.join(ADAPTER_ROOT, "bridge.py");

async function runBridge(action, workspaceRoot, pluginConfig, toolParams) {
  const command = process.env.MEMORY_SERVICE_PYTHON?.trim() || "python3";
  const args = [
    BRIDGE_PATH,
    action,
    "--workspace-root",
    workspaceRoot,
    "--payload",
    JSON.stringify(toolParams ?? {}),
  ];
  if (pluginConfig.data_dir) {
    args.push("--data-dir", String(pluginConfig.data_dir));
  }
  if (pluginConfig.namespace) {
    args.push("--namespace", String(pluginConfig.namespace));
  }
  if (pluginConfig.import_workspace_markdown) {
    args.push("--import-workspace-markdown");
  }
  const { stdout } = await execFileAsync(command, args, {
    cwd: workspaceRoot,
    encoding: "utf-8",
    maxBuffer: 2 * 1024 * 1024,
  });
  return JSON.parse(stdout || "{}");
}

async function main() {
  const workspace = await mkdtemp(path.join(tmpdir(), "openclaw-host-smoke-"));
  try {
    await mkdir(path.join(workspace, "memory", "project"), { recursive: true });
    await writeFile(
      path.join(workspace, "MEMORY.md"),
      "# Project memory\n\nHybrid host smoke fixture.\n",
      "utf-8",
    );
    await writeFile(
      path.join(workspace, "memory", "project", "decisions.md"),
      "# Decisions\n\nGoverned memory over markdown grep.\n",
      "utf-8",
    );
    const namespace = path.basename(workspace);
    const config = {
      data_dir: ".ai-memory/data",
      namespace,
      import_workspace_markdown: true,
    };
    const search = await runBridge("memory_search", workspace, config, {
      query: "governed memory",
      scope: "repository",
      namespace,
      limit: 5,
    });
    if (!Array.isArray(search.results) || search.results.length === 0) {
      throw new Error("memory_search returned no results after markdown import");
    }
    const hit = search.results[0];
    for (const key of ["path", "snippet", "score", "startLine", "endLine"]) {
      if (!(key in hit)) {
        throw new Error(`memory_search result missing ${key}`);
      }
    }
    const get = await runBridge("memory_get", workspace, config, {
      path: "memory/project/decisions.md",
    });
    if (!String(get.text || "").toLowerCase().includes("governed")) {
      throw new Error("memory_get path alias did not return imported content");
    }
    console.log("openclaw host bridge smoke: OK");
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
