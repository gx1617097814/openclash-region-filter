#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function usage() {
  return [
    "Usage:",
    "  node scripts/router-ttyd-exec.mjs [--env FILE] [--debug] -- COMMAND",
    "  node scripts/router-ttyd-exec.mjs [--env FILE] --check-config",
    "",
    "Credentials are read from .router.env and are never printed.",
  ].join("\n");
}

function parseArgs(argv) {
  let envPath = ".router.env";
  let checkConfig = false;
  let debug = false;
  let index = 0;

  while (index < argv.length) {
    const arg = argv[index];
    if (arg === "--env") {
      if (!argv[index + 1]) throw new Error("--env requires a file path");
      envPath = argv[index + 1];
      index += 2;
      continue;
    }
    if (arg === "--check-config") {
      checkConfig = true;
      index += 1;
      continue;
    }
    if (arg === "--debug") {
      debug = true;
      index += 1;
      continue;
    }
    if (arg === "--") {
      index += 1;
      break;
    }
    if (arg === "--help" || arg === "-h") return { help: true };
    throw new Error(`unknown option: ${arg}`);
  }

  const command = argv.slice(index).join(" ");
  if (!checkConfig && !command) throw new Error("a remote command is required");
  if (/[\r\n\0]/.test(command)) throw new Error("the remote command must be a single line");
  return { envPath, checkConfig, debug, command };
}

function unquote(value, lineNumber) {
  if (!value) return "";
  const first = value[0];
  if (first !== '"' && first !== "'") return value.trim();
  if (value.length < 2 || value.at(-1) !== first) {
    throw new Error(`unterminated quoted value on line ${lineNumber}`);
  }
  const inner = value.slice(1, -1);
  if (first === "'") return inner;
  return inner.replace(/\\([\\"nrt])/g, (_, escaped) => ({
    "\\": "\\",
    '"': '"',
    n: "\n",
    r: "\r",
    t: "\t",
  })[escaped]);
}

function readEnv(envPath) {
  const resolved = path.resolve(envPath);
  const stat = fs.statSync(resolved);
  if (!stat.isFile()) throw new Error(`${envPath} is not a regular file`);
  if (process.platform !== "win32" && (stat.mode & 0o077) !== 0) {
    throw new Error(`${envPath} must not be readable or writable by group/others; run chmod 600 ${envPath}`);
  }

  const values = {};
  const content = fs.readFileSync(resolved, "utf8");
  for (const [offset, rawLine] of content.split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = /^(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match) throw new Error(`invalid environment entry on line ${offset + 1}`);
    values[match[1]] = unquote(match[2], offset + 1);
  }
  return { resolved, values };
}

function buildConfig(envPath) {
  const { resolved, values } = readEnv(envPath);
  const urlValue = values.ROUTER_TTYD_URL;
  const user = values.ROUTER_USER || "root";
  const password = values.ROUTER_PASSWORD;
  const timeoutSeconds = Number(values.ROUTER_COMMAND_TIMEOUT_SECONDS || "120");

  if (!urlValue) throw new Error("ROUTER_TTYD_URL is required");
  if (!user) throw new Error("ROUTER_USER is required");
  if (!password) throw new Error("ROUTER_PASSWORD is required");
  if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 5 || timeoutSeconds > 1800) {
    throw new Error("ROUTER_COMMAND_TIMEOUT_SECONDS must be between 5 and 1800");
  }

  const url = new URL(urlValue);
  if (url.protocol === "http:") url.protocol = "ws:";
  else if (url.protocol === "https:") url.protocol = "wss:";
  else if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new Error("ROUTER_TTYD_URL must use http, https, ws, or wss");
  }
  url.pathname = `${url.pathname.replace(/\/$/, "")}/ws`;
  url.search = "";
  url.hash = "";

  return {
    envPath: resolved,
    url: url.toString(),
    host: url.host,
    user,
    password,
    timeoutMs: timeoutSeconds * 1000,
  };
}

function stripTerminalControls(value) {
  return value
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\r/g, "");
}

function messageText(data) {
  let bytes;
  if (typeof data === "string") bytes = encoder.encode(data);
  else if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
  else if (ArrayBuffer.isView(data)) bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  else throw new Error("unsupported ttyd message type");
  if (bytes.length === 0) return "";
  return decoder.decode(bytes.subarray(1));
}

function sendInput(socket, text) {
  socket.send(encoder.encode(`0${text}`));
}

