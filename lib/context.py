"""Editor-state tools: selection, open editors, workspace folders, and
opening/saving files -- the read-mostly half of the protocol, as opposed
to diff_view.py's blocking review flow.

All functions here run on the main thread; callers marshal.
"""

import os
import time

import sublime

from .pathurl import path_to_uri

_latest_selection = None  # last non-empty selection payload seen, any view
_saved_layouts = {}  # window id -> {"layout": dict, "saved_at": float}
_layout_backups = {}  # window id -> list of {"layout": dict, "saved_at": float}
_agentIDE_views = {}  # window id -> set of view IDs opened by AgentIDE


def settings():
    return sublime.load_settings("AgentIDE.sublime-settings")


def _viewport_width(window):
    """The visible width of the active group's viewport, in layout px --
    the closest real proxy Sublime's API exposes for "how much room is
    there to split"; there is no direct window-pixel-size accessor."""
    view = window.active_view()
    if view is None:
        return None
    return view.viewport_extent()[0]


def side_group(window):
    """Group index for tabs AgentIDE opens: a right-hand pane, so the
    user's own tabs stay untouched. Creates the split on first use.
    Returns -1 (active group) if the setting is off."""
    if not settings().get("open_in_side_group", True):
        return -1

    if window.num_groups() == 1:
        width = _viewport_width(window)
        min_size = settings().get("min_window_size", 400)
        max_size = settings().get("max_window_size")
        if width is not None:
            if width < min_size:
                return -1  # Window too small for split
            if max_size and width > max_size:
                return -1  # Window too large for split

        # Save original layout if restore is enabled
        window_id = window.id()
        save_original = settings().get("restore_on_close", True) and window_id not in _saved_layouts
        if save_original:
            original_layout = window.get_layout()

        # Apply split based on settings
        split_position = settings().get("split_position", "right")
        split_size = settings().get("split_size", 0.45)
        split_orientation = settings().get("split_orientation", "vertical")

        # Adaptive layout adjustments: shrink/grow the split for narrow/wide windows
        if settings().get("adaptive_layouts", True) and width is not None:
            if width < 1200:
                split_size = max(0.3, split_size * 0.8)  # Smaller split on small screens
            elif width > 2000:
                split_size = min(0.6, split_size * 1.2)  # Larger split on large screens

        if split_orientation == "vertical":
            if split_position == "left":
                cols = [0.0, split_size, 1.0]
            else:  # right
                cols = [0.0, 1.0 - split_size, 1.0]
            rows = [0.0, 1.0]
            cells = [[0, 0, 1, 1], [1, 0, 2, 1]]
        else:  # horizontal
            cols = [0.0, 1.0]
            if split_position == "top":
                rows = [0.0, split_size, 1.0]
            else:  # bottom
                rows = [0.0, 1.0 - split_size, 1.0]
            cells = [[0, 0, 1, 1], [0, 1, 1, 2]]

        applied_layout = {"cols": cols, "rows": rows, "cells": cells}
        window.set_layout(applied_layout)

        if save_original:
            _saved_layouts[window_id] = {
                "layout": original_layout,
                "applied": applied_layout,
                "saved_at": time.time(),
            }
    return window.num_groups() - 1


def restore_layout(window):
    """Restore the original window layout if it was saved. Falls back to
    the most recent backup if the plugin was reloaded and the in-memory
    saved-layout entry was lost.

    KNOWN LIMITATION: this only restores group geometry (cols/rows/cells)
    via window.get_layout()/set_layout() -- it does not snapshot or
    restore each view's (group, index_in_group). side_group() only ever
    splits when num_groups() == 1, so today every view ends up back in
    the single remaining group regardless (no cross-group misplacement
    possible), but tab order within that group is not guaranteed to
    match what the user had before the split. A precise restore would
    need to record (view, group, index) for every view before splitting
    and replay it with window.set_view_index() after restoring the grid."""
    if not settings().get("restore_on_close", True):
        return

    restore_timing = settings().get("restore_timing", "when_empty")
    if restore_timing == "never":
        return

    window_id = window.id()
    entry = _saved_layouts.get(window_id)
    if entry is None:
        backups = _layout_backups.get(window_id)
        if not backups:
            return
        entry = backups[-1]

    original_layout = entry["layout"]

    # Respect a manual layout change made since the split, rather than
    # overwrite it -- compare against what AgentIDE itself applied, not
    # the pre-split original (which of course differs; that's the split).
    # A backup-sourced entry has no "applied" value to compare against
    # (it's a recovery path after the primary entry was already lost), so
    # just proceed with the restore in that case.
    if settings().get("preserve_manual_changes", False) and "applied" in entry:
        if window.get_layout() != entry["applied"]:
            return

    if settings().get("backup_layouts", True):
        _backup_layout(window_id, window.get_layout())

    delay = settings().get("restore_delay", 0)
    if delay > 0:
        sublime.set_timeout(lambda: _apply_restore(window, original_layout, window_id), delay)
    else:
        _apply_restore(window, original_layout, window_id)


