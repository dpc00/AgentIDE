#!/usr/bin/env bun
/**
 * agentide-bridge — lets omp use AgentIDE as its IDE.
 *
 * omp speaks MCP over stdio/HTTP/SSE; AgentIDE speaks the Claude Code `/ide` protocol over a
 * WebSocket on a random loopback port, advertised in ~/.claude/ide/<port>.lock. Nothing in omp
 * discovers or speaks that, which is why AgentIDE's notifications (selection_changed, at_mentioned,
 * and now dialog_changed) never reach an omp session.
 *
 * This bridges the two: a stdio MCP server that
 *   - finds the live lock, connects with the x-claude-code-ide-authorization header,
 *   - proxies AgentIDE's tools through as its own (tools/list, tools/call),
 *   - forwards every server notification straight to stdout, so omp's MCP layer sees
 *     `notifications/*` and custom methods such as dialog_changed.
 *
 * Report only: it never clicks, answers, or writes to the editor by itself.
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const AUTH_HEADER = "x-claude-code-ide-authorization";
const LOCK_DIR = process.env.AGENTIDE_LOCK_DIR || join(homedir(), ".claude", "ide");
const LOG = (...a) => { if (process.env.AGENTIDE_BRIDGE_DEBUG) console.error("[bridge]", ...a); };

function lockCandidates() {
  if (!existsSync(LOCK_DIR)) return [];
  const out = [];
  for (const name of readdirSync(LOCK_DIR)) {
    if (!name.endsWith(".lock")) continue;
    const path = join(LOCK_DIR, name);
    try {
      const info = JSON.parse(readFileSync(path, "utf8"));
      out.push({ port: Number(name.replace(/\.lock$/, "")), path, info });
    } catch { /* skip an unreadable lock */ }
  }
  return out.sort((a, b) => (b.info.pid || 0) - (a.info.pid || 0));
}

function findLiveLock() {
  for (const c of lockCandidates()) {
    if (!c.info?.authToken) continue;
    try { process.kill(c.info.pid, 0); return c; } catch { /* owner gone */ }
  }
  return null;
}

// ── websocket ────────────────────────────────────────────────────────────────
class IdeSocket {
  constructor(url, token) {
    this.url = url;
    this.token = token;
    this.ws = null;
    this.pending = new Map();
    this.nextId = 1;
    this.onNotification = () => {};
    this.onClose = () => {};
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url, { headers: { [AUTH_HEADER]: this.token } });
      this.ws = ws;
      const timer = setTimeout(() => { try { ws.close(); } catch {} reject(new Error("websocket connect timeout")); }, 5000);
      ws.addEventListener("open", () => { clearTimeout(timer); resolve(); });
      ws.addEventListener("error", (e) => { clearTimeout(timer); reject(new Error("websocket error: " + (e?.message || "unknown"))); });
      ws.addEventListener("close", () => { clearTimeout(timer); this.onClose(); });
      ws.addEventListener("message", (ev) => {
        let msg;
        try { msg = JSON.parse(typeof ev.data === "string" ? ev.data : String(ev.data)); } catch { return; }
        if (msg.id !== undefined && this.pending.has(msg.id)) {
          const { resolve: res, reject: rej } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? rej(new Error(msg.error.message || JSON.stringify(msg.error))) : res(msg.result);
          return;
        }
        if (msg.method) this.onNotification(msg);
      });
    });
  }

  request(method, params) {
    const id = this.nextId++;
    const frame = JSON.stringify({ jsonrpc: "2.0", id, method, params: params ?? {} });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error(`${method}: no response in 15s`)); }
      }, 15000);
      try { this.ws.send(frame); } catch (e) { this.pending.delete(id); reject(e); }
    });
  }

  close() { try { this.ws?.close(); } catch {} }
}

// ── stdio MCP ────────────────────────────────────────────────────────────────
const send = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
const reply = (id, result) => send({ jsonrpc: "2.0", id, result });
const fail = (id, message) => send({ jsonrpc: "2.0", id, error: { code: -32603, message } });
const notify = (method, params) => send({ jsonrpc: "2.0", method, params });