function shellQuote(value) {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function execute(config, command, debug = false) {
  return new Promise((resolve, reject) => {
    const marker = crypto.randomUUID().replaceAll("-", "");
    const readyMarker = `__OCFILTER_READY_${marker}__`;
    const doneMarker = `__OCFILTER_DONE_${marker}__`;
    const socket = new WebSocket(config.url, "tty");
    socket.binaryType = "arraybuffer";
    let phase = "connecting";
    let transcript = "";
    let output = "";
    let settled = false;

    const trace = (message) => {
      if (debug) process.stderr.write(`[ttyd] ${message}\n`);
    };

    const startCommand = () => {
      trace("terminal echo disabled; executing command");
      phase = "running";
      transcript = "";
      output = "";
      const quotedCommand = shellQuote(command);
      sendInput(socket, `sh -c ${quotedCommand}; __ocfilter_rc=$?; printf '\\n${doneMarker}%s\\n' "$__ocfilter_rc"; stty echo\r`);
    };

    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { socket.close(); } catch {}
      if (error) reject(error);
      else resolve(result);
    };

    const timer = setTimeout(() => {
      finish(new Error(`router command timed out after ${config.timeoutMs / 1000} seconds`));
    }, config.timeoutMs);

    socket.addEventListener("open", () => {
      trace("connected; waiting for login prompt");
      phase = "login";
      socket.send(JSON.stringify({ AuthToken: "", columns: 120, rows: 40 }));
    });
    socket.addEventListener("error", () => {
      finish(new Error(`unable to connect to ttyd at ${config.host}`));
    });
    socket.addEventListener("close", () => {
      if (!settled) finish(new Error("ttyd connection closed before the command completed"));
    });

    socket.addEventListener("message", (event) => {
      try {
        const chunk = stripTerminalControls(messageText(event.data));
        if (phase === "running") {
          output += chunk;
          const doneIndex = output.indexOf(doneMarker);
          if (doneIndex === -1) return;
          const afterMarker = output.slice(doneIndex + doneMarker.length);
          const codeMatch = /^(\d+)/.exec(afterMarker);
          if (!codeMatch) return;
          const commandOutput = output.slice(0, doneIndex).replace(/^\n+|\n+$/g, "");
          finish(null, { code: Number(codeMatch[1]), output: commandOutput });
          return;
        }

        transcript = `${transcript}${chunk}`.slice(-8192);
        if (phase === "ready" && /(?:^|\n)[^\n]{0,160}[#$]\s*$/.test(transcript)) {
          startCommand();
          return;
        }
        if (phase === "login" && /login:\s*$/i.test(transcript)) {
          trace("login prompt received; sending username");
          phase = "password";
          transcript = "";
          sendInput(socket, `${config.user}\r`);
          return;
        }
        if (phase === "password" && /password:\s*$/i.test(transcript)) {
          trace("password prompt received; sending password");
          phase = "shell";
          transcript = "";
          sendInput(socket, `${config.password}\r`);
          return;
        }
        if (phase === "shell") {
          if (/login incorrect|authentication failed/i.test(transcript) || /login:\s*$/i.test(transcript)) {
            finish(new Error("router login failed; check ROUTER_USER and ROUTER_PASSWORD"));
            return;
          }
          if (/(?:^|\n)[^\n]{0,160}[#$]\s*$/.test(transcript)) {
            trace("shell prompt received; disabling terminal echo");
            phase = "arming";
            transcript = "";
            sendInput(socket, `stty -echo; printf '\\n${readyMarker}\\n'\r`);
            return;
          }
        }
        if (phase === "arming" && transcript.includes(readyMarker)) {
          phase = "ready";
          transcript = transcript.slice(transcript.indexOf(readyMarker) + readyMarker.length);
          if (/(?:^|\n)[^\n]{0,160}[#$]\s*$/.test(transcript)) startCommand();
        }
      } catch (error) {
        finish(error);
      }
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return 0;
  }
  const config = buildConfig(args.envPath);
  if (args.checkConfig) {
    console.log(`Router ttyd configuration is valid (${config.host}, user ${config.user}).`);
    return 0;
  }
  const result = await execute(config, args.command, args.debug);
  if (result.output) process.stdout.write(`${result.output}\n`);
  return result.code;
}

try {
  process.exitCode = await main();
} catch (error) {
  console.error(`router-ttyd-exec: ${error.message}`);
  process.exitCode = 1;
}
