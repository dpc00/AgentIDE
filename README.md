# AgentIDE

A standalone Sublime Text plugin that bridges Sublime to any IDE-protocol
agent CLI (Claude Code, and others that speak the same lock-file/WebSocket
protocol) — generic across agents, not tied to one vendor.

Separate from `GhostShell` (drives PTY-hosted agent sessions inside ST) and
`sublime-mcp` (a generic MCP tool server for the Sublime API). Kept apart on
purpose: embedding this in either of those repos would tie the bridge's
reload lifecycle to a much larger plugin's, which is exactly what killed the
predecessor feature (`ide_companion.py` in sublime-mcp, removed 2026-09-10).

## Status: first slice

Right now this only proves the handshake: starts a WebSocket+MCP server,
writes the discovery lock file at `~/.claude/ide/<port>.lock`, and accepts
a connection from a real agent CLI's `/ide` command. No tools, no diff
review, no context sharing yet — those land once this is verified working
end-to-end.

## Testing this slice

1. Install (see below), restart Sublime.
2. Command Palette → **AgentIDE: Status** to see the port and lock path.
3. In a terminal: `claude`, then `/ide` — it should report connecting to
   Sublime Text.

## Install (dev)

```
New-Item -ItemType Junction -Path "$env:APPDATA\Sublime Text\Packages\AgentIDE" -Target "C:\Users\donal\projects\AgentIDE"
```
