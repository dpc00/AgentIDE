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

## 2026-11-08: dialogs implemented (pickers not)

`lib/dialogwatch.py` + a `dialog_changed` notification next to `selection_changed`. Report only —
it never clicks a button; the agent decides, which is the point of the notification.

- Signal: Win32 `EVENT_OBJECT_SHOW` on a top-level, visible `#32770` window of the editor's process
  tree. Measured on 4215: the window is created **invisible** ~314 ms before it is shown, with its
  buttons still being built, so SHOW (not CREATE) is the honest trigger; reporting lands in under 1 ms.
- Payload: `{state: open|closed, hwnd, pid, title, buttons, messages, status}` where `status` is the
  line the agent acts on: `Dialog is up: 'Open'. It says: 'File name: …'. 3 buttons: Open, Cancel, Help.`
- Proof: standalone harness against a 4215 rig — open reported at t=9.44, the agent sent Cancel,
  close reported at t=31.77. Same code path the plugin uses; plugin wiring takes effect on reload.
- Dialogs belong to `sublime_text.exe` **and** `plugin_host-x.y.exe`, so the watcher resolves the
  topmost ancestor and covers the tree.

**Still open, unchanged by this:** the note's original gate — whether the CLI *acts* on a notification
whose method it does not know. `dialog_changed` is new, so that has never been shown. What is shown is
the editor half: the information exists, on time, with the buttons named.

**Still not built:** pickers. AgentIDE sends nothing when a quick panel opens; sublime-mcp reads one
with `get_quick_panel`, and Sublime itself has no event for it (a package-opened panel could be hooked
at `Window.show_quick_panel`).
