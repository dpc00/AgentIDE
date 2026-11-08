"""Keep an agentide-bridge session open and print every line it emits.

    python probe_bridge.py [seconds] [tool-name]

Proves the relay: handshake, then whatever the IDE pushes (selection_changed, dialog_changed).
"""

import json
import os
import subprocess
import sys
import time

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60
TOOL = sys.argv[2] if len(sys.argv) > 2 else None

proc = subprocess.Popen(
    [os.environ.get("BUN", "C:/Users/donal/.bun/bin/bun.EXE"), "run",
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.js")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
)

proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
proc.stdin.flush()
print(json.dumps({"probe": "initialized"}), flush=True)
if TOOL:
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                                 "params": {"name": TOOL, "arguments": {}}}) + "\n")
    proc.stdin.flush()

end = time.time() + DURATION
while time.time() < end:
    line = proc.stdout.readline()
    if not line:
        break
    print(line.rstrip(), flush=True)

proc.stdin.close()
proc.terminate()
