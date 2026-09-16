"""Bare-bones RFC 6455 WebSocket server, standard library only.

Sublime's plugin host has no asyncio event loop worth building on and no
bundled websockets package, so this runs the accept loop and each
connection's read loop on plain daemon threads. Scope is exactly what the
IDE protocol needs: a loopback listener, one upgrade handshake with a
bearer-style auth header, text frames, ping/pong, and a clean close.
"""

import base64
import hashlib
import hmac
import os
import random
import socket
import struct
import threading

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Claude Code's CLI sends this exact header on /ide connect; a generic
# client speaking the same lock-file protocol may send either name.
AUTH_HEADER = "x-claude-code-ide-authorization"

_OP_CONT, _OP_TEXT, _OP_BINARY, _OP_CLOSE, _OP_PING, _OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
_MAX_HANDSHAKE_BYTES = 64 * 1024
_MAX_FRAME_BYTES = 64 * 1024 * 1024


def _accept_key(client_key):
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _parse_request_head(raw):
    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return headers


def _handshake_response(client_key):
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: {}".format(_accept_key(client_key)),
        "", "",
    ]
    return "\r\n".join(lines).encode("ascii")


def _reject_response(status=401, reason="Unauthorized"):
    body = reason.encode("ascii")
    head = "HTTP/1.1 {} {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n".format(
        status, reason, len(body))
    return head.encode("ascii") + body


def encode_frame(opcode, payload):
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + payload


def iter_frames(buf):
    """Pop as many complete frames as are buffered; mutates buf in place."""
    frames = []
    while True:
        if len(buf) < 2:
            return frames
        b0, b1 = buf[0], buf[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        pos = 2
        if length == 126:
            if len(buf) < 4:
                return frames
            length = struct.unpack("!H", bytes(buf[2:4]))[0]
            pos = 4
        elif length == 127:
            if len(buf) < 10:
                return frames
            length = struct.unpack("!Q", bytes(buf[2:10]))[0]
            pos = 10
        if length > _MAX_FRAME_BYTES:
            raise ValueError("frame too large: {}".format(length))
        needed = pos + (4 if masked else 0) + length
        if len(buf) < needed:
            return frames
        if masked:
            key = bytes(buf[pos:pos + 4])
            pos += 4
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(buf[pos:pos + length]))
        else:
            payload = bytes(buf[pos:pos + length])
        del buf[:needed]
        frames.append((fin, opcode, payload))


