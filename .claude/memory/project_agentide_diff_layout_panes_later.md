---
name: agentide-diff-layout-panes-later
description: "Donald wants AgentIDE's diff in real layout panes (window.set_layout, merged cells) instead of the output panel; deferred until he asks"
metadata:
  node_type: memory
  type: project
  originSessionId: 1e07794b-efe6-4e5f-bbc3-7d3161a16a46
  modified: 2026-09-25T08:37:13.361Z
---

Donald wants AgentIDE's diff display (`lib/diff_view.py`) to use real layout-based panes via `window.set_layout` (Pain's merged-cell trick) rather than the bottom output panel. His settings currently have `"diff_display": "panel"`; a `"side_group"` mode already exists (plain two-group split via `context.side_group`).

**Why:** on 2026-09-25, after I explained how Pain does "impossible" splits, he said that's what he wanted for the diff. When offered two shapes (full-width bottom strip vs. old/new side-by-side in a right column) he answered "later."

**How to apply:** don't edit AgentIDE unprompted. When he raises it, offer the two shapes again (bottom strip with existing panes untouched above; or two synced views side by side) and reuse the save/restore-layout logic from `side_group`. Related: [[project-learn-lsp-copilot-later]].
