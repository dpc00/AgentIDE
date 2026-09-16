"""Editor-state tools: selection, open editors, workspace folders, and
opening/saving files -- the read-mostly half of the protocol, as opposed
to diff_view.py's blocking review flow.

All functions here run on the main thread; callers marshal.
"""

import os

import sublime

from .pathurl import path_to_uri

_latest_selection = None  # last non-empty selection payload seen, any view


def settings():
    return sublime.load_settings("AgentIDE.sublime-settings")


def side_group(window):
    """Group index for tabs AgentIDE opens: a right-hand pane, so the
    user's own tabs stay untouched. Creates the split on first use.
    Returns -1 (active group) if the setting is off."""
    if not settings().get("open_in_side_group", True):
        return -1
    if window.num_groups() == 1:
        window.set_layout({
            "cols": [0.0, 0.55, 1.0], "rows": [0.0, 1.0],
            "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
        })
    return window.num_groups() - 1


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
    if make_frontmost:
        window.focus_view(view)
        if hasattr(window, "bring_to_front"):
            window.bring_to_front()
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
