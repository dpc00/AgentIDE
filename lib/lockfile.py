"""Discovery lock file at ~/.claude/ide/<port>.lock — how a CLI's `/ide`
command and CLAUDE_CODE_SSE_PORT auto-connect find this server and its
auth token."""

import json
import os
import secrets
import sys


def generate_token():
    return secrets.token_hex(16)


def _pid_alive(pid):
    """True if a process with this pid is currently running. On Windows,
    os.kill(pid, 0) doesn't reliably detect liveness, so this uses
    OpenProcess directly."""
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but we lack permission to signal it


def prune_stale_locks(directory=None):
    """Remove lock files left behind by a process that's no longer running
    (e.g. ST didn't get a chance to call plugin_unloaded on a hard restart).
    Best-effort: a lock whose owning process exited a moment after this
    check still lingers until the next prune — see [[ide_stale_lock_race_on_restart]]
    for the same race in a different implementation."""
    directory = directory or lock_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.endswith(".lock"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            pid = payload.get("pid")
        except (OSError, ValueError):
            continue
        if not isinstance(pid, int) or not _pid_alive(pid):
            try:
                os.remove(path)
            except OSError:
                pass


def lock_dir():
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = config_dir if config_dir else os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "ide")


def lock_path(port, directory=None):
    return os.path.join(directory or lock_dir(), "{}.lock".format(port))


def write_lock(port, pid, workspace_folders, auth_token, ide_name="Sublime Text", directory=None):
    directory = directory or lock_dir()
    os.makedirs(directory, exist_ok=True)
    path = lock_path(port, directory)
    payload = {
        "pid": pid,
        "workspaceFolders": workspace_folders,
        "ideName": ide_name,
        "transport": "ws",
        "authToken": auth_token,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def remove_lock(port, directory=None):
    try:
        os.remove(lock_path(port, directory))
    except OSError:
        pass
