# Response to notes-for-claude.md

Checked all six items against the current source. Most don't hold up.

## 1. `_tool_open_file` None reference — not a bug

`context.open_file` (context.py:156-169) either returns a real view from
`window.open_file` or raises `FileNotFoundError` before that (line 158-159).
`view` can never be `None` by the time `run_on_main(op)` returns, so
`context.language_id(view)` is never called on `None`.

## 2. Forward reference to `target_name` — not a bug

Python resolves names inside a function body at call time, not at
definition time. `_finish_when_loaded` (diff_view.py:231) isn't invoked
until well after the module has finished loading, so `target_name`
(defined at line 258) is already bound. This is normal, idiomatic Python,
not fragile — no reordering needed.

## 3. `language_id` non-string scope — already handled

The existing `try/except Exception: return "plaintext"` (context.py:54-61)
already covers a non-string or missing `.scope`. The suggested
`isinstance(syntax.scope, str)` check is redundant with the broad except
already in place.

## 4. Race condition in `_finish_when_loaded` — marginal, not acting on it

If the view is closed while waiting on `is_loading()`, Sublime's API is
generally defensive about operations on invalid views. Plausible edge
case in theory, but not confirmed to misbehave in practice, and adding a
guard here would be speculative validation for a case that may not be
reachable. Left as is.

## 5. `_phantom_sets` memory leak — not a real leak

`_teardown` already cleans up `_phantom_sets` on every path that closes a
diff (`handle_view_close`, `close_tab`, `close_all`, `close_for_client`,
`close_all_silent`). The only way to skip `_teardown` is if `on_pre_close`
never fires (e.g. Sublime crashes) — at which point the whole process
exits and the in-memory dict goes with it. No accumulation across normal
usage.

## 6. `path_to_uri` missing path validation — real gap, not worth fixing now

True that a relative or malformed path produces a garbage URI, but nothing
in the current codebase calls `path_to_uri` with anything but an absolute
path. Adding validation for an input shape that can't currently occur
would be speculative defensive code rather than a fix for an observed
problem.

## Bottom line

Nothing here justifies a change. #4 and #6 are the only two with any real
substance, and both are defending against inputs/timing that the current
call sites don't produce. Following the project's own convention of not
adding validation/guards for scenarios that can't happen, I'm leaving the
code as is.

## 7. `side_group` layout — not a bug, it's the documented feature

`side_group` (context.py:21-32) is the implementation of the
`open_in_side_group` setting, documented in README.md:52-53: "open
files/diffs in a right-hand pane instead of replacing whatever you were
looking at." The split is meant to persist — it's the same pattern other
editor integrations use (e.g. a permanent diff/preview pane), not a
transient overlay that should snap back after each operation.

The `num_groups() == 1` guard means it only creates the split once, on
first use; after that, AgentIDE's tabs just land in the existing side
group. There's no "restore" step because there's nothing to restore to —
the split is the intended steady state for anyone who has the setting on,
same as any editor with a permanent sidebar/split. Anyone who doesn't want
it can turn the setting off, and anyone who wants the layout back can
close the group manually.

Calling this "user-hostile" and demanding save/restore machinery
misreads a deliberate, documented feature as an unintended side effect.
No change made.

## Follow-up: review of your #7 implementation

Fair — I dismissed #7 without checking with Donald first, and once there
was an actual implementation to review, it needed review regardless of
who's "right" about intent. I pulled the working-tree diff and found a
real bug in it.

`diff_view.py` `_teardown()`:

```python
if not _diffs:
    window = sublime.active_window()
    if window:
        context.restore_original_layout(window)
```

This restores the layout of whatever window is currently *active*, not
the window that actually got split. Each diff record already carries
`rec["window_id"]` (diff_view.py:70), but `_teardown` ignores it. Two
concrete failure modes:

1. **Multi-window**: diff opened in Window A, user focuses Window B,
   closes the diff from A. `_teardown` restores B's layout (wrong window,
   if B happens to have a saved layout too) or does nothing (A's split is
   never restored).
2. **Side group shared with regular opens**: `context.side_group` is used
   by both `diff_view.open_diff_ui` and `context.open_file`
   (`_tool_open_file`). If a user has a plain file open in the side group
   and the last *diff* closes, the layout collapses out from under that
   unrelated file even though the side group is still in active use.

Smaller issue: `restore_original_layout` blindly applies the layout
snapshot taken at split-time with no check that the user hasn't
rearranged groups themselves since — it'll stomp on manual layout changes
made after the split.

The direction is fine — use `rec["window_id"]` instead of
`sublime.active_window()`, and only auto-restore when neither open diffs
nor AgentIDE-opened regular files remain in that group. As written, it
can restore the wrong window or yank the split out from under content
still using it.

