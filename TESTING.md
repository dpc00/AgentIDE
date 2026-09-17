# AgentIDE manual test checklist

Everything on this list should be done by actually clicking/typing in a
real Sublime Text window and watching what happens -- not by calling
internal functions from a script. That's the gap in what's been verified
so far: internal calls prove the logic runs, not that a human using the
plugin gets the right result.

Check off each item as `[x]` once you've actually done it and it behaved
as described. Leave a one-line note under any that didn't.

## 0. Baseline

- [ ] Fresh Sublime Text restart. Console (`View > Show Console`) has no
      Python traceback anywhere in the boot log.
- [ ] `[AgentIDE] listening on 127.0.0.1:<port>` appears in the console.
- [ ] `/ide` from a real `claude` CLI session connects successfully
      (status bar shows the connected indicator, not just "started").

## 1. Command Palette -- every entry, by hand

For each, open the Command Palette (Ctrl+Shift+P), type the caption,
click it with the mouse. Confirm it does what it says and nothing else
(no stray dialog, no new empty tab, no console error).

- [ ] **AgentIDE: Status** -- dialog shows port/connection/lock path.
      Click OK to dismiss.
- [ ] **AgentIDE: Restart Server** -- console shows stop+start, a new
      port, `/ide` still reconnects afterward.
- [ ] **AgentIDE: Send Selection as @-mention** -- with real text
      selected in a real file, confirm the connected CLI actually
      receives the mention.
- [ ] **AgentIDE: Restore Layout** -- with no split active, confirm it's
      a harmless no-op (no error, no unexpected layout change).
- [ ] **AgentIDE: Cleanup Layouts** -- confirm it runs without error
      whether or not there's anything to clean up.
- [ ] **Preferences: AgentIDE Settings** -- opens the default+user
      settings side by side, in a split view.

## 2. Core diff flow, via a real connected CLI

Not `diff_view.open_diff_ui()` called directly -- have the actual
`claude` CLI propose a real edit to a real file.

- [ ] Single-group window, `open_in_side_group: true`. Ask the CLI to
      edit a file. Confirm: window splits, diff opens in the new group,
      gutter markers show the diff, Accept/Reject phantom is visible.
- [ ] Click **Accept** (mouse, not a script). File on disk has the new
      content. Tab closes. Layout restores per `restore_timing`.
- [ ] Repeat, click **Reject** instead. File on disk is unchanged.
      Layout restores per `restore_timing`.
- [ ] Close the diff tab with the mouse (the `x` on the tab) instead of
      clicking Accept/Reject. Confirm this counts as Reject (per the
      docstring) and the CLI actually gets told so, not left hanging.
- [ ] Ask the CLI to edit a file that's *already open* in a normal tab
      with unsaved changes. Confirm accept writes through the existing
      view rather than clobbering it or losing the unsaved state weirdly.
- [ ] Ask the CLI to edit a file that doesn't exist yet. Confirm the
      directory gets created and the new file is saved correctly on
      Accept.

## 3. Multiple diffs at once

- [ ] Have the CLI open two diffs back to back (or fire a second request
      before resolving the first). Confirm both appear as separate tabs
      in the side group.
- [ ] Close/reject the first one. Confirm the layout does **not**
      collapse back to one group while the second diff is still open.
- [ ] Close/reject the second one. Confirm the layout **does** restore
      now (per `restore_timing`).

## 4. Settings -- each one, changed via `Packages/User/AgentIDE.sublime-settings`,
   verified by actually triggering the behavior it controls (not by
   reading `context.settings()` in a script)

- [ ] `split_position`: try `"left"`, confirm the pane actually opens on
      the left instead of the right.
- [ ] `split_orientation`: try `"horizontal"`, confirm a top/bottom split
      instead of left/right.
- [ ] `split_size`: try `0.25` and `0.75`, confirm the pane width/height
      visibly matches.
- [ ] `min_window_size` / `max_window_size`: shrink/maximize the actual
      OS window to cross the threshold, confirm the split is skipped
      outside the configured range.
