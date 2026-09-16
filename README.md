# AgentIDE

A standalone Sublime Text plugin that bridges Sublime to any IDE-protocol
agent CLI that speaks the same lock-file/WebSocket protocol Claude Code
uses — generic across agents, not tied to one vendor.

Separate from `GhostShell` (drives PTY-hosted agent sessions inside ST) and
`sublime-mcp` (a generic MCP tool server for the Sublime API). Kept apart on
purpose: embedding this in either of those repos would tie the bridge's
reload lifecycle to a much larger plugin's, which is exactly what killed the
predecessor feature (`ide_companion.py` in sublime-mcp, removed 2026-09-10).

**Confirmed working with:** Claude Code CLI.
**Not compatible, confirmed by direct investigation, not assumption:**
OpenAI Codex, JetBrains Junie, and GitHub Copilot CLI all tie their IDE
integration to a specific vendor's own IDE extension (VS Code or a
JetBrains IDE) that owns and spawns the CLI process directly — there's no
discoverable file-based protocol for an external tool to join, the way
Claude Code's is. Gemini CLI is the other public implementation of this
same protocol family (confirmed from its open-source `ide/contextUpdate`
push model) but hasn't been tested against AgentIDE directly.

## What it does

When an agent CLI connects (via `/ide` or `CLAUDE_CODE_SSE_PORT` auto-connect):

- **Diff review** — a proposed edit opens in a real Sublime buffer with the
  original content set as its diff baseline via ST's own built-in
  Incremental Diff engine (`View.set_reference_document`) — native gutter
  markers, Ctrl+./Ctrl+, hunk navigation, and Ctrl+K,Ctrl+Z per-hunk revert,
  no hand-built two-pane comparison. The buffer is locked read-only once
  loaded, so it can't be hand-edited while a request is pending. Accept/
  Reject is a phantom at the top of the buffer; closing the tab counts as
  Reject.
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

## Fixed bugs worth knowing about

- **A null `filePath` in `selection_changed` never actually clears the
  CLI's own file-context display**, even though AgentIDE sends it
  correctly — the CLI's client only updates on a truthy value. Fixed by
  sending the tab's own name as a non-null placeholder when there's no
  file (e.g. focus on a GhostShell terminal tab), instead of `null`. Not
  documented anywhere public; found by testing.
- **Switching tabs by clicking the header, with the cursor left where it
  was, updated nothing** — Sublime only fires `on_selection_modified`
  on an actual selection change, not on tab activation, so the CLI kept
  showing whichever tab's content you'd last actually clicked *into*.
  Fixed: every tab activation is now handled directly, not deferred to
  the selection listener.
- Removed a status-bar broadcast that leaked `"AgentIDE <state>:<port>"`
  onto every window's active view, including unrelated GhostShell tabs.
- **Accepting a diff for a CRLF file silently rewrote it as LF-only.**
  The old code read/wrote raw bytes with Python's `open()`; `View.substr()`
  is always LF-only internally, so nothing ever restored the original
  convention. Fixed by never writing raw bytes at all — every write now
  goes through a real, file-backed `View` and `View.save()`, so line
  endings/encoding come from Sublime's own detection. If the target
  wasn't already open, AgentIDE opens (or creates) a view just to save
  through it, then closes that view again and restores focus, so Accept
  never leaves a surprise tab behind or steals focus off the diff review.

## Caveat: don't edit AgentIDE's own source through its own diff review

If the file being diffed is one of AgentIDE's own source files, saving it
triggers Sublime's plugin auto-reload of that very module *while the
request is still in flight*. Reloading re-executes the module top to
bottom, resetting its module-level state (`_diffs`, `_phantom_sets`,
`_resolver`) out from under the running call. Symptoms: the Accept/Reject
phantom disappears but the tab doesn't close, and the CLI's own
permission prompt hangs forever because the response can no longer reach
it. Same hazard already known for `ai_terminal.py` in GhostShell and for
editing `sublime_mcp.py` live — a plugin can't safely mediate a live edit
to its own source. Edit AgentIDE's own files with a plain editor/Bash,
never by routing the change through its own live `/ide` connection.

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
