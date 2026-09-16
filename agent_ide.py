
"""AgentIDE — a standalone Sublime Text bridge to any IDE-protocol agent CLI.

Third slice: registers the selection/context tools (openFile,
getCurrentSelection, getLatestSelection, getOpenEditors,
getWorkspaceFolders, checkDocumentDirty, saveDocument, executeCode-stub)
and pushes selection_changed/at_mentioned notifications.
"""

import json
import os
import queue
import threading

import sublime
import sublime_plugin

from . import context, diff_view
from .lib import lockfile
from .lib.mcp import DEFERRED, MCPServer, ToolError, tool_text_response
from .lib.session import PendingRequests
from .lib.wsserver import WSServer

SETTINGS_FILE = "AgentIDE.sublime-settings"
VERSION = "0.0.1"

_state = {
    "server": None,      # type: WSServer | None
    "mcp": None,         # type: MCPServer | None
    "token": None,
    "port": None,
    "connected": False,
    "debounce_token": 0,
    "last_selection_sent": None,
}

_pending = PendingRequests()


def settings():
    return sublime.load_settings(SETTINGS_FILE)


def log(msg):
    if settings().get("debug", True):
        print("[AgentIDE] {}".format(msg))


def run_on_main(fn, timeout=5.0):
    """Run fn on the UI thread and return its result; safe to call from a
    reader/accept thread. Calls straight through when already on main --
    compared against Python's own threading.main_thread(), not a custom
    flag that needs plugin_loaded() to run first (a stale/unset flag here
    self-deadlocks: it schedules a callback on the very thread it's
    blocking)."""
    if threading.current_thread() is threading.main_thread():
        return fn()
    q = queue.Queue()

    def wrapper():
        try:
            q.put(("ok", fn()))
        except Exception as exc:  # noqa: BLE001 - marshalled back to the caller
            q.put(("err", exc))

    sublime.set_timeout(wrapper, 0)
    kind, value = q.get(timeout=timeout)
    if kind == "err":
        raise value
    return value


def _all_workspace_folders():
    folders = []
    for window in sublime.windows():
        for f in window.folders():
            if f not in folders:
                folders.append(f)
    return folders


def is_running():
    return _state["server"] is not None


def start():
    if is_running():
        log("already running on port {}".format(_state["port"]))
        return _state["port"]

    token = lockfile.generate_token()
    ide_name = settings().get("ide_name") or "Sublime Text"
    mcp = MCPServer(server_name=ide_name, version=VERSION, logger=log)
    _register_tools(mcp)

    port_setting = settings().get("port")
    port_range = (10000, 65535)
    if isinstance(port_setting, int) and 1024 <= port_setting <= 65535:
        port_range = (port_setting, port_setting)

    server = WSServer(
        auth_token=token,
        on_message=_on_message,
        on_connect=_on_connect,
        on_disconnect=_on_disconnect,
        port_range=port_range,
        logger=log,
    )
    try:
        port = server.start()
    except OSError:
        if port_range == (10000, 65535):
            raise
        log("fixed port {} busy, falling back to random".format(port_setting))
        server = WSServer(
            auth_token=token, on_message=_on_message, on_connect=_on_connect,
            on_disconnect=_on_disconnect, logger=log,
        )
        port = server.start()

    lockfile.prune_stale_locks()
    folders = _all_workspace_folders()
    lockfile.write_lock(
        port=port, pid=os.getpid(), workspace_folders=folders,
        auth_token=token, ide_name=ide_name,
    )

    _state.update({"server": server, "mcp": mcp, "token": token, "port": port, "connected": False})
    diff_view.set_resolver(_resolve_and_send)
    log("started on port {} (lock written at {})".format(port, lockfile.lock_path(port)))
    return port


