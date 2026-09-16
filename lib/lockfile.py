"""Discovery lock file at ~/.claude/ide/<port>.lock — how a CLI's `/ide`
command and CLAUDE_CODE_SSE_PORT auto-connect find this server and its
auth token."""

import json
import os
import secrets


def generate_token():
    return secrets.token_hex(16)


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