class WSServer:
    """Threaded, localhost-only WebSocket server. Several agent CLI
    sessions may be connected at once; each gets an integer client_id."""

    def __init__(self, auth_token, on_message, on_connect=None, on_disconnect=None,
                 port_range=(10000, 65535), logger=None):
        self._auth_token = auth_token
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._port_range = port_range
        self._log = logger or (lambda msg: None)

        self._listener = None
        self._clients = {}
        self._clients_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._next_id = 0
        self._running = False
        self.port = None

    def start(self):
        lo, hi = self._port_range
        last_error = None
        for _ in range(64):
            candidate_port = random.randint(lo, hi)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", candidate_port))
            except OSError as exc:
                last_error = exc
                sock.close()
                continue
            sock.listen(5)
            sock.settimeout(0.5)
            self._listener = sock
            self.port = candidate_port
            break
        if self._listener is None:
            raise OSError("could not bind a port in {}-{}: {}".format(lo, hi, last_error))

        self._running = True
        threading.Thread(target=self._accept_loop, name="agentide-accept", daemon=True).start()
        self._log("listening on 127.0.0.1:{}".format(self.port))
        return self.port

    def stop(self):
        self._running = False
        with self._clients_lock:
            client_ids = list(self._clients.keys())
        for client_id in client_ids:
            self._drop_client(client_id, notify=False)
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None

    @property
    def client_count(self):
        with self._clients_lock:
            return len(self._clients)

    def send_to(self, client_id, text):
        with self._clients_lock:
            conn = self._clients.get(client_id)
        if conn is None:
            return False
        with self._send_lock:
            try:
                conn.sendall(encode_frame(_OP_TEXT, text.encode("utf-8")))
                return True
            except OSError as exc:
                self._log("send to #{} failed: {}".format(client_id, exc))
        self._drop_client(client_id)
        return False

    def broadcast(self, text):
        with self._clients_lock:
            client_ids = list(self._clients.keys())
        return sum(1 for cid in client_ids if self.send_to(cid, text))

    def _accept_loop(self):
        while self._running and self._listener is not None:
            try:
                conn, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._log("connection from {}".format(addr))
            threading.Thread(target=self._client_loop, args=(conn,),
                              name="agentide-client", daemon=True).start()

    def _handshake(self, conn):
        conn.settimeout(5)
        raw = b""
        try:
            while b"\r\n\r\n" not in raw and len(raw) < _MAX_HANDSHAKE_BYTES:
                chunk = conn.recv(4096)
                if not chunk:
                    return False
                raw += chunk
        except OSError:
            return False

        headers = _parse_request_head(raw)
        client_key = headers.get("sec-websocket-key")
        supplied_token = headers.get(AUTH_HEADER, "")
        is_upgrade = headers.get("upgrade", "").lower() == "websocket"
        is_authorized = bool(supplied_token) and hmac.compare_digest(supplied_token, self._auth_token)

        if not (is_upgrade and client_key and is_authorized):
            self._log("handshake rejected (upgrade={}, key={}, auth={})".format(
                is_upgrade, bool(client_key), is_authorized))
            try:
                conn.sendall(_reject_response())
            except OSError:
                pass
            conn.close()
            return False

        try:
            conn.sendall(_handshake_response(client_key))
        except OSError:
            conn.close()
            return False
        return True

    def _client_loop(self, conn):
        if not self._handshake(conn):
            return

        with self._clients_lock:
            self._next_id += 1
            client_id = self._next_id
            self._clients[client_id] = conn
        conn.settimeout(0.5)
        self._log("client #{} connected ({} total)".format(client_id, self.client_count))
        if self._on_connect:
            self._safe(lambda: self._on_connect(client_id))

        buf = bytearray()
        fragments = bytearray()
        fragment_opcode = None
        while self._running and self._is_current(client_id, conn):
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            try:
                frames = iter_frames(buf)
            except ValueError as exc:
                self._log("protocol error from #{}: {}".format(client_id, exc))
                break

            should_close = False
            for fin, opcode, payload in frames:
                if opcode == _OP_PING:
                    with self._send_lock:
                        try:
                            conn.sendall(encode_frame(_OP_PONG, payload))
                        except OSError:
                            should_close = True
                            break
                elif opcode == _OP_CLOSE:
                    with self._send_lock:
                        try:
                            conn.sendall(encode_frame(_OP_CLOSE, payload[:2]))
                        except OSError:
                            pass
                    should_close = True
                    break
                elif opcode in (_OP_TEXT, _OP_BINARY, _OP_CONT):
                    if opcode != _OP_CONT:
                        fragment_opcode = opcode
                        fragments = bytearray()
                    fragments.extend(payload)
                    if fin and fragment_opcode == _OP_TEXT:
                        text = fragments.decode("utf-8", errors="replace")
                        fragments = bytearray()
                        self._safe(lambda t=text: self._on_message(client_id, t))
            if should_close:
                break

        self._drop_client(client_id)

    def _is_current(self, client_id, conn):
        with self._clients_lock:
            return self._clients.get(client_id) is conn

    def _drop_client(self, client_id, notify=True):
        with self._clients_lock:
            conn = self._clients.pop(client_id, None)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
            self._log("client #{} disconnected ({} total)".format(client_id, self.client_count))
            if notify and self._on_disconnect:
                self._safe(lambda: self._on_disconnect(client_id))

    def _safe(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a bad callback must not kill the IO thread
            self._log("callback error: {}".format(exc))
