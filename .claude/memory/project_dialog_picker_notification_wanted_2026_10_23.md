---
name: dialog-picker-notification-wanted-2026-10-23
description: Donald wants AgentIDE to send a "there is a dialog/picker" notification, like its selection_changed "where is the user" one; not built yet
metadata:
  type: project
---

Donald, 2026-10-23, mid package audit: "if a dialog or picker appears AgentIDE should be notifying. Like the 'where is the user' notification. 'There is a dialog/picker' notification."

**Why:** the audit repeatedly stalls or fires keys into the wrong place because a native dialog (Save Changes?, Error loading colour scheme, Download Python) or a quick panel is open and the agent does not know.

**State of the code (checked 2026-10-23):** `agent_ide.py` `_notify(method, params)` broadcasts to the connected CLI; today only `selection_changed` and `at_mentioned` use it (the "where is the user" signal). Nothing watches for dialogs or pickers.

**Design points found, not decided:**
- Sublime's plugin API has no event for a quick panel opening or a native dialog; sublime-mcp finds dialogs from outside the main thread (`list_native_windows`, Win32) and pickers with `get_quick_panel`. AgentIDE would need a small watcher doing the same (Win32 EnumWindows for `#32770` windows owned by the Sublime process, plus a quick-panel check) and a notification only on change (opened / closed).
- Claude Code's IDE protocol has no standard method for this, so an unknown notification may be ignored by the CLI. Check how the CLI treats an unknown method before building; if ignored, the signal has to reach the agent another way (for example a field the CLI does read, or a tool result in sublime-mcp).

**How to apply:** NO CODING (Donald, 2026-10-23: "Dont do any coding. You have not established the Claude is going to respond to it."). First establish, by evidence (Claude Code docs or issues, or a test he agrees to), that Claude Code acts on a notification like this from an IDE bridge. Until that is shown, write nothing. Today the agent finds dialogs and pickers by polling (`list_native_windows`, `get_quick_panel`); that is the problem the feature would remove.
