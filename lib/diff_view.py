"""Diff review UI for the blocking openDiff tool.

Uses Sublime's own built-in Incremental Diff engine (`View.set_reference_
document`) instead of hand-building a two-pane comparison: the proposed
content goes straight into a buffer, the original content becomes its
diff baseline, and ST's native gutter markers / Ctrl+./Ctrl+, navigation /
Ctrl+K,Ctrl+Z per-hunk revert all come for free. Accept/Reject is a
retained phantom at the top of the buffer, not a modal -- closing the tab
counts as Reject so nothing can orphan a pending request.

All functions here run on the main thread; callers marshal.
"""

import os

import sublime

from . import context

_diffs = {}         # tab_name -> record dict
_phantom_sets = {}  # view id -> PhantomSet (must be retained or it vanishes)
_resolver = None    # set by agent_ide: fn(client_id, request_id, payload)
_panel_queue = []   # tab_name, in arrival order -- panel mode's FIFO of pending diffs


def set_resolver(fn):
    global _resolver
    _resolver = fn


def _resolve(client_id, request_id, payload):
    if _resolver is not None:
        _resolver(client_id, request_id, payload)


def active_count():
    return len(_diffs)


def open_diff_ui(client_id, request_id, old_file_path, new_file_path, new_file_contents, tab_name):
    old_text = ""
    if old_file_path and os.path.exists(old_file_path):
        with open(old_file_path, encoding="utf-8", errors="replace") as fh:
            old_text = fh.read()

    target = new_file_path or old_file_path
    if tab_name in _diffs:
        tab_name = "{} ({})".format(tab_name, request_id)

    mode = context.settings().get("diff_display", "side_group")
    if mode == "panel":
        _open_diff_panel(client_id, request_id, old_text, target, new_file_contents, tab_name)
    else:
        _open_diff_side_group(client_id, request_id, old_text, target, new_file_contents, tab_name)


def _open_diff_side_group(client_id, request_id, old_text, target, new_file_contents, tab_name):
    window = sublime.active_window()

    group = context.side_group(window)
    if group >= 0:
        window.focus_group(group)
    view = window.new_file()
    view.set_scratch(True)
    view.set_name(tab_name)
    syntax = _syntax_for(target)
    if syntax:
        view.assign_syntax(syntax)
    view.run_command("append", {"characters": new_file_contents})
    view.set_reference_document(old_text)
    view.set_read_only(True)

    _add_action_phantom(view, tab_name)

    _diffs[tab_name] = {
        "mode": "side_group",
        "client_id": client_id,
        "request_id": request_id,
        "view_id": view.id(),
        "window_id": window.id(),
        "target": target,
        "resolved": False,
    }
    view.show(sublime.Region(0, 0))
    window.focus_view(view)
    sublime.status_message("AgentIDE diff: {} — Accept/Reject above the buffer".format(tab_name))


def _open_diff_panel(client_id, request_id, old_text, target, new_file_contents, tab_name):
    """Renders the diff in a bottom output panel instead of a side group --
    doesn't touch the window layout at all (no split, nothing to restore),
    and closes on Escape like any other panel (a pending diff stays pending). Only one panel is visible
    at a time, so pending diffs form a strict FIFO queue (_panel_queue):
    a diff that arrives while another is already pending is queued and
    shown once its turn comes, in arrival order -- it never interrupts
    whatever you're currently looking at, and nothing here is ever
    auto-accepted or auto-rejected. Every pending diff is only ever
    resolved by an explicit Accept/Reject."""
    window = sublime.active_window()
    panel_id = "agent_ide_diff__{}".format(_sanitize_panel_id(tab_name))

    view = window.create_output_panel(panel_id, unlisted=True)
    view.set_read_only(False)
    view.set_name(tab_name)
    syntax = _syntax_for(target)
    if syntax:
        view.assign_syntax(syntax)
    view.run_command("append", {"characters": new_file_contents})
    view.set_reference_document(old_text)
    view.set_read_only(True)

    _add_action_phantom(view, tab_name)

    _diffs[tab_name] = {
        "mode": "panel",
        "client_id": client_id,
        "request_id": request_id,
        "view_id": view.id(),
        "panel_id": panel_id,
        "window_id": window.id(),
        "target": target,
        "resolved": False,
    }
    was_empty = len(_panel_queue) == 0
    _panel_queue.append(tab_name)
    if was_empty:
        window.run_command("show_panel", {"panel": "output.{}".format(panel_id)})
        sublime.status_message(
            "AgentIDE diff: {} — Accept/Reject above the panel".format(tab_name))
    else:
        sublime.status_message(
            "AgentIDE diff: {} queued ({} pending)".format(tab_name, len(_panel_queue)))


def _sanitize_panel_id(tab_name):
    return "".join(c if c.isalnum() else "_" for c in tab_name)


def _show_next_pending_panel(window):
    """Resolving the diff on screen must not leave the next-queued one
    invisible -- the CLI would still be waiting on it with nothing on
    screen to act on. Strict FIFO: whichever diff has been waiting
    longest (front of _panel_queue) is shown next. This only ever
    reveals a still-pending diff; it never resolves one."""
    while _panel_queue:
        tab_name = _panel_queue[0]
        rec = _diffs.get(tab_name)
        if rec is None or rec["resolved"]:
            _panel_queue.pop(0)  # resolved out of band (e.g. closeAllDiffTabs) -- skip it
            continue
        window.run_command("show_panel", {"panel": "output.{}".format(rec["panel_id"])})
        sublime.status_message(
            "AgentIDE diff: {} — Accept/Reject above the panel ({} pending)".format(
                tab_name, len(_panel_queue)))
        return


def _add_action_phantom(view, tab_name):
    html = """
    <body id="agentIDE-diff-actions">
      <style>
        body { padding: 6px 0; }
        a.btn { padding: 3px 12px; border-radius: 4px; text-decoration: none; }
        a.accept { background-color: #1c4d2e; color: #e8ffe8; }
        a.reject { background-color: #5a1f1f; color: #ffecec; }
        span.hint { color: color(var(--foreground) alpha(0.5)); font-size: 0.9em; }
      </style>
      <div>
        <a class="btn accept" href="accept">✓ Accept</a>&nbsp;&nbsp;
        <a class="btn reject" href="reject">✗ Reject</a>&nbsp;&nbsp;
        <span class="hint">Gutter marks show the diff -- Ctrl+K,Ctrl+Z reverts a hunk, Ctrl+./Ctrl+, navigate</span>
      </div>
    </body>"""
    ps = sublime.PhantomSet(view, "agentIDE_diff_actions")
    phantom = sublime.Phantom(
        sublime.Region(0, 0), html, sublime.LAYOUT_BLOCK,
        on_navigate=lambda href, t=tab_name: _on_action(href, t),
    )
    ps.update([phantom])
    _phantom_sets[view.id()] = ps


def _on_action(href, tab_name):
    if href == "accept":
        accept(tab_name)
    elif href == "reject":
        reject(tab_name)


# ---------- outcomes ----------


def accept(tab_name):
    rec = _diffs.get(tab_name)
    if rec is None or rec["resolved"]:
        return False
    view = _find_view(rec)
    if view is None:
        return reject(tab_name)
    content = view.substr(sublime.Region(0, view.size()))

    def on_written():
        rec["resolved"] = True
        # Two content blocks: the reference client ignores a bare FILE_SAVED
        # and re-prompts without the body if the final content isn't included.
        _resolve(rec["client_id"], rec["request_id"], ["FILE_SAVED", content])
        _teardown(tab_name)
        sublime.status_message("AgentIDE diff accepted → {}".format(os.path.basename(rec["target"])))

    def on_error(exc):
        sublime.error_message("AgentIDE diff: writing {} failed:\n{}".format(rec["target"], exc))

    _write_target(rec["target"], content, on_written, on_error)
    return True


def reject(tab_name):
    rec = _diffs.get(tab_name)
    if rec is None or rec["resolved"]:
        return False
    rec["resolved"] = True
    _resolve(rec["client_id"], rec["request_id"], ["DIFF_REJECTED", tab_name])
    _teardown(tab_name)
    sublime.status_message("AgentIDE diff rejected")
    return True


def handle_view_close(view):
    """on_pre_close: manually closing the diff tab counts as Reject, so
    nothing can be left orphaned pending forever."""
    vid = view.id()
    for tab_name, rec in list(_diffs.items()):
        if rec["view_id"] == vid and not rec["resolved"]:
            rec["resolved"] = True
            _resolve(rec["client_id"], rec["request_id"], ["DIFF_REJECTED", tab_name])
            sublime.set_timeout(lambda t=tab_name: _teardown(t), 0)
            return


def close_tab(tab_name):
    if tab_name in _diffs:
        reject(tab_name)
        _teardown(tab_name)
        return True
    return False


def close_all():
    tabs = list(_diffs.keys())
    for tab_name in tabs:
        reject(tab_name)
        _teardown(tab_name)
    return len(tabs)


def close_for_client(client_id):
    """One session disconnected: tear down its tabs silently -- its
    pending entries are already resolved by the caller."""
    for tab_name, rec in list(_diffs.items()):
        if rec["client_id"] == client_id:
            rec["resolved"] = True
            _teardown(tab_name)


def close_all_silent():
    for tab_name, rec in list(_diffs.items()):
        rec["resolved"] = True
        _teardown(tab_name)


# ---------- internals ----------


def _write_target(target, content, on_done, on_error):
    """Never write raw bytes ourselves -- always push the content through a
    real, file-backed Sublime view and let View.save() write it, so the
    view's own (auto-detected) line_endings()/encoding() are honored
    instead of guessed at or silently discarded.

    If the target already has an open view, write into that one and leave
    it exactly as the user had it. Otherwise AgentIDE has to open or create
    a view just to save through -- that view is closed again afterward and
    the previously-focused view/tab is restored, so accepting a diff never
    leaves a surprise new tab behind or steals focus off the diff review."""
    window = sublime.active_window()
    refocus = window.active_view()

    existing = context.find_view(target)
    if existing is not None:
        _apply_and_save(existing, content, on_done, on_error, close_after=False, refocus=None)
        return

    if os.path.exists(target):
        view = window.open_file(target)
        _finish_when_loaded(view, content, on_done, on_error, refocus)
        return

    try:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        on_error(exc)
        return
    view = window.new_file()
    view.retarget(target)
    _apply_and_save(view, content, on_done, on_error, close_after=True, refocus=refocus)


def _finish_when_loaded(view, content, on_done, on_error, refocus, tries=200):
    if view.is_loading():
        if tries <= 0:
            on_error(OSError("Timed out waiting for {} to load".format(target_name(view))))
            return
        sublime.set_timeout(lambda: _finish_when_loaded(view, content, on_done, on_error, refocus, tries - 1), 20)
        return
    _apply_and_save(view, content, on_done, on_error, close_after=True, refocus=refocus)


def _apply_and_save(view, content, on_done, on_error, close_after, refocus):
    view.run_command("agent_ide_replace_content", {"text": content})
    try:
        view.run_command("save")
    except Exception as exc:  # noqa: BLE001 - surface any save failure to the caller
        on_error(exc)
        return
    if close_after:
        view.set_scratch(True)
        view.close()
    if refocus is not None:
        window = refocus.window()
        if window is not None:
            window.focus_view(refocus)
    on_done()


def target_name(view):
    return view.file_name() or view.name() or "?"


def _teardown(tab_name):
    rec = _diffs.pop(tab_name, None)
    if rec is None:
        return
    _phantom_sets.pop(rec["view_id"], None)
    window_id = rec.get("window_id")
    window = _window_by_id(window_id) if window_id is not None else None

    if rec.get("mode") == "panel":
        # Panel mode never touched the window layout -- nothing to restore.
        if tab_name in _panel_queue:
            _panel_queue.remove(tab_name)
        if window is not None and rec.get("panel_id"):
            window.destroy_output_panel(rec["panel_id"])
            _show_next_pending_panel(window)
        return

    view = _view_by_id(rec["view_id"])
    if view is not None:
        view.set_scratch(True)
        view.close()

    if window is None:
        return

    restore_timing = context.settings().get("restore_timing", "when_empty")
    if restore_timing == "always":
        context.restore_layout(window)
    elif restore_timing == "when_empty":
        other_diffs_open = any(r["window_id"] == window_id for r in _diffs.values())
        if not other_diffs_open and not context.has_agentIDE_views(window):
            context.restore_layout(window)
    # "manual" and "never" don't auto-restore


def _syntax_for(path):
    try:
        return sublime.find_syntax_for_file(path)
    except Exception:  # noqa: BLE001 - older builds
        return None


def _find_view(rec):
    """Panel views don't show up in window.views() (they're not tabs in a
    group), so they need their own lookup path."""
    if rec.get("mode") == "panel":
        window = _window_by_id(rec.get("window_id"))
        if window is None:
            return None
        return window.find_output_panel(rec["panel_id"])
    return _view_by_id(rec["view_id"])


def _view_by_id(view_id):
    for window in sublime.windows():
        for view in window.views():
            if view.id() == view_id:
                return view
    return None


def _window_by_id(window_id):
    for window in sublime.windows():
        if window.id() == window_id:
            return window
    return None


def _window_by_id(window_id):
    for window in sublime.windows():
        if window.id() == window_id:
            return window
    return None
