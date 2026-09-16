"""AgentIDE: a standalone Sublime Text bridge to any IDE-protocol agent CLI.

First slice, deliberately narrow: start a WebSocket+MCP server, write the
discovery lock file, and prove a real agent CLI's `/ide` command can
connect and report itself connected. No tools registered yet, no diff
review, no context sharing — those come once this handshake is verified
end-to-end against a live `claude` process.
"""

import json
import os
import queue
import threading

import sublime
import sublime_plugin

from .lib import lockfile
from .lib.mcp import MCPServer
from .lib.wsserver import WSServer

SETTINGS_FILE = "AgentIDE.sublime-settings"
STATUS_KEY = "zz_agentide"
VERSION = "0.0.1"

_main_thread = None

_state = {
    "server": None,      # type: WSServer | None
    "mcp": None,         # type: MCPServer | None
    "token": None,
    "port": None,
    "connected": False,
}


def settings():
    return sublime.load_settings(SETTINGS_FILE)


def log(msg):
    if settings().get("debug", True):
        print("[AgentIDE] {}".format(msg))


def run_on_main(fn, timeout=5.0):
    """Run fn on the UI thread and return its result; safe to call from a
    reader/accept thread. Calls straight through when already on main."""
    if threading.current_thread() is _main_thread:
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
    port = server.start()

    folders = _all_workspace_folders()
    lockfile.write_lock(
        port=port, pid=os.getpid(), workspace_folders=folders,
        auth_token=token, ide_name="Sublime Text",
    )

    _state.update({"server": server, "mcp": mcp, "token": token, "port": port, "connected": False})
    log("started on port {} (lock written at {})".format(port, lockfile.lock_path(port)))
    _refresh_status_bar()
    return port


def stop():
    server = _state["server"]
    port = _state["port"]
    if server is not None:
        server.stop()
    if port is not None:
        lockfile.remove_lock(port)
    _state.update({"server": None, "mcp": None, "token": None, "port": None, "connected": False})
    _refresh_status_bar()
    log("stopped")


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
    global _main_thread
    _main_thread = threading.current_thread()
    start()


def plugin_unloaded():
    stop()
