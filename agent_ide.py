"""AgentIDE — a standalone Sublime Text bridge to any IDE-protocol agent CLI.

Second slice: registers openDiff (blocking diff review via ST's native
Incremental Diff engine, see diff_view.py), close_tab, and
closeAllDiffTabs. Still no selection/context sharing.
"""

import json
import os
import queue
import threading

import sublime
import sublime_plugin

from . import diff_view
from .lib import lockfile
from .lib.mcp import DEFERRED, MCPServer, tool_text_response
from .lib.session import PendingRequests
from .lib.wsserver import WSServer

SETTINGS_FILE = "AgentIDE.sublime-settings"
STATUS_KEY = "zz_agentide"
VERSION = "0.0.1"

_state = {
    "server": None,      # type: WSServer | None
    "mcp": None,         # type: MCPServer | None
    "token": None,
    "port": None,
    "connected": False,
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
    mcp = MCPServer(server_name="Sublime Text (AgentIDE)", version=VERSION, logger=log)

    server = WSServer(
        auth_token=token,
        on_message=_on_message,
        on_connect=_on_connect,
        on_disconnect=_on_disconnect,
        logger=log,
    )
    _register_tools(mcp)
    port = server.start()

    lockfile.prune_stale_locks()
    folders = _all_workspace_folders()
    lockfile.write_lock(
        port=port, pid=os.getpid(), workspace_folders=folders,
        auth_token=token, ide_name="Sublime Text",
    )

    _state.update({"server": server, "mcp": mcp, "token": token, "port": port, "connected": False})
    diff_view.set_resolver(_resolve_and_send)
    log("started on port {} (lock written at {})".format(port, lockfile.lock_path(port)))
    _refresh_status_bar()
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
    _refresh_status_bar()
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
    _refresh_status_bar()
    log("client #{} connected".format(client_id))


def _on_disconnect(client_id):
    _pending.resolve_all_for(client_id)  # unblock; the peer is gone, no response to send
    try:
        run_on_main(lambda: diff_view.close_for_client(client_id))
    except Exception as exc:  # noqa: BLE001
        log("diff cleanup on disconnect failed: {}".format(exc))
    server = _state["server"]
    _state["connected"] = server.client_count > 0 if server else False
    _refresh_status_bar()
    log("client #{} disconnected".format(client_id))


def _status_text():
    if not is_running():
        return ""
    if _state["connected"]:
        return "AgentIDE ⚡:{}".format(_state["port"])
    return "AgentIDE ○:{}".format(_state["port"])


def _refresh_status_bar():
    def op():
        text = _status_text()
        for window in sublime.windows():
            view = window.active_view()
            if view is None:
                continue
            if text:
                view.set_status(STATUS_KEY, text)
            else:
                view.erase_status(STATUS_KEY)

    try:
        run_on_main(op)
    except Exception as exc:  # noqa: BLE001
        log("status bar update failed: {}".format(exc))


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
    start()


def plugin_unloaded():
    stop()
