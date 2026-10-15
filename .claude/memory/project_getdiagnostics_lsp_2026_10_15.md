---
name: getdiagnostics-lsp-2026-10-15
description: getDiagnostics now reads the LSP package's diagnostics (commit 3c3352a, not pushed); how it works, how it was proved, and the /ide reconnect after every reload
metadata:
  type: project
---

2026-10-15, on Donald's "go, wire up LSP diagnostics": `lib/diagnostics.py` + `_tool_get_diagnostics` in agent_ide.py. Reads each LSP session's `diagnostics` store (`sys.modules["LSP.plugin.core.registry"].windows.lookup(window).get_sessions()`, `session.diagnostics.get_diagnostics()`), on Sublime's async thread via `set_timeout_async` (LSP changes that state there). Never imports LSP; no LSP loaded gives `{"content": []}` as before. Output is the VS Code extension's shape: `[{uri, diagnostics: [{message, severity word, range, source, code}]}]`. Written against LSP 2.15.0 internals (`DiagnosticsStorage.get_diagnostics`, `WindowRegistry.lookup`, `WindowManager.get_sessions`), which are not a public API: if LSP changes them the tool will raise, check there first.

**Proved:** inside Sublime (handler called from a worker thread) and through Claude Code's real `mcp__ide__getDiagnostics` after `/ide`: a test file with a wrong assignment, an undefined name and a bad argument returned the 3 Pyright errors with rule codes plus a hint; the `uri` filter matched exact and differently spelled URIs (lowercase drive, %3A); an unknown file gave nothing. With no `uri` it lists every open file that has diagnostics, hints included.

**Reload gotcha:** each AgentIDE reload picks a new port and Claude Code's IDE link drops; Donald reconnects with `/ide AgentIDE` typed in the Claude tab (the docs list `/ide` with no arguments; he ran it with the IDE's name and it answered "Connected to AgentIDE."). Do not send it into the tab myself unless he says so.

Not pushed: the repo has no rule saying when to push.