def stop():
    server = _state["server"]
    port = _state["port"]
    for client_id, request_id in _pending.resolve_all():
        if server is not None:
            resp = tool_text_response(request_id, ["DIFF_REJECTED", "server stopping"])
            server.send_to(client_id, json.dumps(resp, ensure_ascii=False))
    try:
        run_on_main(diff_view.close_all_silent)
    except Exception as exc:  # noqa: BLE001
        log("diff cleanup on stop failed: {}".format(exc))
    if server is not None:
        server.stop()
    if port is not None:
        lockfile.remove_lock(port)
    _state.update({"server": None, "mcp": None, "token": None, "port": None, "connected": False})
    log("stopped")


def _resolve_and_send(client_id, request_id, payload):
    """Resolve a deferred tool (openDiff) and push its response to the
    session that asked, once the user accepts/rejects."""
    if not _pending.resolve(client_id, request_id):
        return
    server = _state["server"]
    if server is None:
        return
    resp = tool_text_response(request_id, payload)
    server.send_to(client_id, json.dumps(resp, ensure_ascii=False))


def _on_message(client_id, text):
    log("<- #{} {}".format(client_id, text[:300]))
    mcp = _state["mcp"]
    server = _state["server"]
    if mcp is None or server is None:
        return
    out = mcp.handle_text(text, client_id)
    if out is not None:
        log("-> #{} {}".format(client_id, out[:300]))
        server.send_to(client_id, out)


def _on_connect(client_id):
    _state["connected"] = True
    log("client #{} connected".format(client_id))


def _on_disconnect(client_id):
    _pending.resolve_all_for(client_id)  # unblock; the peer is gone, no response to send
    try:
        run_on_main(lambda: diff_view.close_for_client(client_id))
    except Exception as exc:  # noqa: BLE001
        log("diff cleanup on disconnect failed: {}".format(exc))
    server = _state["server"]
    _state["connected"] = server.client_count > 0 if server else False
    log("client #{} disconnected".format(client_id))


def _status_text():
    if not is_running():
        return ""
    if _state["connected"]:
        return "AgentIDE ⚡:{}".format(_state["port"])
    return "AgentIDE ○:{}".format(_state["port"])


def _register_tools(mcp):
    obj = {"type": "object", "properties": {}}

    mcp.register_tool(
        "openDiff", "Open a diff review (blocking until the user accepts or rejects)",
        {"type": "object", "properties": {
            "old_file_path": {"type": "string"},
            "new_file_path": {"type": "string"},
            "new_file_contents": {"type": "string"},
            "tab_name": {"type": "string"},
        }, "required": ["old_file_path", "new_file_contents", "tab_name"]},
        _tool_open_diff)

    mcp.register_tool(
        "close_tab", "Close a tab by name",
        {"type": "object", "properties": {"tab_name": {"type": "string"}},
         "required": ["tab_name"]},
        _tool_close_tab)

    mcp.register_tool(
        "closeAllDiffTabs", "Close all open diff review tabs", obj,
        lambda args, ctx: "CLOSED_{}_DIFF_TABS".format(run_on_main(diff_view.close_all)))

    mcp.register_tool(
        "getDiagnostics", "Get language diagnostics for a file",
        {"type": "object", "properties": {"uri": {"type": "string"}}},
        # Always empty for now -- Sublime has no error/warning source wired
        # in yet. Returns {"content": []}, the correct "no diagnostics"
        # shape, instead of erroring like before.
        lambda args, ctx: {"content": []})

    mcp.register_tool(
        "openFile", "Open a file in the editor and optionally select text",
        {"type": "object", "properties": {
            "filePath": {"type": "string"},
            "preview": {"type": "boolean"},
            "startText": {"type": "string"},
            "endText": {"type": "string"},
            "selectToEndOfLine": {"type": "boolean"},
            "makeFrontmost": {"type": "boolean"},
        }, "required": ["filePath"]},
        _tool_open_file)

    mcp.register_tool(
        "getCurrentSelection", "Get the current selection in the active editor", obj,
        lambda args, ctx: run_on_main(context.get_current_selection))

    mcp.register_tool(
        "getLatestSelection", "Get the most recent text selection (even from non-active editors)",
        obj, lambda args, ctx: context.latest_selection() or {
            "success": False, "message": "no selection recorded yet"})

    mcp.register_tool(
        "getOpenEditors", "List open editor tabs", obj,
        lambda args, ctx: run_on_main(context.get_open_editors))

    mcp.register_tool(
        "getWorkspaceFolders", "List workspace folders", obj,
        lambda args, ctx: run_on_main(context.get_workspace_folders))

    mcp.register_tool(
        "checkDocumentDirty", "Check whether a document has unsaved changes",
        {"type": "object", "properties": {"filePath": {"type": "string"}},
         "required": ["filePath"]},
        lambda args, ctx: run_on_main(lambda: context.check_document_dirty(args.get("filePath", ""))))

    mcp.register_tool(
        "saveDocument", "Save a document",
        {"type": "object", "properties": {"filePath": {"type": "string"}},
         "required": ["filePath"]},
        lambda args, ctx: run_on_main(lambda: context.save_document(args.get("filePath", ""))))

    mcp.register_tool(
        "executeCode", "Execute code in a Jupyter kernel", obj,
        lambda args, ctx: (_ for _ in ()).throw(
            ToolError("executeCode is not supported in Sublime Text")))


