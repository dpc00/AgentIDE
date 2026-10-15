"""getDiagnostics from the LSP package.

Sublime Text has no diagnostics of its own, so there is something to report only when the LSP package
(sublimelsp/LSP) is loaded and a language server has published some. This module never imports LSP: it
looks at the module LSP already registered in sys.modules and reads each session's DiagnosticsStorage, whose
entries are the raw LSP Diagnostic objects the server sent. No LSP, no session or no diagnostics gives [].

The result has the shape the VS Code extension returns: a list with one {uri, diagnostics} entry per file
that has any, each diagnostic {message, severity, range, source, code} with LSP's 0-based line and character
and severity as a word (Error, Warning, Information, Hint).
"""

import os
import queue
import sys

import sublime

from .pathurl import path_to_uri, uri_to_path

_SEVERITY = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}
_LSP_REGISTRY = "LSP.plugin.core.registry"


def lsp_loaded():
    return getattr(sys.modules.get(_LSP_REGISTRY), "windows", None) is not None


def _same_file(a, b):
    """True when two URIs name the same file (drive letter case, %3A and slash style do not matter)."""
    try:
        return os.path.normcase(uri_to_path(a)) == os.path.normcase(uri_to_path(b))
    except ValueError:
        return a == b


def _display_uri(uri):
    """A file URI in the same spelling the other AgentIDE tools use; anything else unchanged."""
    try:
        return path_to_uri(uri_to_path(uri))
    except ValueError:
        return uri


def _diagnostic(raw):
    code = raw.get("code")
    return {
        "message": raw.get("message", ""),
        "severity": _SEVERITY.get(raw.get("severity", 1), "Error"),
        "range": raw.get("range") or {},
        "source": raw.get("source") or "",
        "code": "" if code is None else str(code),
    }


def _collect(uri):
    """Runs on Sublime's async thread, where LSP itself reads and changes this state."""
    registry = getattr(sys.modules.get(_LSP_REGISTRY), "windows", None)
    if registry is None:
        return []
    by_file = {}
    for window in sublime.windows():
        manager = registry.lookup(window)
        if manager is None:
            continue
        for session in manager.get_sessions():
            storage = getattr(session, "diagnostics", None)
            if storage is None:
                continue
            for file_uri, diagnostics in storage.get_diagnostics().items():
                if not diagnostics or (uri and not _same_file(file_uri, uri)):
                    continue
                entry = by_file.setdefault(_display_uri(file_uri), [])
                entry.extend(_diagnostic(d) for d in diagnostics)
    return [{"uri": file_uri, "diagnostics": found} for file_uri, found in sorted(by_file.items())]


def get_diagnostics(uri=None, timeout=5.0):
    """The payload for the getDiagnostics tool; called from a server thread, so the read is handed to
    Sublime's async thread and waited for."""
    if not lsp_loaded():
        return []
    answer = queue.Queue()

    def work():
        try:
            answer.put(("ok", _collect(uri)))
        except Exception as error:  # noqa: BLE001 - handed back to the caller
            answer.put(("error", error))

    sublime.set_timeout_async(work, 0)
    kind, value = answer.get(timeout=timeout)
    if kind == "error":
        raise value
    return value
