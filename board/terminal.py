"""The browser terminal: the full herdr TUI over a WebSocket backed by a pty.

Nothing from the harness config is read. Closing sends SIGHUP to the client,
which only detaches; the herdr server and every agent keep running.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import pty
import select
import signal
import struct
import termios
import threading
import urllib.parse

from .config import config
from .herdr import HerdrError, herdr, valid_pane
from .security import audit, origin_allowed

_b64 = base64
_fcntl = fcntl
_hashlib = hashlib
_pty = pty
_select = select
_struct = struct
_termios = termios

import base64 as _b64  # noqa: E402
import fcntl as _fcntl  # noqa: E402
import hashlib as _hashlib  # noqa: E402
import pty as _pty  # noqa: E402
import select as _select  # noqa: E402
import struct as _struct  # noqa: E402
import termios as _termios  # noqa: E402

# RFC 6455 opcodes
_WS_TEXT = 0x1
_WS_BINARY = 0x2
_WS_CLOSE = 0x8
_WS_PING = 0x9
_WS_PONG = 0xA
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept(key: str) -> str:
    return _b64.b64encode(
        _hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()


def _ws_send(sock, payload: bytes, opcode: int = _WS_BINARY) -> None:
    """Server->client frame (unmasked)."""
    header = bytearray()
    header.append(0x80 | opcode)
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += _struct.pack("!H", n)
    else:
        header.append(127)
        header += _struct.pack("!Q", n)
    sock.sendall(bytes(header) + payload)


def _ws_recv(sock) -> tuple[int, bytes] | None:
    """Client->server frame (masked). Returns (opcode, payload) or None."""
    def readn(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    try:
        b0, b1 = readn(2)
    except (ConnectionError, OSError):
        return None
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    length = b1 & 0x7F
    if length == 126:
        length = _struct.unpack("!H", readn(2))[0]
    elif length == 127:
        length = _struct.unpack("!Q", readn(8))[0]
    mask = readn(4) if masked else b"\x00\x00\x00\x00"
    payload = readn(length) if length else b""
    if masked:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


class TerminalSession:
    """One browser terminal: a fresh herdr TUI client in a pty, bridged over WS.

    Measured: spawn a new `herdr --session <name>` client with every HERDR_*
    variable removed (otherwise it believes it is nested). Closing sends SIGHUP
    to the client, which only detaches: the server and its agents keep running.
    """

    def __init__(self, sock, pane: str, cols: int, rows: int):
        self.sock = sock
        self.pane = pane
        self.cols = max(20, min(cols, 400))
        self.rows = max(5, min(rows, 200))
        self.pid = -1
        self.fd = -1
        self._stop = threading.Event()
        self._sender = None

    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("HERDR_")}
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.setdefault("PATH", os.environ.get("PATH", ""))
        return env

    def _spawn(self) -> None:
        cfg = config()
        argv = ["herdr", "--session", cfg["herdr_session"]]
        if self.pane and valid_pane(self.pane):
            # focus the requested pane before the client draws, so the TUI
            # opens on the agent the user clicked. Best-effort.
            try:
                herdr("pane", "focus", self.pane, timeout=8)
            except HerdrError:
                pass
        self.pid, self.fd = _pty.fork()
        if self.pid == 0:
            try:
                os.execvpe(argv[0], argv, self._env())
            except OSError:
                os._exit(127)
        _fcntl.ioctl(self.fd, _termios.TIOCSWINSZ,
                     _struct.pack("HHHH", self.rows, self.cols, 0, 0))

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(20, min(cols, 400))
        self.rows = max(5, min(rows, 200))
        if self.fd >= 0:
            try:
                _fcntl.ioctl(self.fd, _termios.TIOCSWINSZ,
                             _struct.pack("HHHH", self.rows, self.cols, 0, 0))
            except OSError:
                pass

    def _pty_to_ws(self) -> None:
        while not self._stop.is_set():
            try:
                r, _, _ = _select.select([self.fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not r:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            try:
                _ws_send(self.sock, data, _WS_BINARY)
            except OSError:
                break
        self._stop.set()

    def run(self) -> None:
        try:
            self._spawn()
        except (OSError, ValueError):
            return
        t = threading.Thread(target=self._pty_to_ws, daemon=True)
        t.start()
        try:
            while not self._stop.is_set():
                frame = _ws_recv(self.sock)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == _WS_CLOSE:
                    break
                if opcode == _WS_PING:
                    _ws_send(self.sock, payload, _WS_PONG)
                    continue
                if opcode in (_WS_TEXT, _WS_BINARY):
                    # Control messages: a small JSON prelude drives resize.
                    if payload[:1] == b"{":
                        try:
                            msg = json.loads(payload)
                        except ValueError:
                            msg = {}
                        if msg.get("type") == "resize":
                            self.resize(int(msg.get("cols", self.cols)),
                                        int(msg.get("rows", self.rows)))
                            continue
                    try:
                        os.write(self.fd, payload)
                    except OSError:
                        break
        finally:
            self._stop.set()
            # SIGHUP only detaches the herdr client; the server keeps running.
            if self.pid > 0:
                try:
                    os.kill(self.pid, signal.SIGHUP)
                except OSError:
                    pass
            if self.fd >= 0:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
            try:
                _ws_send(self.sock, b"", _WS_CLOSE)
            except OSError:
                pass


def serve_terminal(handler) -> None:
    """Upgrade an HTTP request to a WebSocket terminal session."""
    headers = handler.headers
    key = headers.get("Sec-WebSocket-Key")
    if not key or "websocket" not in (headers.get("Upgrade", "").lower()):
        handler._json(400, {"error": "expected_websocket"})
        return
    host = handler._host()
    origin = headers.get("Origin")
    if not origin_allowed(host, origin):
        handler._json(403, {"error": "forbidden"})
        return
    user = handler._auth()
    if not user:
        handler._json(403, {"error": "forbidden"})
        return
    q = urllib.parse.urlparse(handler.path).query
    params = urllib.parse.parse_qs(q)
    pane = params.get("pane", [""])[0]
    cols = int(params.get("cols", ["80"])[0] or 80)
    rows = int(params.get("rows", ["24"])[0] or 24)
    if not valid_pane(pane):
        handler._json(400, {"error": "bad_pane"})
        return

    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", _ws_accept(key))
    handler.end_headers()
    sock = handler.connection
    audit(user, "terminal_open", pane, extra={"cols": cols, "rows": rows})
    try:
        TerminalSession(sock, pane, cols, rows).run()
    finally:
        audit(user, "terminal_close", pane)
        handler.close_connection = True


