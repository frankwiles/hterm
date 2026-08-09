import { isAbsolute } from "node:path";
import { spawn } from "node:child_process";

export interface Project {
  name: string;
  label: string;
  description: string | null;
  aliases: string[];
  keywords: string[];
  cwd: string;
}

export interface HtermErrorBody {
  code: string;
  message: string;
  project?: string;
  step?: string;
  exit_code?: number;
  [key: string]: unknown;
}

interface SuccessEnvelope {
  ok: true;
  action: string;
  [key: string]: unknown;
}

interface FailureEnvelope {
  ok: false;
  error: HtermErrorBody;
}

type Envelope = SuccessEnvelope | FailureEnvelope;

export interface OpenResult extends SuccessEnvelope {
  action: "open";
  project: string;
  workspace_id?: string;
  warnings: HtermErrorBody[];
}

export class HtermFailure extends Error {
  constructor(
    readonly error: HtermErrorBody,
    readonly status: number | null = null,
  ) {
    super(error.message);
    this.name = "HtermFailure";
  }
}

function executable(path: string): string {
  const value = path.trim();
  if (!isAbsolute(value)) {
    throw new HtermFailure({
      code: "invalid_executable_path",
      message: "Set hterm Executable to an absolute path in extension preferences",
    });
  }
  return value;
}

function run(
  path: string,
  args: string[],
  timeoutMs = 120_000,
): Promise<{ stdout: string; stderr: string; status: number }> {
  return new Promise((resolve, reject) => {
    const child = spawn(executable(path), args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => (stdout += chunk));
    child.stderr.on("data", (chunk: string) => (stderr += chunk));

    const timer = setTimeout(() => {
      settled = true;
      child.kill("SIGTERM");
      reject(
        new HtermFailure({
          code: "command_timeout",
          message: `hterm did not finish within ${timeoutMs / 1000} seconds`,
        }),
      );
    }, timeoutMs);

    child.on("error", (cause: NodeJS.ErrnoException) => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      const message =
        cause.code === "ENOENT" ? `hterm executable not found: ${path}` : `Unable to run hterm: ${cause.message}`;
      reject(new HtermFailure({ code: "executable_failed", message }));
    });
    child.on("close", (status) => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      resolve({ stdout, stderr, status: status ?? 1 });
    });
  });
}

function protocolFailure(message: string, status: number, stderr: string): HtermFailure {
  const diagnostic = stderr.trim();
  return new HtermFailure(
    {
      code: "invalid_json_response",
      message: diagnostic ? `${message}: ${diagnostic}` : message,
    },
    status,
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function call(path: string, args: string[], expectedAction: string): Promise<SuccessEnvelope> {
  const result = await run(path, args);
  let value: unknown;
  try {
    value = JSON.parse(result.stdout);
  } catch {
    throw protocolFailure("hterm returned malformed JSON", result.status, result.stderr);
  }

  if (!isObject(value) || typeof value.ok !== "boolean") {
    throw protocolFailure("hterm returned an invalid result envelope", result.status, result.stderr);
  }
  const envelope = value as unknown as Envelope;
  if (!envelope.ok) {
    if (
      !isObject(envelope.error) ||
      typeof envelope.error.code !== "string" ||
      typeof envelope.error.message !== "string"
    ) {
      throw protocolFailure("hterm returned an invalid error envelope", result.status, result.stderr);
    }
    throw new HtermFailure(envelope.error, result.status);
  }
  if (result.status !== 0) {
    throw protocolFailure(
      `hterm exited with status ${result.status} after reporting success`,
      result.status,
      result.stderr,
    );
  }
  if (envelope.action !== expectedAction) {
    throw protocolFailure(
      `Expected hterm action ${expectedAction}, received ${String(envelope.action)}`,
      result.status,
      result.stderr,
    );
  }
  return envelope;
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isProject(value: unknown): value is Project {
  return (
    isObject(value) &&
    typeof value.name === "string" &&
    typeof value.label === "string" &&
    (typeof value.description === "string" || value.description === null) &&
    stringArray(value.aliases) &&
    stringArray(value.keywords) &&
    typeof value.cwd === "string"
  );
}

export async function listProjects(path: string): Promise<Project[]> {
  const result = await call(path, ["list", "--json"], "list");
  if (!Array.isArray(result.projects) || !result.projects.every(isProject)) {
    throw new HtermFailure({ code: "invalid_json_response", message: "hterm list returned invalid project metadata" });
  }
  return result.projects;
}

export async function openProject(path: string, project: string, focus: boolean): Promise<OpenResult> {
  const args = ["open", project, "--json"];
  if (!focus) args.push("--no-focus");
  const result = await call(path, args, "open");
  if (typeof result.project !== "string" || !Array.isArray(result.warnings)) {
    throw new HtermFailure({ code: "invalid_json_response", message: "hterm open returned an invalid result" });
  }
  return result as OpenResult;
}

export async function checkConfig(path: string): Promise<string> {
  const result = await call(path, ["check", "--json"], "check");
  if (typeof result.path !== "string") {
    throw new HtermFailure({ code: "invalid_json_response", message: "hterm check did not return a config path" });
  }
  return result.path;
}

export async function configPath(path: string): Promise<string> {
  const result = await call(path, ["config", "path", "--json"], "config-path");
  if (typeof result.path !== "string") {
    throw new HtermFailure({ code: "invalid_json_response", message: "hterm did not return a config path" });
  }
  return result.path;
}