let ide = null;
let tools = [];

const TOOL_FALLBACK = [
  { name: "getCurrentSelection", description: "The current selection in the IDE.", inputSchema: { type: "object", properties: {} } },
  { name: "getOpenEditors", description: "Tabs open in the IDE.", inputSchema: { type: "object", properties: {} } },
  { name: "getWorkspaceFolders", description: "Workspace folders open in the IDE.", inputSchema: { type: "object", properties: {} } },
];

async function connect() {
  const lock = findLiveLock();
  if (!lock) throw new Error(`no live AgentIDE lock in ${LOCK_DIR} (start AgentIDE in Sublime Text)`);
  ide = new IdeSocket(`ws://127.0.0.1:${lock.port}`, lock.info.authToken);
  ide.onNotification = (msg) => notify(msg.method, msg.params);   // selection_changed, at_mentioned, dialog_changed
  ide.onClose = () => { notify("notifications/message", { level: "info", data: "AgentIDE connection closed" }); };
  await ide.connect();
  LOG("connected to", lock.port, "pid", lock.info.pid);
  try {
    const listed = await ide.request("tools/list", {});
    tools = Array.isArray(listed?.tools) ? listed.tools : [];
  } catch (e) {
    LOG("tools/list failed:", String(e));
    tools = [];
  }
  if (!tools.length) tools = TOOL_FALLBACK;
  notify("notifications/message", {
    level: "info",
    data: `AgentIDE connected on ${lock.port}: ${tools.length} tools, notifications forwarded (${lock.info.ideName || "IDE"})`,
  });
}

async function handle(msg) {
  const { id, method, params } = msg;
  if (method === "initialize") {
    try { await connect(); } catch (e) { notify("notifications/message", { level: "error", data: String(e.message || e) }); }
    reply(id, {
      protocolVersion: "2025-11-25",
      capabilities: { tools: {}, logging: {} },
      serverInfo: { name: "agentide-bridge", version: "0.1.0" },
    });
    return;
  }
  if (method === "notifications/initialized" || method?.startsWith("notifications/")) return;
  if (method === "ping") { reply(id, {}); return; }
  if (method === "tools/list") { reply(id, { tools }); return; }
  if (method === "tools/call") {
    const name = params?.name;
    try {
      if (!ide) await connect();
      const result = await ide.request("tools/call", { name, arguments: params?.arguments ?? {} });
      reply(id, result ?? { content: [] });
    } catch (e) { fail(id, `${name}: ${e.message || e}`); }
    return;
  }
  if (id !== undefined) fail(id, `unsupported method ${method}`);
}

// ── line-delimited JSON on stdin ─────────────────────────────────────────────
let buffer = "";
let chain = Promise.resolve();
process.stdin.on("data", (chunk) => {
  buffer += chunk.toString();
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    // Serialise: a tools/list that overtakes initialize would list an empty set (seen 2026-11-08).
    chain = chain.then(() => handle(msg)).catch((e) => {
      if (msg.id !== undefined) fail(msg.id, String(e));
    });
  }
});
process.stdin.on("end", () => { chain.finally(() => { ide?.close(); scan?.kill(); process.exit(0); }); });

// ── desktop situation ────────────────────────────────────────────────────────
// The scanner observes the whole desktop (every window, the foreground, idle time) and needs nothing
// from Sublime: a dialog in any app is the same fact. Its lines ride the same channel as the IDE's.
let scan = null;
try {
  scan = Bun.spawn(["python", join(import.meta.dir, "scan.py"), "--interval", "0.5"], {
    stdout: "pipe", stderr: "inherit",
  });
  (async () => {
    const decoder = new TextDecoder();
    let buf = "";
    for await (const chunk of scan.stdout) {
      buf += decoder.decode(chunk, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        if (!line.trim()) continue;
        try { notify("situation_changed", JSON.parse(line)); } catch { /* ignore a partial line */ }
      }
    }
  })().catch(() => {});
} catch (e) {
  notify("notifications/message", { level: "warning", data: "situation scanner not started: " + String(e) });
}
