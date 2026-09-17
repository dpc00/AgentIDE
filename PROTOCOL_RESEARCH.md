# Claude Code IDE protocol — primary-source research

This documents what was actually verified against real, authoritative
sources (not guesses, not third-party blog posts) about the protocol
AgentIDE implements. Written up 2026-09-16 after a long research session
so this doesn't need to be redone.

## Primary sources used (on this machine)

- **VS Code extension** (real, current, matches this machine's CLI
  version 2.1.273):
  `C:\Users\donal\.antigravity-ide\extensions\anthropic.claude-code-2.1.273-win32-x64\extension.js`
  (Antigravity IDE bundles the real Anthropic VS Code extension unmodified.)
- **JetBrains/PyCharm plugin** (v0.1.14-beta):
  `C:\Users\donal\AppData\Roaming\JetBrains\PyCharm2026.2\plugins\claude-code-jetbrains-plugin\lib\claude-code-jetbrains-plugin-0.1.14-beta.jar`
  — a Kotlin/JVM jar; tool names extracted from compiled bytecode via
  `javap -p -c -cp . <class>` and grepping for `// String <name>` comments
  next to `ldc` instructions (the actual string constant being loaded,
  e.g. the tool-name argument passed to `server.addTool(name=...)`).
- Sibling reference implementations already in `~/tools/`:
  `sublime-claude-code` (has a real, smoke-tested diff flow —
  `scripts/smoke_diff.py`), `sublime-claude`, `sublime-gemini`,
  `claudesublime`.

## The real tool list (VS Code / Claude Code CLI's actual protocol)

Extracted via `grep -o '\.tool("[a-zA-Z_]*"' extension.js`:

```
checkDocumentDirty, closeAllDiffTabs, close_tab, executeCode,
getCurrentSelection, getDiagnostics, getLatestSelection, getOpenEditors,
getWorkspaceFolders, openDiff, openFile, saveDocument
```

12 tools. **AgentIDE implements all 12, with matching schemas.** Verified
by direct comparison, not assumption.

## The JetBrains/PyCharm plugin's tool list (different, smaller, real)

Extracted via `javap` on `FileTools`, `DiffTools`, `EditorTools`,
`DiagnosticTools` (the registration classes) in the compiled jar:

```
close_tab, openFile, open_files, get_all_opened_file_paths, openDiff,
reformat_file, getDiagnostics
```

7 tools — notably fewer than VS Code. Selection is **push-only** in this
plugin (`SelectionChangedNotification`), not a pollable tool — there's no
`getCurrentSelection`/`getWorkspaceFolders`/`executeCode`/`saveDocument`/
`checkDocumentDirty` in this plugin at all.

**Two tools here that don't exist in VS Code's extension or in AgentIDE:**

- **`reformat_file`** — asks the IDE to run its own code formatter
  (IntelliJ's `ReformatCodeProcessor`) on a file. A real, genuine
  capability gap versus what AgentIDE offers today. Sublime doesn't have
  a single universal "reformat" the way IntelliJ does, but per-syntax
  indent/format commands could back this if ever added.
- **`open_files`** (plural) — opens multiple files in one call, instead
  of forcing one `openFile` call per file. Trivial to add on top of
  AgentIDE's existing `context.open_file()` (just loop over a list) if
  ever wanted.

Neither has been added to AgentIDE — noted here as a known, real,
verified option for later, not a requirement.

## Lock-file schema — confirmed correct, no `capabilities` field needed

`lib/lockfile.py`'s `write_lock()` writes exactly:

```json
{"pid": ..., "workspaceFolders": [...], "ideName": "...",
 "transport": "ws", "authToken": "..."}
```

This is **identical** to what every reference implementation
(`sublime-claude-code/claudeide/lockfile.py`, and its own
`docs/dev-notes.md`) writes and has smoke-tested working, including a
real end-to-end diff flow. **No `capabilities` field exists anywhere in
any real lock file** — an earlier subagent's claim that one was needed
was checked against real reference implementations and found to be
wrong. Don't re-add it without new, real evidence.

## A known sibling bug — checked, AgentIDE does NOT have it

An earlier investigation (2026-09-05, in the unrelated but
protocol-sibling `sublime-mcp` project) found that its diff-Accept path
was tearing down the entire IDE-companion WebSocket server as a side
effect of its cleanup code — which silently killed the `/ide` connection
right when the CLI needed to receive the Accept result. Symptom: file
saves correctly on disk, but Claude Code never sees it, and reports the
edit as failed/rejected.

**Checked in AgentIDE (2026-09-16): does not have this bug.** `stop()`
(the actual WSServer teardown) is only called from
`AgentIdeRestartCommand` and `plugin_unloaded()` — nothing in
`diff_view.accept()`, `reject()`, or `_teardown()` touches the server.
Verified by reading the actual call sites, not by inference.

## The actual explanation for "edits didn't visibly route through AgentIDE" today

Not a protocol defect. This Claude Code session's `/ide` connection was
established once, then orphaned by several subsequent Sublime Text
restarts during testing (each restart puts AgentIDE's server on a new
random port; the CLI doesn't auto-rediscover a new port after the one it
originally connected to disappears). Running `/ide` again reconnects it.
This matches the exact same failure mode independently diagnosed in the
2026-09-05 sibling investigation, for the same underlying reason.

## Important distinction: two unrelated kinds of "IDE integration"

Confirmed live by a peer Claude session actually connected inside
PyCharm (2026-09-16): PyCharm exposes a **separate, general-purpose MCP
server** (tool prefix `mcp__pycharm__`, ~50 tools) that is bundled with
the IDE itself and is **not** part of Anthropic's `/ide` protocol at all.
It includes real Jupyter notebook execution (`run_notebook_cell`,
`execute_code_on_kernel`, `create_notebook`, etc. — verified live,
returned real kernel output) plus unrelated things like
`execute_terminal_command`, `git_status`, `lint_files`, database tools,
run configurations, and symbol search.

This is a **different integration category** from everything else in
this document:

- **Claude Code `/ide` protocol** (lock file + WebSocket + the
  `openFile`/`openDiff`/`getDiagnostics`/etc. tool set) — what
  `claude-code-jetbrains-plugin` implements for JetBrains IDEs, what the
  VS Code extension implements, and what **AgentIDE implements for
  Sublime Text**. Auto-detected via `~/.claude/ide/*.lock`.
- **A vendor's own bespoke MCP server** (like PyCharm's `mcp__pycharm__`)
  — connected the ordinary way any MCP server is (via MCP config), has
  nothing to do with the `/ide` lock-file mechanism, and can expose
  whatever huge tool surface the vendor wants (JetBrains' one is ~50
  tools deep, covering far more than editor-companion basics).