def _tool_open_file(args, ctx):
    file_path = args.get("filePath")
    make_frontmost = bool(args.get("makeFrontmost", True))

    def op():
        try:
            view = context.open_file(
                file_path, preview=bool(args.get("preview", False)),
                start_text=args.get("startText"), end_text=args.get("endText"),
                select_to_eol=bool(args.get("selectToEndOfLine", False)),
                make_frontmost=make_frontmost)
        except FileNotFoundError:
            raise ToolError("file not found: {}".format(file_path))
        sublime.status_message("AgentIDE opened: {}".format(os.path.basename(file_path)))
        return view

    view = run_on_main(op)
    if make_frontmost:
        return "Opened file: {}".format(file_path)
    return {"success": True, "filePath": file_path,
            "languageId": run_on_main(lambda: context.language_id(view))}


def _tool_open_diff(args, ctx):
    old_path = args.get("old_file_path") or ""
    new_path = args.get("new_file_path")
    contents = args.get("new_file_contents", "")
    tab_name = args.get("tab_name") or "AgentIDE diff"
    request_id = ctx["id"]
    client_id = ctx.get("client_id")

    _pending.add(client_id, request_id, {"tab_name": tab_name})
    log("openDiff deferred: client=#{} id={} tab={!r}".format(client_id, request_id, tab_name))

    def ui():
        try:
            diff_view.open_diff_ui(client_id, request_id, old_path, new_path, contents, tab_name)
        except Exception as exc:  # noqa: BLE001 - never leave a pending request orphaned
            log("openDiff UI failed: {}".format(exc))
            _resolve_and_send(client_id, request_id, ["DIFF_REJECTED", tab_name])

    sublime.set_timeout(ui, 0)
    return DEFERRED


def _tool_close_tab(args, ctx):
    tab_name = args.get("tab_name", "")

    def op():
        if diff_view.close_tab(tab_name):
            return True
        for window in sublime.windows():
            for view in window.views():
                label = view.name() or (os.path.basename(view.file_name()) if view.file_name() else "")
                if label == tab_name:
                    view.close()
                    return True
        return False

    run_on_main(op)
    return "TAB_CLOSED"


def _notify(method, params):
    """Broadcast a notification (no id, no response expected) to every
    connected session."""
    server = _state["server"]
    if server is None:
        log("_notify({}) dropped: server not running".format(method))
        return
    msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False)
    sent = server.broadcast(msg)
    log("_notify({}) -> {} client(s): {}".format(method, sent, msg[:300]))


def _no_file_payload(view):
    # Confirmed live 2026-09-15: a null filePath never actually cleared the
    # CLI's own display even though AgentIDE sent it correctly -- its
    # client only updates on a truthy filePath. The tab's own name doubles
    # as a readable non-null value here.
    tab_label = view.name() or "untitled"
    return {
        "text": "", "filePath": tab_label, "fileUrl": None, "viewName": tab_label,
        "selection": {"start": {"line": 0, "character": 0},
                      "end": {"line": 0, "character": 0}, "isEmpty": True},
    }