def _apply_restore(window, layout, window_id):
    """Actually apply the layout restore."""
    try:
        window.set_layout(layout)
        _saved_layouts.pop(window_id, None)
    except Exception:
        error_handling = settings().get("error_handling", "warn")
        if error_handling == "fallback":
            fallback = settings().get("fallback_layout")
            if fallback:
                try:
                    window.set_layout(fallback)
                except Exception:
                    pass
        elif error_handling == "warn":
            sublime.status_message("AgentIDE: warning - layout restore failed")
        elif error_handling == "error":
            sublime.error_message("AgentIDE: layout restore failed")
        # "ignore" (or any other value): fail silently


def _backup_layout(window_id, layout):
    """Record a timestamped backup, so a manual restore has something to
    fall back to if the in-memory saved-layout entry was lost (e.g. a
    plugin reload)."""
    max_backups = settings().get("max_backup_versions", 5)

    backups = _layout_backups.setdefault(window_id, [])
    backups.append({"layout": layout, "saved_at": time.time()})

    if len(backups) > max_backups:
        del backups[:-max_backups]


def track_agentIDE_view(window, view):
    """Track that a view was opened by AgentIDE."""
    window_id = window.id()
    if window_id not in _agentIDE_views:
        _agentIDE_views[window_id] = set()
    _agentIDE_views[window_id].add(view.id())


def untrack_agentIDE_view(window, view):
    """Stop tracking an AgentIDE-opened view."""
    window_id = window.id()
    if window_id in _agentIDE_views:
        _agentIDE_views[window_id].discard(view.id())
        if not _agentIDE_views[window_id]:
            del _agentIDE_views[window_id]


def has_agentIDE_views(window):
    """Check if window has any AgentIDE-opened views."""
    window_id = window.id()
    return window_id in _agentIDE_views and bool(_agentIDE_views[window_id])


def cleanup_layouts():
    """Drop saved layouts/backups for windows that no longer exist, and
    age out anything older than layout_gc_hours (covers a window that got
    split but never closed its diff/file, e.g. the CLI disconnected)."""
    if not settings().get("cleanup_on_exit", True):
        return

    current_window_ids = {window.id() for window in sublime.windows()}

    for window_id in list(_saved_layouts.keys()):
        if window_id not in current_window_ids:
            del _saved_layouts[window_id]

    for window_id in list(_layout_backups.keys()):
        if window_id not in current_window_ids:
            del _layout_backups[window_id]

    for window_id in list(_agentIDE_views.keys()):
        if window_id not in current_window_ids:
            del _agentIDE_views[window_id]

    gc_hours = settings().get("layout_gc_hours", 24)
    if gc_hours > 0:
        gc_threshold = time.time() - (gc_hours * 3600)
        for window_id in list(_saved_layouts.keys()):
            if _saved_layouts[window_id]["saved_at"] < gc_threshold:
                del _saved_layouts[window_id]
        for window_id in list(_layout_backups.keys()):
            kept = [b for b in _layout_backups[window_id] if b["saved_at"] >= gc_threshold]
            if kept:
                _layout_backups[window_id] = kept
            else:
                del _layout_backups[window_id]


def all_workspace_folders():
    folders = []
    for window in sublime.windows():
        for f in window.folders():
            if f not in folders:
                folders.append(f)
    return folders


def find_view(file_path):
    norm = os.path.normcase(os.path.normpath(file_path))
    for window in sublime.windows():
        for view in window.views():
            fn = view.file_name()
            if fn and os.path.normcase(os.path.normpath(fn)) == norm:
                return view
    return None


def language_id(view):
    try:
        syntax = view.syntax()
        if syntax is not None and syntax.scope:
            return syntax.scope.split(".")[-1]
    except Exception:  # noqa: BLE001 - older API surface
        return "plaintext"
    return "plaintext"