**So: PyCharm really can run Jupyter cells for Claude, and the small
`claude-code-jetbrains-plugin` jar really doesn't have `executeCode` —
both are true, because they're different mechanisms.** AgentIDE's scope
is the first category (replicating the `/ide` protocol for Sublime), not
the second (building a general Sublime automation MCP server — that's
what `sublime-mcp` already is, separately, in this same environment).

## Correction (2026-09-16, later same day): `executeCode` does not require notebook editing

The paragraph above originally concluded Jupyter/notebook support "would
only make sense if Sublime had native notebook editing to back it." That
was wrong — it conflated the MCP *contract* with VS Code's particular
*implementation* of it.

Decompiling `extension.js`'s `_r$()` (the real handler behind
`G.tool("executeCode", ...)`) shows the wire contract is just
`executeCode(code: string) -> content blocks`. VS Code chooses to
satisfy that by finding `vscode.window.activeNotebookEditor`, asking the
Jupyter extension for that notebook's kernel, appending a cell, running
`notebook.cell.execute`, and reading the cell's outputs back — but that's
VS Code's implementation choice, not something the protocol requires.
Nothing in the schema or response shape says "must come from a `.ipynb`
kernel."

AgentIDE now backs the same contract with a plain persistent Python
subprocess (`lib/execute.py`) instead of a real Jupyter kernel:
- Satisfies the "state persists across calls" behavior the real tool's
  description promises, via one long-lived interpreter process per
  session (not `python -c` per call).
- Gated behind the same user-consent step the real `_r$()` has
  (`Zr0()`'s VS Code QuickPick) — implemented here as a Sublime
  `show_quick_panel` with Execute/Cancel, returning the same cancellation
  message text on decline.
- Runs off the plugin-host thread entirely (its own OS process, driven
  from a background thread) so a hang or crash in executed code can't
  take Sublime down with it.
- Gives up the `display_data`/image content-block branch (unreachable
  without a real kernel) — text/error output only. Not a `jupyter_client`
  kernel client: pyzmq doesn't load in Sublime's restricted plugin-host
  Python, and a plain subprocess needs zero extra dependencies.

## Where this came from, for future reference

- Real primary sources: see the two file paths at the top of this doc.
  Both are genuine, unmodified, currently-installed copies — no need to
  hunt for or re-download anything.
- The 2026-09-05 investigation (sibling bug, VS Code extension
  decompilation) is recorded in
  `~/data/logs/jsonl_tail_transcripts/2026-09-05.md` (search for
  "openDiff" or "claude_ide.py").
- Every other agent's own session storage (Devin's `sessions.db`, omp's
  `~/.omp/agent/*.db` + `~/.omp/logs/`, jcode's `~/.jcode/sessions/`,
  Grok's `~/.grok/sessions/`, Codex's `~/.codex/sessions/`) was searched
  exhaustively for a remembered "~10 capabilities" list and came up
  empty — the JetBrains plugin's 7-tool list (this doc) is the closest
  real match found and is very likely what was being remembered.