def _send_view_state(view):
    """Send whatever the CLI should know about this view right now --
    real-file selection or the no-file placeholder. Shared by both
    on_activated_async (switching to a tab, even with no selection
    change) and on_selection_modified_async (moving the cursor within
    the already-active tab) so either one alone keeps the CLI in sync."""
    if view.file_name() is not None and not view.settings().get("is_widget"):
        payload = context.remember_selection(view)
    else:
        payload = _no_file_payload(view)
    serialized = json.dumps(payload, sort_keys=True)
    if serialized == _state["last_selection_sent"]:
        return
    _state["last_selection_sent"] = serialized
    _notify("selection_changed", payload)


class AgentideSelectionListener(sublime_plugin.EventListener):
    def on_selection_modified_async(self, view):
        if not is_running() or not _state["connected"]:
            return
        if view.file_name() is None or view.settings().get("is_widget"):
            return

        _state["debounce_token"] += 1
        token = _state["debounce_token"]
        delay = int(settings().get("selection_debounce_ms", 200))

        def fire():
            if token != _state["debounce_token"]:
                return  # superseded by a newer selection change
            _send_view_state(view)

        sublime.set_timeout_async(fire, delay)

    def on_activated_async(self, view):
        # Switching to a tab by clicking its header, with the cursor left
        # where it was, doesn't fire on_selection_modified_async at all
        # (Sublime only fires that on an actual selection change) -- so
        # this must handle every activation itself, real file or not,
        # rather than deferring file views to the selection listener.
        if not is_running() or not _state["connected"]:
            return
        _send_view_state(view)


class AgentideAtMentionCommand(sublime_plugin.TextCommand):
    """Send the current selection to the connected agent as an @-mention."""

    def run(self, edit):
        if not is_running() or not _state["connected"]:
            sublime.status_message("AgentIDE: not connected")
            return
        view = self.view
        if view.file_name() is None:
            sublime.status_message("AgentIDE: no file for @-mention")
            return
        region = view.sel()[0] if len(view.sel()) > 0 else sublime.Region(0, 0)
        start_line, _ = view.rowcol(region.begin())
        end_line, _ = view.rowcol(region.end())
        _notify("at_mentioned", {"filePath": view.file_name(), "lineStart": start_line, "lineEnd": end_line})
        sublime.status_message(
            "AgentIDE: sent @{}#L{}-{}".format(os.path.basename(view.file_name()), start_line + 1, end_line + 1))


class AgentideReplaceContentCommand(sublime_plugin.TextCommand):
    """Replace the whole buffer -- used by diff_view.accept() when the
    target file is open with unsaved changes."""

    def run(self, edit, text):
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)


class AgentideDiffCloseListener(sublime_plugin.EventListener):
    def on_pre_close(self, view):
        diff_view.handle_view_close(view)


def launch_env_line():
    """Env-prefixed launch line for a POSIX-ish shell (git-bash)."""
    if not is_running():
        return None
    return "CLAUDE_CODE_SSE_PORT={} ENABLE_IDE_INTEGRATION=true claude".format(_state["port"])


class AgentideStatusCommand(sublime_plugin.WindowCommand):
    def run(self):
        if not is_running():
            sublime.message_dialog("AgentIDE: stopped")
            return
        conn = "connected" if _state["connected"] else "waiting for an agent CLI (/ide)"
        sublime.message_dialog(
            "AgentIDE: port {} — {}\nlock: {}\nlaunch line: {}".format(
                _state["port"], conn, lockfile.lock_path(_state["port"]), launch_env_line()))


class AgentideRestartCommand(sublime_plugin.WindowCommand):
    def run(self):
        stop()
        start()


def plugin_loaded():
    if settings().get("auto_start", True):
        start()


def plugin_unloaded():
    stop()
