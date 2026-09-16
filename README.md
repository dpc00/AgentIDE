# AgentIDE

A standalone Sublime Text plugin that bridges Sublime to any IDE-protocol
agent CLI (Claude Code, and others that speak the same lock-file/WebSocket
protocol) — generic across agents, not tied to one vendor.

Separate from `GhostShell` (drives PTY-hosted agent sessions inside ST) and
`sublime-mcp` (a generic MCP tool server for the Sublime API). Kept apart on
purpose: embedding this in either of those repos would tie the bridge's
reload lifecycle to a much larger plugin's, which is exactly what killed the
predecessor feature (`ide_companion.py` in sublime-mcp, removed 2026-09-10).

## What it does

When an agent CLI connects (via `/ide` or `CLAUDE_CODE_SSE_PORT` auto-connect):

- **Diff review** — a proposed edit opens in a real Sublime buffer with the
  original content set as its diff baseline via ST's own built-in
  Incremental Diff engine (`View.set_reference_document`) — native gutter
  markers, Ctrl+./Ctrl+, hunk navigation, and Ctrl+K,Ctrl+Z per-hunk revert,
  no hand-built two-pane comparison. Accept/Reject is a phantom at the top
  of the buffer; closing the tab counts as Reject.
- **Context tools** — `openFile`, `getCurrentSelection`, `getLatestSelection`,
  `getOpenEditors`, `getWorkspaceFolders`, `checkDocumentDirty`,
  `saveDocument`, `getDiagnostics` (always empty for now — no linter/LSP
  source wired in yet).
- **Selection sharing** — debounced `selection_changed` notifications and an
  `AgentIDE: Send Selection as @-mention` command.
- `executeCode` is registered but explicitly unsupported (matches the
  reference protocol's own behavior for editors with no code-execution
  surface, rather than erroring as an unknown tool).

## Settings (`AgentIDE.sublime-settings`)

- `debug` — print protocol traffic to the Sublime console.
- `auto_start` — start the server when Sublime starts.
- `port` — fixed port (falls back to random if busy), so a machine-wide
  `CLAUDE_CODE_SSE_PORT` env var can point at one stable port.
- `ide_name` — name shown in the connecting CLI's IDE picker and lock file.
- `open_in_side_group` — open files/diffs in a right-hand pane instead of
  replacing whatever you were looking at.
- `selection_debounce_ms` — debounce for `selection_changed` notifications.

## Testing

1. Install (see below), restart Sublime Text — required after any AgentIDE
   code change; its own plugin auto-reload is unreliable for a multi-file
   package (submodule edits don't reliably get picked up), so restart
   rather than trust a live reload.
2. Command Palette → **AgentIDE: Status** to see the port and lock path.
3. In a terminal: `claude`, then `/ide` — it should report connecting to
   Sublime Text.
4. Ask Claude to edit a file — the diff should open in Sublime instead of
   the terminal.

## Install (dev)

```
New-Item -ItemType Junction -Path "$env:APPDATA\Sublime Text\Packages\AgentIDE" -Target "C:\Users\donal\projects\AgentIDE"
```
