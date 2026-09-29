"""Agent Board terminal implementation."""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
from .config import DEFAULT_CONFIG, _config_lock, load_config, config, _deep_merge, save_config
from .herdr import (
    HerdrError,
    _herdr_env,
    herdr,
    herdr_text,
    snapshot,
    PANE_RE,
    SHA_RE,
    NAME_RE,
    TICKET_RE,
    ALLOWED_KEYS,
    valid_pane,
    valid_keys,
    read_screen,
    _extract_read_text,
)
from .security import (
    hash_password,
    verify_password,
    Sessions,
    SESSIONS,
    _secrets_lock,
    _save_machine_tokens,
    _load_machine_tokens,
    origin_allowed,
    _audit_lock,
    audit,
)
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
    if not masked or length > 1_000_000:
        raise ConnectionError("invalid or oversized WebSocket frame")
    mask = readn(4)
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

    def __init__(self, sock, pane: str, cols: int, rows: int, authorized=None):
        self.sock = sock
        self.authorized = authorized
        self.pane = pane
        self.cols = max(20, min(cols, 400))
        self.rows = max(5, min(rows, 200))
        self.pid = -1
        self.fd = -1
        self._stop = threading.Event()
        self._sender = None
        self._send_lock = threading.Lock()

    def _send(self, payload: bytes, opcode: int) -> None:
        with self._send_lock:
            _ws_send(self.sock, payload, opcode)

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
                self._send(data, _WS_BINARY)
            except OSError:
                break
        self._stop.set()
        try:
            self.sock.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def _reap(self) -> None:
        try:
            os.waitpid(self.pid, 0)
        except (ChildProcessError, OSError):
            pass

    def run(self) -> None:
        try:
            self._spawn()
        except (OSError, ValueError):
            return
        t = threading.Thread(target=self._pty_to_ws, daemon=True)
        t.start()
        try:
            while not self._stop.is_set():
                if self.authorized and not self.authorized():
                    break
                readable, _, _ = _select.select([self.sock], [], [], 1.0)
                if not readable:
                    continue
                frame = _ws_recv(self.sock)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == _WS_CLOSE:
                    break
                if opcode == _WS_PING:
                    self._send(payload, _WS_PONG)
                    continue
                if opcode in (_WS_TEXT, _WS_BINARY):
                    # Control messages: a small JSON prelude drives resize.
                    if payload[:1] == b"{":
                        try:
                            msg = json.loads(payload)
                        except ValueError:
                            msg = {}
                        if isinstance(msg, dict) and msg.get("type") == "resize":
                            try:
                                self.resize(int(msg.get("cols", self.cols)),
                                            int(msg.get("rows", self.rows)))
                            except (ValueError, TypeError, OverflowError):
                                pass
                            continue
                    try:
                        os.write(self.fd, payload)
                    except OSError:
                        break
        except (ConnectionError, OSError):
            pass
        finally:
            self._stop.set()
            # SIGHUP only detaches the herdr client; the server keeps running.
            if self.pid > 0:
                try:
                    os.kill(self.pid, signal.SIGHUP)
                except OSError:
                    pass
            if self.pid > 0:
                # Reap the detached client without blocking other requests.
                threading.Thread(target=self._reap, daemon=True).start()
            if self.fd >= 0:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
            try:
                self._send(b"", _WS_CLOSE)
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
    try:
        cols = int(params.get("cols", ["80"])[0] or 80)
        rows = int(params.get("rows", ["24"])[0] or 24)
    except ValueError:
        handler._json(400, {"error": "bad_dimensions"})
        return
    if not valid_pane(pane):
        handler._json(400, {"error": "bad_pane"})
        return

    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", _ws_accept(key))
    handler.end_headers()
    sock = handler.connection
    sock.settimeout(10)
    audit(user, "terminal_open", pane, extra={"cols": cols, "rows": rows})
    try:
        TerminalSession(sock, pane, cols, rows, authorized=handler._auth).run()
    finally:
        audit(user, "terminal_close", pane)
        handler.close_connection = True