- [ ] `adaptive_layouts`: compare a narrow vs. very wide window with this
      on vs. off, confirm the split ratio actually shifts only when on.
- [ ] `restore_timing`: test all four values (`always`, `when_empty`,
      `manual`, `never`) against the same close-a-diff action, confirm
      each produces the documented behavior.
- [ ] `preserve_manual_changes: true` -- open a diff, manually drag the
      group divider to resize it (a real manual layout change), close
      the diff, confirm the restore is skipped and your resize is left
      alone.
- [ ] `preserve_manual_changes: false` -- same steps, confirm it
      restores anyway, overwriting your manual resize.
- [ ] `restore_delay`: set to `2000`, close a diff, confirm the restore
      visibly happens ~2 seconds later, not instantly.
- [ ] `focus_behavior`: test all three values (`new_content`,
      `keep_current`, `alternate`) with `openFile`, confirm focus
      actually does/doesn't move as documented.
- [ ] `error_handling` + `fallback_layout`: hard to trigger a real
      `set_layout()` failure -- at minimum, confirm an invalid
      `fallback_layout` value doesn't itself crash anything when
      `error_handling` isn't `"fallback"`.
- [ ] `backup_layouts` / `max_backup_versions`: force a plugin reload
      (edit and save `agent_ide.py`) mid-diff so the in-memory
      `_saved_layouts` entry is lost, then use **AgentIDE: Restore
      Layout** manually -- confirm it recovers from the backup instead
      of doing nothing.
- [ ] `layout_gc_hours`: set to something tiny (e.g. `0.02` = ~1 minute),
      wait it out, run **AgentIDE: Cleanup Layouts**, confirm the stale
      entry is actually gone (check via a script if needed, but trigger
      it by hand).

## 4b. `diff_display: "panel"` (new -- doesn't touch layout at all)

- [ ] Set `"diff_display": "panel"`. Ask the CLI to edit a file. Confirm
      the diff appears in a bottom panel, not a new group -- window
      layout is completely unchanged (no split).
- [ ] Click **Accept** in the panel's phantom. File on disk has the new
      content, panel closes.
- [ ] Repeat, click **Reject** instead. File unchanged, panel closes.
- [ ] With the panel focused, press **Escape**. Confirm this counts as
      Reject (file unchanged, CLI actually gets told, panel closes) --
      same contract as closing a side_group tab.
- [ ] Trigger two diffs back to back in panel mode. Confirm each is
      independently resolvable (accepting/rejecting one doesn't affect
      the other's pending request), even though only one panel is
      visible at a time.
- [ ] Switch back to `"diff_display": "side_group"` (or leave it unset)
      mid-session and confirm the old behavior is unchanged -- this is
      an added option, not a replacement.

## 5. Multi-window

- [ ] Two separate Sublime windows open, each with its own connected CLI
      session (or one CLI, two windows). Trigger a diff in window A.
      Confirm window B's layout is completely untouched.
- [ ] Close the diff in window A. Confirm only window A's layout
      restores.

## 6. Disruption scenarios

- [ ] Disconnect the CLI (`Ctrl+C` the `claude` process) while a diff is
      open. Confirm the tab closes / resolves as rejected rather than
      being left permanently orphaned.
- [ ] Force-quit Sublime Text entirely while a diff is open (not a
      graceful close). Relaunch. Confirm nothing is left in a broken
      state -- no phantom errors, no stuck layout, plugin loads clean.
- [ ] With a diff open, manually close the *window* (not the tab) via
      the OS close button. Confirm this doesn't hang or leave the CLI
      waiting forever for a response that will never come.

## 7. Known limitation to just confirm, not "fix"

- [ ] Start with 2+ tabs already open in a specific order in the single
      group. Trigger a diff (splits to 2 groups), accept/reject it
      (restores to 1 group). Confirm your original tabs are still there
      -- note whether their order changed. (Per the documented
      limitation, order changing is expected/acceptable; losing a tab
      entirely would not be.)

---

Notes / failures found while running this, with date:
