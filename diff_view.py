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


def set_resolver(fn):
    global _resolver
    _resolver = fn


def _resolve(client_id, request_id, payload):
    if _resolver is not None:
        _resolver(client_id, request_id, payload)


def active_count():
    return len(_diffs)


def open_diff_ui(client_id, request_id, old_file_path, new_file_path, new_file_contents, tab_name):
    window = sublime.active_window()

    old_text = ""
    if old_file_path and os.path.exists(old_file_path):
        with open(old_file_path, encoding="utf-8", errors="replace") as fh:
            old_text = fh.read()

    target = new_file_path or old_file_path
    if tab_name in _diffs:
        tab_name = "{} ({})".format(tab_name, request_id)

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


def _add_action_phantom(view, tab_name):
    html = """
    <body id="agentide-diff-actions">
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
    ps = sublime.PhantomSet(view, "agentide_diff_actions")
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
    view = _view_by_id(rec["view_id"])
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
    instead of guessed at or silently discarded."""
    existing = context.find_view(target)
    if existing is not None:
        _apply_and_save(existing, content, on_done, on_error)
        return

    window = sublime.active_window()
    if os.path.exists(target):
        view = window.open_file(target)
        _finish_when_loaded(view, content, on_done, on_error)
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
    _apply_and_save(view, content, on_done, on_error)


def _finish_when_loaded(view, content, on_done, on_error, tries=200):
    if view.is_loading():
        if tries <= 0:
            on_error(OSError("Timed out waiting for {} to load".format(target_name(view))))
            return
        sublime.set_timeout(lambda: _finish_when_loaded(view, content, on_done, on_error, tries - 1), 20)
        return
    _apply_and_save(view, content, on_done, on_error)


def _apply_and_save(view, content, on_done, on_error):
    view.run_command("agentide_replace_content", {"text": content})
    try:
        view.run_command("save")
    except Exception as exc:  # noqa: BLE001 - surface any save failure to the caller
        on_error(exc)
        return
    on_done()


def target_name(view):
    return view.file_name() or view.name() or "?"


def _teardown(tab_name):
    rec = _diffs.pop(tab_name, None)
    if rec is None:
        return
    _phantom_sets.pop(rec["view_id"], None)
    view = _view_by_id(rec["view_id"])
    if view is not None:
        view.set_scratch(True)
        view.close()


def _syntax_for(path):
    try:
        return sublime.find_syntax_for_file(path)
    except Exception:  # noqa: BLE001 - older builds
        return None


def _view_by_id(view_id):
    for window in sublime.windows():
        for view in window.views():
            if view.id() == view_id:
                return view
    return None
