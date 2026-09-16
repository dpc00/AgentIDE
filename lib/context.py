"""Editor-state tools: selection, open editors, workspace folders, and
opening/saving files -- the read-mostly half of the protocol, as opposed
to diff_view.py's blocking review flow.

All functions here run on the main thread; callers marshal.
"""

import os

import sublime

from .pathurl import path_to_uri

_latest_selection = None  # last non-empty selection payload seen, any view
_saved_layouts = {}  # window id -> saved layout dict
_layout_backups = {}  # window id -> list of backup layouts
_agentide_views = {}  # window id -> set of view IDs opened by AgentIDE


def settings():
    return sublime.load_settings("AgentIDE.sublime-settings")


def side_group(window):
    """Group index for tabs AgentIDE opens: a right-hand pane, so the
    user's own tabs stay untouched. Creates the split on first use.
    Returns -1 (active group) if the setting is off."""
    if not settings().get("open_in_side_group", True):
        return -1

    layout_settings = settings().get("layout", {})
    use_new_window = layout_settings.get("use_new_window", False)

    if use_new_window:
        return -1  # Let caller handle new window creation

    if window.num_groups() == 1:
        # Check window size constraints
        min_size = layout_settings.get("min_window_size", 400)
        max_size = layout_settings.get("max_window_size")
        window_rect = window.active_view().viewport_position()
        # Simple check - if window is too small, don't split
        if window_rect and len(window_rect) >= 2:
            width = window.active_view().layout_extent()[0]
            if width < min_size:
                return -1  # Window too small for split
            if max_size and width > max_size:
                return -1  # Window too large for split

        # Save original layout if restore is enabled
        if layout_settings.get("restore_on_close", True):
            window_id = window.id()
            if window_id not in _saved_layouts:
                _saved_layouts[window_id] = window.get_layout()

        # Apply split based on settings
        split_position = layout_settings.get("split_position", "right")
        split_size = layout_settings.get("split_size", 0.45)
        split_orientation = layout_settings.get("split_orientation", "vertical")

        # Adaptive layout adjustments
        if layout_settings.get("adaptive_layouts", True):
            # Adjust split size based on window width
            try:
                width = window.active_view().layout_extent()[0]
                if width < 1200:
                    split_size = max(0.3, split_size * 0.8)  # Smaller split on small screens
                elif width > 2000:
                    split_size = min(0.6, split_size * 1.2)  # Larger split on large screens
            except Exception:
                pass  # If we can't get width, use default

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

        window.set_layout({
            "cols": cols,
            "rows": rows,
            "cells": cells,
        })
    return window.num_groups() - 1


def restore_layout(window):
    """Restore the original window layout if it was saved."""
    layout_settings = settings().get("layout", {})
    if not layout_settings.get("restore_on_close", True):
        return

    restore_timing = layout_settings.get("restore_timing", "when_empty")
    if restore_timing == "never":
        return

    window_id = window.id()
    if window_id not in _saved_layouts:
        return

    original_layout = _saved_layouts[window_id]

    # Check if we should preserve manual changes
    if not layout_settings.get("preserve_manual_changes", False):
        current_layout = window.get_layout()
        if current_layout != original_layout:
            # User manually changed layout, don't restore
            return

    # Validate layout before restore if enabled
    if layout_settings.get("validate_on_restore", True):
        if not _validate_layout(original_layout):
            # Use fallback layout if validation fails
            fallback = layout_settings.get("fallback_layout")
            if fallback:
                window.set_layout(fallback)
            else:
                # If no fallback, keep current layout
                return

    # Create backup before restore if enabled
    if layout_settings.get("backup_layouts", True):
        _backup_layout(window_id, window.get_layout())

    # Apply restore delay if specified
    delay = layout_settings.get("restore_delay", 0)
    if delay > 0:
        sublime.set_timeout(lambda: _apply_restore(window, original_layout, window_id), delay)
    else:
        _apply_restore(window, original_layout, window_id)


def _apply_restore(window, layout, window_id):
    """Actually apply the layout restore."""
    try:
        window.set_layout(layout)
        del _saved_layouts[window_id]
    except Exception:
        # Error handling based on settings
        layout_settings = settings().get("layout", {})
        error_handling = layout_settings.get("error_handling", "warn")
        if error_handling == "warn":
            sublime.status_message("AgentIDE: warning - layout restore failed")
        elif error_handling == "error":
            sublime.error_message("AgentIDE: layout restore failed")


def _validate_layout(layout):
    """Validate that a layout dict is well-formed."""
    if not isinstance(layout, dict):
        return False
    required_keys = {"cols", "rows", "cells"}
    if not required_keys.issubset(layout.keys()):
        return False
    return True


def _backup_layout(window_id, layout):
    """Create a backup of the current layout."""
    layout_settings = settings().get("layout", {})
    max_backups = layout_settings.get("max_backup_versions", 5)

    if window_id not in _layout_backups:
        _layout_backups[window_id] = []

    backups = _layout_backups[window_id]
    backups.append(layout)

    # Limit backup size
    if len(backups) > max_backups:
        _layout_backups[window_id] = backups[-max_backups:]


def track_agentide_view(window, view):
    """Track that a view was opened by AgentIDE."""
    window_id = window.id()
    if window_id not in _agentide_views:
        _agentide_views[window_id] = set()
    _agentide_views[window_id].add(view.id())


def untrack_agentide_view(window, view):
    """Stop tracking an AgentIDE-opened view."""
    window_id = window.id()
    if window_id in _agentide_views:
        _agentide_views[window_id].discard(view.id())
        if not _agentide_views[window_id]:
            del _agentide_views[window_id]


def has_agentide_views(window):
    """Check if window has any AgentIDE-opened views."""
    window_id = window.id()
    return window_id in _agentide_views and bool(_agentide_views[window_id])


def cleanup_layouts():
    """Clean up saved layouts for windows that no longer exist."""
    layout_settings = settings().get("layout", {})
    if not layout_settings.get("cleanup_on_exit", True):
        return

    # Get all current window IDs
    current_window_ids = {window.id() for window in sublime.windows()}

    # Clean up saved layouts for non-existent windows
    for window_id in list(_saved_layouts.keys()):
        if window_id not in current_window_ids:
            del _saved_layouts[window_id]

    # Clean up backups for non-existent windows
    for window_id in list(_layout_backups.keys()):
        if window_id not in current_window_ids:
            del _layout_backups[window_id]

    # Clean up agentide view tracking for non-existent windows
    for window_id in list(_agentide_views.keys()):
        if window_id not in current_window_ids:
            del _agentide_views[window_id]

    # Layout garbage collection if enabled
    gc_hours = layout_settings.get("layout_gc_hours", 24)
    if gc_hours > 0:
        import time
        current_time = time.time()
        gc_threshold = current_time - (gc_hours * 3600)
        # Simple timestamp-based cleanup could be added here
        # For now, we just clean up non-existent windows


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
        track_agentide_view(window, view)

    # Handle focus behavior based on settings
    layout_settings = settings().get("layout", {})
    focus_behavior = layout_settings.get("focus_behavior", "new_content")

    if focus_behavior == "new_content":
        # Focus new content (default behavior)
        if make_frontmost:
            window.focus_view(view)
            if hasattr(window, "bring_to_front"):
                window.bring_to_front()
    elif focus_behavior == "keep_current":
        # Keep current focus, don't switch to new content
        pass
    elif focus_behavior == "alternate":
        # Alternate between current and new
        current_view = window.active_view()
        if current_view != view:
            window.focus_view(view)
        else:
            # Already focused, keep it
            pass

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
