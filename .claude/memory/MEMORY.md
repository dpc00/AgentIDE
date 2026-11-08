STATUS 2026-10-31: current state of everything is in sublime-package-audit\notes\project_session_baton_2026_10_31_state_of_play.md (relative to projects\); the notes below are dated records, and open or parked lists in them are history. Entries marked HISTORY were superseded that day.

# Memory index (AgentIDE notes)

- [AgentIDE diff layout panes](project_agentide_diff_layout_panes_later.md) — Donald wants set_layout panes for AgentIDE diff instead of output…

- [agentide-debug-trap-2026-10-10](project_agentide_debug_trap_2026_10_10.md) — AgentIDE debug log ON to catch the message behind the stray line selection / frozen Claude tab; turn it off after catching
- [dialog-picker-notification-wanted-2026-10-23](project_dialog_picker_notification_wanted_2026_10_23.md) - his request: notify the agent when a native dialog or quick panel appears, like selection_changed. **Dialogs are built** (`lib/dialogwatch.py` + `dialog_changed`, report only, never clicks); **pickers are not**. Also records `contrib/ide-bridge/`, the MCP client that lets a non-Claude agent consume these notifications.
- [getdiagnostics-lsp-2026-10-15](project_getdiagnostics_lsp_2026_10_15.md) - getDiagnostics now reads the LSP package diagnostics (3c3352a, not pushed); how it was proved; /ide AgentIDE reconnect after every reload
