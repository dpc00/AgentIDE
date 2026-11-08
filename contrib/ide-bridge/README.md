# agentide-bridge

Gives an MCP agent (omp, Claude Code, anything speaking MCP over stdio) the two things it otherwise
cannot get:

1. **The IDE itself**, through AgentIDE's `/ide` server.
2. **The desktop situation** — every window, the foreground, idle time, and dialogs from any process.

## Why a bridge exists

`omp` speaks MCP over stdio/HTTP/SSE. AgentIDE speaks the Claude Code IDE protocol: a WebSocket on a
random loopback port, authenticated with `x-claude-code-ide-authorization`, advertised in
`~/.claude/ide/<port>.lock`. Those do not compose, so before this bridge an omp session had no IDE
attached at all and AgentIDE's notifications (`selection_changed`, `at_mentioned`, `dialog_changed`)
went nowhere.

## Run

```jsonc
// .omp/mcp.json
"agentide": {
  "command": "C:\\Users\\donal\\.bun\\bin\\bun.EXE",
  "args": ["run", "C:\\Users\\donal\\projects\\AgentIDE\\contrib\\ide-bridge\\bridge.js"],
  "type": "stdio"
}
```

Takes effect when the session starts (or on `/mcp reload`).

## What it emits

| method | when |
|---|---|
| `tools/list`, `tools/call` | proxied to AgentIDE: `openFile`, `getCurrentSelection`, `openDiff`, `getDiagnostics`, … 12 tools |
| `selection_changed`, `at_mentioned`, `dialog_changed` | forwarded verbatim from AgentIDE |
| `situation_changed` | a line from `scan.py` beside this README: foreground, `idle_ms`, dialogs with their text and buttons |

`situation_changed` example:

```json
{"foreground":{"exe":"python.exe","title":"scanner probe dialog"},"idle_ms":0,
 "dialogs":[{"class":"#32770","buttons":["Yes","No","Cancel"],"messages":["Three buttons, one of them Cancel."],
 "status":"Dialog is up: 'scanner probe dialog'. It says: 'Three buttons, one of them Cancel.'. 3 buttons: Yes, No, Cancel."}]}
```

## Proven 2026-11-08 (build 4215)

- handshake answered; `tools/list` returned all 12 AgentIDE tools;
- `dialog_changed` arrived while Sublime's main thread was blocked by that very dialog;
- `situation_changed` reported a message box raised by a scratch `python.exe` — nothing to do with
  Sublime — and reported it removed when it closed;
- a real `selection_changed` from the editor came through on the same stream.

## Rules

- Report only. The bridge never clicks, focuses, answers a dialog, or writes to the editor.
- Nothing here depends on Sublime: `scan.py` reads the desktop, so a dialog in any application is
  reported.

`probe_bridge.py` keeps a session open and prints every line — the way to see the stream without an
agent in front of it.
