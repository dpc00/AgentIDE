"""executeCode backend: a persistent Python subprocess, not a Jupyter
kernel. The MCP contract is `executeCode(code: string) -> content blocks`;
VS Code happens to implement that by driving a real .ipynb notebook cell
through the Jupyter extension, but nothing in the protocol requires a
notebook UI. Sublime has no notebook document model, so this backs the
same contract with a plain long-lived interpreter process instead:
state persists across calls exactly like the real tool describes, until
the process is restarted.

Never runs on Sublime's own (plugin-host) Python -- a crash or an
infinite loop in user code would take the editor down with it. Runs in
its own OS process, driven from a background thread.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

_BOOTSTRAP = r"""
import sys, io, json, contextlib, traceback

_globals = {"__name__": "__main__"}

def _run(code):
    out = io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(code, "<executeCode>", "exec"), _globals)
        ok = True
        error = None
    except BaseException:
        ok = False
        error = traceback.format_exc()
    return {"ok": ok, "stdout": out.getvalue(), "stderr": err.getvalue(), "error": error}

for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    req = json.loads(line)
    result = _run(req["code"])
    result["id"] = req["id"]
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
"""


class ExecuteSession:
    """One persistent interpreter process. Not thread-safe against
    concurrent execute() calls by design -- executeCode is meant to be
    called one at a time, like a real notebook kernel."""

    def __init__(self, python_path, cwd=None, logger=None):
        self.python_path = python_path
        self.cwd = cwd
        self._log = logger or (lambda msg: None)
        self._proc = None
        self._reader_thread = None
        self._replies = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()

    def _ensure_started(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        self._log("execute: starting {}".format(self.python_path))
        self._proc = subprocess.Popen(
            [self.python_path, "-u", "-c", _BOOTSTRAP],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        proc = self._proc
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._replies.put(json.loads(line))
            except ValueError:
                self._log("execute: unparseable reply: {!r}".format(line))

    def restart(self):
        with self._lock:
            self._kill()

    def _kill(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

    def execute(self, code, timeout=30.0):
        """Runs on a background thread. Returns a dict:
        {"ok": bool, "stdout": str, "stderr": str, "error": str|None}
        or raises TimeoutError / OSError on interpreter failure."""
        with self._lock:
            self._ensure_started()
            self._next_id += 1
            req_id = self._next_id
            try:
                self._proc.stdin.write(json.dumps({"id": req_id, "code": code}) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._kill()
                raise OSError("interpreter process died: {}".format(exc))

            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._kill()  # long-running/hung code: kill so the next call gets a fresh process
                    raise TimeoutError("executeCode timed out after {:.0f}s".format(timeout))
                try:
                    reply = self._replies.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    if self._proc.poll() is not None:
                        stderr = self._proc.stderr.read() if self._proc.stderr else ""
                        self._kill()
                        raise OSError("interpreter process exited: {}".format(stderr.strip()))
                    continue
                if reply.get("id") == req_id:
                    return reply


def discover_python(workspace_folders):
    """Prefer a project-local virtualenv over whatever's on PATH -- running
    against the wrong interpreter means every project import fails, which
    makes the feature useless. Falls back to 'python'/'python3' on PATH."""
    candidates = []
    for folder in workspace_folders or []:
        if sys.platform == "win32":
            candidates.append(os.path.join(folder, ".venv", "Scripts", "python.exe"))
            candidates.append(os.path.join(folder, "venv", "Scripts", "python.exe"))
        else:
            candidates.append(os.path.join(folder, ".venv", "bin", "python"))
            candidates.append(os.path.join(folder, "venv", "bin", "python"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return "python" if sys.platform == "win32" else "python3"