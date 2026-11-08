"""Watch for native dialogs of the editor and report them; never answer them.

Claude Code's /ide protocol has selection_changed and at_mentioned; this module feeds a third
notification, dialog_changed, so the agent knows a dialog is up ("Dialog is up: 'Open'. It says:
'File name: …'. 3 buttons: Open, Cancel, Help.") instead of stalling on it.

Why it is not a Sublime API call: Sublime has no event for a native dialog. The signal is Win32's
EVENT_OBJECT_SHOW on a top-level, visible window of class #32770 owned by the editor's process tree
(the dialogs are raised by sublime_text.exe *and* by plugin_host-x.y.exe, so the tree is what matters).

Measured on build 4215 (2026-11-08): a file dialog is created ~314 ms before it is shown, invisible
and with its buttons still being created, so SHOW is the first moment reporting it is accurate.
Reporting latency from SHOW is under a millisecond; the answer is the agent's decision, not ours.

Pure ctypes, standard library only. Runs on its own thread with a message pump, so the caller keeps
its own threads and the plugin main thread is never touched.
"""

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

_EVENT_OBJECT_SHOW = 0x8002
_EVENT_OBJECT_DESTROY = 0x8001
_WINEVENT_OUTOFCONTEXT = 0x0000
_WM_GETTEXT = 0x000D
_GA_ROOT = 2

_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD
)
_ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _user32():
    return ctypes.windll.user32


def _process_tree(root_pid):
    """root_pid plus every descendant, from a Toolhelp snapshot (Windows only)."""
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    parents = {}
    try:
        if kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    keep = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in keep and pid not in keep:
                keep.add(pid)
                changed = True
    return keep


def _class_of(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _title_of(hwnd):
    length = _user32().SendMessageW(hwnd, _WM_GETTEXT, 512, 0)
    buffer = ctypes.create_unicode_buffer(512)
    _user32().SendMessageW(hwnd, _WM_GETTEXT, 512, ctypes.byref(buffer))
    return buffer.value


def _child_texts(hwnd, classes):
    """Text of the dialog's children whose window class is in `classes`, in creation order."""
    found = []

    def _visit(child, _lparam):
        if _class_of(child) in classes:
            text = _title_of(child).replace("&", "").strip()
            if text:
                found.append(text)
        return True

    _user32().EnumChildWindows(hwnd, _ENUMPROC(_visit), 0)
    return found


def describe(title, messages, buttons, hwnd):
    """The one line an agent can act on. Wording is deliberate: what is up, what it says, what can be pressed."""
    said = " / ".join(" ".join(m.split()) for m in messages) if messages else ""
    if len(said) > 300:
        said = said[:297] + "..."
    parts = ["Dialog is up: {!r}.".format(title)] if title else ["Dialog is up."]
    if said:
        parts.append("It says: {!r}.".format(said))
    if buttons:
        parts.append("{} button{}: {}.".format(len(buttons), "" if len(buttons) == 1 else "s",
                                               ", ".join(buttons)))
    else:
        parts.append("No buttons found.")
    return " ".join(parts)


def root_of(pid):
    """The topmost ancestor of `pid` (the editor process), so a watcher can cover the whole tree.

    Called with the plugin host's own pid: dialogs are raised by sublime_text.exe *and* by
    plugin_host-x.y.exe, so watching only this process would miss half of them."""
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    parents = {}
    try:
        if kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    seen = set()
    while pid in parents and parents[pid] not in (0, pid) and parents[pid] not in seen:
        seen.add(pid)
        pid = parents[pid]
    return pid


class DialogWatcher:
    """Reports dialogs opened or closed by the editor's process tree.

    on_event(payload) is called from this watcher's own thread; make it cheap and thread-safe
    (AgentIDE's _notify only serialises and broadcasts).
    """

    def __init__(self, root_pid, on_event):
        self.root_pid = root_pid
        self.on_event = on_event
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._pids = set()
        self._seen = set()

    # -- lifetime ---------------------------------------------------------------------------
    def start(self, timeout=2.0):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="AgentIDE-dialogwatch", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)

    def stop(self):
        self._stop.set()

    # -- internals --------------------------------------------------------------------------
    def _run(self):
        user32 = _user32()
        self._pids = _process_tree(self.root_pid)
        callback = _WINEVENTPROC(self._on_win_event)
        hooks = [
            user32.SetWinEventHook(_EVENT_OBJECT_SHOW, _EVENT_OBJECT_SHOW, 0, callback, 0, 0,
                                   _WINEVENT_OUTOFCONTEXT),
            user32.SetWinEventHook(_EVENT_OBJECT_DESTROY, _EVENT_OBJECT_DESTROY, 0, callback, 0, 0,
                                   _WINEVENT_OUTOFCONTEXT),
        ]
        self._hooks = hooks
        self._callback = callback  # keep a reference: a collected callback would crash the hook
        self._ready.set()

        message = wt.MSG()
        refresh = time.time()
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(message), 0, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            if time.time() - refresh > 1.0:
                self._pids = _process_tree(self.root_pid)
                refresh = time.time()
            time.sleep(0.002)
        for hook in hooks:
            if hook:
                user32.UnhookWinEvent(hook)

    def _on_win_event(self, hook, event, hwnd, id_object, id_child, thread, when):
        if not hwnd:
            return
        user32 = _user32()
        try:
            if user32.GetAncestor(hwnd, _GA_ROOT) != hwnd:
                return
            window_pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value not in self._pids:
                return
            if event == _EVENT_OBJECT_DESTROY:
                hwnd = int(hwnd)
                if hwnd in self._seen:
                    self._seen.discard(hwnd)
                    self.on_event({"state": "closed", "hwnd": hwnd, "pid": window_pid.value})
                return
            if _class_of(hwnd) != "#32770" or not user32.IsWindowVisible(hwnd):
                return
            hwnd = int(hwnd)
            if hwnd in self._seen:
                return
            self._seen.add(hwnd)
            title = _title_of(hwnd)
            buttons = _child_texts(hwnd, ("Button",))
            messages = _child_texts(hwnd, ("Static", "Edit"))
            self.on_event({
                "state": "open",
                "hwnd": hwnd,
                "pid": window_pid.value,
                "title": title,
                "buttons": buttons,
                "messages": messages,
                "status": describe(title, messages, buttons, hwnd),
            })
        except Exception as exc:  # a hook callback must never raise into the pump
            try:
                self.on_event({"state": "error", "error": repr(exc)})
            except Exception:
                pass

if sys.platform != "win32":
    class DialogWatcher:  # noqa: F811 - the API surface, inert off Windows
        """No-op off Windows: there is no native #32770 dialog to watch."""

        def __init__(self, root_pid, on_event):
            self.root_pid = root_pid
            self.on_event = on_event

        def start(self, timeout=0):
            return None

        def stop(self):
            return None
