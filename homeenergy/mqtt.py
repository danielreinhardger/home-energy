"""Minimal read-only MQTT 3.1.1 subscriber (stdlib only).

Keeps the latest payload per topic in memory. Runs in a background thread,
reconnects with backoff and sends keep-alive pings. QoS 0 only, no publishing.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
import time

log = logging.getLogger(__name__)


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _packet(ptype: int, body: bytes) -> bytes:
    n, rem = len(body), b""
    while True:
        digit, n = n % 128, n // 128
        rem += bytes([digit | (0x80 if n else 0)])
        if not n:
            return bytes([ptype]) + rem + body


class MqttSubscriber:
    def __init__(self, host: str, topics: list[str], port: int = 1883, keepalive: int = 30,
                 client_id: str = "home-energy"):
        self.host, self.port, self.topics, self.keepalive = host, port, topics, keepalive
        self.client_id = f"{client_id}-{int(time.time())}"
        self.values: dict[str, tuple[str, float]] = {}
        self.connected = False
        self.last_error: str | None = None
        self.last_message: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    # -- public -----------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"mqtt-{self.host}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.sendall(bytes([0xE0, 0]))   # DISCONNECT
                sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def get(self, topic: str) -> str | None:
        with self._lock:
            item = self.values.get(topic)
        return item[0] if item else None

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {k: v for k, (v, _) in self.values.items()}

    # -- internals ----------------------------------------------------------------
    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 2.0
            except Exception as exc:  # network errors, broker restarts
                self.last_error = str(exc) or exc.__class__.__name__
                log.debug("mqtt %s: %s", self.host, exc)
            self.connected = False
            if self._stop.wait(backoff):
                return
            backoff = min(60.0, backoff * 2)

    def _session(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=10)
        self._sock = sock
        try:
            sock.sendall(_packet(0x10, _str("MQTT") + bytes([4, 0x02]) + struct.pack(">H", self.keepalive)
                                 + _str(self.client_id)))
            ptype, body = self._read(sock)
            if ptype >> 4 != 2 or len(body) < 2 or body[1] != 0:
                raise ConnectionError(f"broker refused connection ({body[1] if len(body) > 1 else '?'})")
            payload = struct.pack(">H", 1) + b"".join(_str(t) + b"\x00" for t in self.topics)
            sock.sendall(_packet(0x82, payload))
            self.connected, self.last_error = True, None
            sock.settimeout(1.0)
            last_ping = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - last_ping >= self.keepalive / 2:
                    sock.sendall(bytes([0xC0, 0]))   # PINGREQ
                    last_ping = time.monotonic()
                try:
                    ptype, body = self._read(sock)
                except socket.timeout:
                    continue
                if ptype >> 4 == 3:
                    self._on_publish(ptype, body)
        finally:
            self._sock = None
            sock.close()

    def _on_publish(self, ptype: int, body: bytes) -> None:
        tlen = struct.unpack(">H", body[:2])[0]
        topic = body[2:2 + tlen].decode("utf-8", "replace")
        offset = 2 + tlen + (2 if (ptype >> 1) & 3 else 0)
        now = time.time()
        with self._lock:
            self.values[topic] = (body[offset:].decode("utf-8", "replace"), now)
            self.last_message = now

    @staticmethod
    def _read(sock: socket.socket) -> tuple[int, bytes]:
        first = sock.recv(1)
        if not first:
            raise ConnectionError("broker closed the connection")
        sock.settimeout(10.0)              # once a packet starts, read it whole
        try:
            mult, length = 1, 0
            while True:
                b = sock.recv(1)
                if not b:
                    raise ConnectionError("broker closed the connection")
                length += (b[0] & 127) * mult
                mult *= 128
                if not b[0] & 128:
                    break
            data = b""
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    raise ConnectionError("broker closed the connection")
                data += chunk
            return first[0], data
        finally:
            sock.settimeout(1.0)
