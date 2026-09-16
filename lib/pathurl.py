"""Path <-> file:// URI conversion, deterministic across host OSes.

Inspects the path shape (Windows drive letter vs POSIX) instead of
relying on os-specific helpers, so it behaves the same regardless of
which platform ST is running on.
"""

import re
from urllib.parse import quote, unquote, urlparse

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_URI_WIN_DRIVE = re.compile(r"^/([A-Za-z]):(/|$)")


def path_to_uri(path):
    if _WIN_DRIVE.match(path):
        drive = path[0].upper()
        rest = path[2:].replace("\\", "/")
        return "file:///" + quote(drive + ":" + rest, safe="/:")
    return "file://" + quote(path, safe="/")


def uri_to_path(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("not a file URI: {}".format(uri))
    p = unquote(parsed.path)
    m = _URI_WIN_DRIVE.match(p)
    if m:
        drive = m.group(1).upper()
        return drive + ":" + p[len("/X:"):].replace("/", "\\")
    return p
