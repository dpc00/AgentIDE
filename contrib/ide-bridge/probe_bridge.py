"""Keep an agentide-bridge session open and print every line it emits.

    python probe_bridge.py [seconds]

Proves the relay: handshake, then whatever the IDE pushes (selection_changed, dialog_changed).
"""

import json
import subprocess
import sys
import time

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60

proc = subprocess.Popen(
    ["C:/Users/donal/.bun/bin/bun.EXE", "run", "C:/Users/donal/tools/agentide-bridge/bridge.js"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
)

proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
proc.stdin.flush()
print(json.dumps({"probe": "initialized"}), flush=True)

end = time.time() + DURATION
while time.time() < end:
    line = proc.stdout.readline()
    if not line:
        break
    print(line.rstrip(), flush=True)

proc.stdin.close()
proc.terminate()
