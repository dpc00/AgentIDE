"""Scan the desktop situation and report it; touch nothing.

Not tied to Sublime or to any plugin: it enumerates every top-level window on this machine through
Win32, classifies what it sees, and emits what changed plus one line a reader can act on.

    python scan.py [--interval 0.5] [--all] [--dialogs-only]

Output (JSONL, one object per change):
    {"event":"scan", "t":…, "foreground":{"exe":"sublime_text.exe","title":"…"},
     "idle_ms":1234, "dialogs":[{"exe":…, "title":…, "buttons":[…], "status":"Dialog is up: …"}],
     "windows_added":[…], "windows_removed":[…]}

A dialog here is any top-level visible window that behaves like one: class #32770, or a window with
an owner and a dialog frame — across every process, so a save prompt in an editor and an installer's
error box are the same kind of fact.

It never clicks, focuses, or answers anything: reporting is the whole job.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import sys
import time


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


_ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_GA_ROOT = 2
_GW_OWNER = 4
_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_DLGFRAME = 0x00400000
_WM_GETTEXT = 0x000D


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def user32():
    return ctypes.windll.user32


def kernel32():
    return ctypes.windll.kernel32


def process_table():
    """pid -> (parent pid, exe name) for every process, from one Toolhelp snapshot."""
    snap = kernel32().CreateToolhelp32Snapshot(0x2, 0)
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    table = {}
    try:
        if kernel32().Process32First(snap, ctypes.byref(entry)):
            while True:
                table[entry.th32ProcessID] = (entry.th32ParentProcessID,
                                              entry.szExeFile.decode("mbcs", "replace"))
                if not kernel32().Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32().CloseHandle(snap)
    return table


def text_of(hwnd):
    buffer = ctypes.create_unicode_buffer(512)
    user32().SendMessageW(hwnd, _WM_GETTEXT, 512, ctypes.byref(buffer))
    return buffer.value


def class_of(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32().GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def child_texts(hwnd, classes):
    found = []

    def _visit(child, _lparam):
        if class_of(child) in classes:
            text = text_of(child).replace("&", "").strip()
            if text:
                found.append(text)
        return True

    user32().EnumChildWindows(hwnd, _ENUMPROC(_visit), 0)
    return found


def window_pid(hwnd):
    pid = wt.DWORD()
    user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def idle_ms():
    """Milliseconds since the last keyboard or mouse input anywhere on the desktop."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not user32().GetLastInputInfo(ctypes.byref(info)):
        return None
    return int(kernel32().GetTickCount() - info.dwTime)


def status_line(title, messages, buttons):
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


SHELL_CLASSES = {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "tooltips_class32",
    "ThumbnailDeviceHelperWnd", "XamlExplorerHostIslandWindow_WASDK", "TaskListThumbnailWnd",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow",
}


def snapshot(exes):
    """Every top-level visible window worth reporting. Never touches another app's threads."""
    u = user32()
    rows = []

    def _visit(hwnd, _lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        title = text_of(hwnd).strip()
        style = u.GetWindowLongW(hwnd, _GWL_STYLE) & 0xFFFFFFFF
        owner = bool(u.GetWindow(hwnd, _GW_OWNER))
        cls = class_of(hwnd)
        pid = window_pid(hwnd)
        parent_pid, exe = exes.get(pid, (None, ""))
        dialog_like = cls == "#32770" or (owner and (style & _WS_DLGFRAME))
        if cls in SHELL_CLASSES and not dialog_like:
            return True
        if not title and not dialog_like:
            return True  # an untitled, ordinary window is not a situation fact
        entry = {
            "hwnd": int(hwnd),
            "pid": pid,
            "exe": exe,
            "class": cls,
            "title": title,
            "owner": owner,
            "dialog": bool(dialog_like),
        }
        if dialog_like:
            entry["buttons"] = child_texts(hwnd, ("Button",))
            entry["messages"] = child_texts(hwnd, ("Static", "Edit"))
            entry["status"] = status_line(title, entry["messages"], entry["buttons"])
        rows.append(entry)
        return True

    u.EnumWindows(_ENUMPROC(_visit), 0)
    return rows


def describe(rows):
    """(windows, dialogs) with the verbose child lists kept only for dialogs."""
    dialogs = [w for w in rows if w["dialog"]]
    light = [{k: v for k, v in w.items() if k not in ("buttons", "messages", "status")} for w in rows]
    return light, dialogs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--all", action="store_true", help="print a full snapshot every tick, not just changes")
    args = ap.parse_args()

    started = time.time()

    def emit(**rec):
        rec["t"] = round(time.time() - started, 3)
        print(json.dumps(rec), flush=True)

    previous_keys = set()
    previous_fingerprint = None
    while True:
        exes = process_table()
        rows = snapshot(exes)
        light, dialogs = describe(rows)
        keys = {w["hwnd"] for w in light}
        added = [w for w in light if w["hwnd"] not in previous_keys]
        removed = sorted(previous_keys - keys)
        previous_keys = keys

        foreground = None
        fg = user32().GetForegroundWindow()
        if fg:
            pid = window_pid(fg)
            _parent, exe = exes.get(pid, (None, ""))
            foreground = {"hwnd": int(fg), "pid": pid, "exe": exe, "title": text_of(fg).strip()}

        # Emit on change only: a dialog that simply stays up is not news every 300 ms.
        fingerprint = (
            tuple(sorted(d["hwnd"] for d in dialogs)),
            foreground["hwnd"] if foreground else None,
            None if idle_ms() is None else idle_ms() // 5000,
            tuple(sorted(w["hwnd"] for w in light)),
        )
        if not args.all and fingerprint == previous_fingerprint:
            time.sleep(args.interval)
            continue
        previous_fingerprint = fingerprint
        if args.all or added or removed or dialogs:
            emit(event="scan", foreground=foreground, idle_ms=idle_ms(), dialogs=dialogs,
                 windows_added=added, windows_removed=removed,
                 window_count=len(light))
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