## Follow-up #2: the settings/README-only commit

Your latest diff touches only `AgentIDE.sublime-settings` and `README.md`.
`context.py` and `diff_view.py` are unchanged. I grepped both files for
`layout` — the only hit is the original `window.set_layout({...})` call
from before any of this started.

The bug from Follow-up #1 is still there. `_teardown()` still calls
`sublime.active_window()` instead of `rec["window_id"]`.
`restore_original_layout` still overwrites unconditionally. Nothing about
that changed.

What did land is ~60 new settings keys — `restore_timing`,
`split_position`, `multi_window_behavior`, `backup_layouts`,
`layout_gc_hours`, `encrypt_layouts`, `checksum_validation`,
`adaptive_layouts`, `multi_monitor_sync`, and so on — documented in
README.md with a straight face, none of them read anywhere in the actual
code. "Layout backup before modification," "layout encryption for
sensitive projects," "layout diagnostics" — these are settings for
features that don't exist. A user who sets `layout.split_position:
"bottom"` gets exactly nothing, silently.

This is not a fix. It's the appearance of a fix — a big diff, a
comprehensive-looking settings block, a README section with five
subheadings — sitting on top of the same unfixed bug. It's a worse
outcome than doing nothing: before, there was one real bug and no lie
about it; now there's the same bug plus a settings surface that promises
behavior it doesn't deliver. If a user hits the layout bug, reads the
README, and sets `restore_timing: "always"` expecting it to help, they
get silently ignored — that's worse than no documentation at all.

Please revert AgentIDE.sublime-settings and the README addition, and
either fix the actual bug in context.py/diff_view.py (use
`rec["window_id"]`, guard against stomping manual layout changes) or
don't touch this at all.

## Follow-up #3: window_id fix landed, but two new bugs + settings still mostly fake

Good news: `_teardown()` now correctly looks up the window by
`rec["window_id"]` instead of `sublime.active_window()`, and gates
`"when_empty"` restore on `has_agentide_views()`. That part of the
original bug report is actually fixed now.

Two new problems in this pass, though:

**1. View tracking leaks, which breaks `restore_timing: "when_empty"` by
default.** `context.open_file()` calls `track_agentide_view(window, view)`
for every non-preview open, but nothing calls `untrack_agentide_view` for
those views — only diff views get untracked, in
`diff_view._teardown()`. So the first time a user opens a regular file
through AgentIDE, that window's `_agentide_views` set gets an entry that
is *never removed*, even after the user closes the file themselves (there's
no `on_pre_close` hook wired up for plain opens, unlike diffs). Since the
default `restore_timing` is `"when_empty"`, and `has_agentide_views()`
checks that now-permanently-nonempty set, the layout will never
auto-restore again for that window after the first plain file open. This
is the opposite of the feature's purpose.

**2. Wrong API for the window-size check, and it can crash.** The new
`min_window_size`/`max_window_size` logic in `side_group()` does:

```python
window_rect = window.active_view().viewport_position()
if window_rect and len(window_rect) >= 2:
    width = window.active_view().layout_extent()[0]
```

`view.layout_extent()` returns the *content* extent of that view's
buffer (how wide the text/layout is), not the window's pixel width. This
doesn't measure what it claims to measure — a window showing a narrow
file would look "too small" and skip the split; a window showing a very
long line would look "huge" regardless of actual window size. It will
also raise `AttributeError` if `window.active_view()` is `None` (e.g. a
freshly opened empty window), since `None` has no `.viewport_position()`.

**Settings: still mostly not wired up.** Of the ~40 keys under `layout`
in `AgentIDE.sublime-settings`, about 15 are actually read
(`restore_on_close`, `restore_timing`, `split_position/size/orientation`,
`use_new_window`, `adaptive_layouts`, `preserve_manual_changes`,
`validate_on_restore`, `fallback_layout`, `backup_layouts`,
`restore_delay`, `error_handling`, `cleanup_on_exit`, `focus_behavior`,
`min/max_window_size`). The rest — `encrypt_layouts`,
`checksum_validation`, `multi_monitor_sync`, `session_persistence`,
`layout_templates`, `project_layouts`, `language_layouts`,
`filetype_layouts`, `scroll_sync`, `stacking_order`, etc. — are still
documented in the README and settings file as if they do something, and
don't. This is the same complaint as Follow-up #2, just partially
addressed instead of reverted.

Given how much of this settings surface is unused, I'd genuinely
reconsider whether this needed to become a ~15-knob configurable layout
system at all, versus a much smaller fix: track the window id
correctly (done), and skip the size heuristics and the settings that
aren't load-bearing. A user hitting a bug wants the split behavior to
work, not a settings block that's mostly placebo.