def guess_language(file_name):
    ext = os.path.splitext(file_name)[1].lower().lstrip(".")
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "tga"):
        return "image"
    return ext or "plaintext"


def selection_payload(view):
    regions = view.sel()
    region = regions[0] if len(regions) > 0 else sublime.Region(0, 0)
    text = view.substr(region)
    start_line, start_col = view.rowcol(region.begin())
    end_line, end_col = view.rowcol(region.end())
    file_name = view.file_name()
    return {
        "text": text,
        "filePath": file_name,
        "fileUrl": path_to_uri(file_name) if file_name else None,
        "selection": {
            "start": {"line": start_line, "character": start_col},
            "end": {"line": end_line, "character": end_col},
            "isEmpty": region.empty(),
        },
    }


def remember_selection(view):
    payload = selection_payload(view)
    if not payload["selection"]["isEmpty"]:
        global _latest_selection
        _latest_selection = payload
    return payload


def latest_selection():
    return _latest_selection


def get_current_selection():
    window = sublime.active_window()
    view = window.active_view() if window else None
    if view is None:
        return {"success": False, "message": "no active editor"}
    return selection_payload(view)


def get_open_editors():
    # Tabs are sheets, not views: an image tab has sheet.view() == None
    # and would be invisible to a views()-based walk.
    tabs = []
    for window in sublime.windows():
        active_sheet = window.active_sheet()
        active_id = active_sheet.id() if active_sheet else -1
        for sheet in window.sheets():
            file_name = sheet.file_name()
            if not file_name:
                continue
            view = sheet.view()
            tabs.append({
                "uri": path_to_uri(file_name),
                "isActive": sheet.id() == active_id,
                "label": os.path.basename(file_name),
                "languageId": language_id(view) if view else guess_language(file_name),
                "isDirty": view.is_dirty() if view is not None else False,
            })
    return {"tabs": tabs}


def get_workspace_folders():
    folders = [
        {"name": os.path.basename(f) or f, "uri": path_to_uri(f), "path": f}
        for f in all_workspace_folders()
    ]
    root = folders[0]["path"] if folders else ""
    return {"success": True, "folders": folders, "rootPath": root}


def check_document_dirty(file_path):
    view = find_view(file_path)
    if view is None:
        return {"success": False, "message": "Document not open: {}".format(file_path)}
    return {"success": True, "filePath": file_path, "isDirty": view.is_dirty(), "isUntitled": False}


def save_document(file_path):
    view = find_view(file_path)
    if view is None:
        return {"success": False, "message": "Document not open: {}".format(file_path)}
    view.run_command("save")
    return {"success": True, "filePath": file_path, "saved": True}


def open_file(file_path, preview=False, start_text=None, end_text=None,
              select_to_eol=False, make_frontmost=True):
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    window = sublime.active_window()
    flags = sublime.TRANSIENT if preview else 0
    group = side_group(window)
    view = window.open_file(file_path, flags, group=group)

    # Track this view as opened by AgentIDE
    if not preview:
        track_agentIDE_view(window, view)

    focus_behavior = settings().get("focus_behavior", "new_content")

    if focus_behavior == "new_content" and make_frontmost:
        window.focus_view(view)
        if hasattr(window, "bring_to_front"):
            window.bring_to_front()
    elif focus_behavior == "alternate" and window.active_view() != view:
        window.focus_view(view)
    # "keep_current" (or "alternate" when the new view is already active): no-op

    _select_when_loaded(view, start_text, end_text, select_to_eol)
    return view


def _select_when_loaded(view, start_text, end_text, to_eol, tries=100):
    if not start_text:
        return

    def attempt():
        if view.is_loading():
            if tries > 0:
                sublime.set_timeout(
                    lambda: _select_when_loaded(view, start_text, end_text, to_eol, tries - 1), 50)
            return
        start_region = view.find(start_text, 0, sublime.LITERAL)
        if start_region is None or start_region.a == -1:
            return
        end_point = start_region.b
        if end_text:
            end_region = view.find(end_text, start_region.b, sublime.LITERAL)
            if end_region is not None and end_region.a != -1:
                end_point = end_region.b
        if to_eol:
            end_point = view.line(end_point).b
        selection = sublime.Region(start_region.a, end_point)
        view.sel().clear()
        view.sel().add(selection)
        view.show_at_center(selection)

    attempt()
