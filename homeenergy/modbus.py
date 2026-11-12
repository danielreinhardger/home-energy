"""Minimal, dependency-free Modbus TCP client.

Only the function codes this project needs: 3 (read holding), 4 (read input),
6 (write single holding). Addresses are protocol addresses (0-based); device maps
translate vendor register numbers into protocol addresses.
"""
from __future__ import annotations

import socket
import struct
import threading

MAX_READ = 125  # Modbus limit for registers per read request

EXCEPTION_CODES = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
                   4: "device failure", 6: "device busy"}


class ModbusError(Exception):
    """Transport failure talking to a Modbus device."""


class ModbusExceptionResponse(ModbusError):
    """The device answered with a Modbus exception (the connection is still fine)."""

    def __init__(self, code: int):
        super().__init__(f"Modbus exception {code} ({EXCEPTION_CODES.get(code, 'unknown')})")
        self.code = code


class ModbusTcpClient:
    """Thread-safe Modbus TCP client with lazy connect and one reconnect retry."""

    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 5.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self._sock: socket.socket | None = None
        self._tid = 0
        self._lock = threading.Lock()

    def read_holding(self, address: int, count: int) -> list[int]:
        return self._read(3, address, count)

    def read_input(self, address: int, count: int) -> list[int]:
        return self._read(4, address, count)

    def write_single(self, address: int, value: int) -> None:
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"register value out of range: {value}")
        self._transact(struct.pack(">BHH", 6, address, value))

    def close(self) -> None:
        with self._lock:
            self._drop()

    def _read(self, fc: int, address: int, count: int) -> list[int]:
        if not 1 <= count <= MAX_READ:
            raise ValueError(f"count must be 1..{MAX_READ}")
        body = self._transact(struct.pack(">BHH", fc, address, count))
        if body[1] != 2 * count:
            raise ModbusError(f"expected {2 * count} data bytes, got {body[1]}")
        return list(struct.unpack(f">{count}H", body[2:2 + 2 * count]))

    def _transact(self, pdu: bytes) -> bytes:
        with self._lock:
            for attempt in (1, 2):
                try:
                    return self._send_recv(pdu)
                except ModbusExceptionResponse:
                    raise
                except OSError as exc:
                    self._drop()
                    if attempt == 2:
                        raise ModbusError(f"{self.host}:{self.port}: {exc}") from exc
        raise AssertionError("unreachable")

    def _send_recv(self, pdu: bytes) -> bytes:
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._tid = (self._tid + 1) & 0xFFFF
        self._sock.sendall(struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit) + pdu)
        tid, _, length, _ = struct.unpack(">HHHB", self._recv_exact(7))
        body = self._recv_exact(length - 1)
        if tid != self._tid:
            raise ConnectionError(f"transaction id mismatch ({tid} != {self._tid})")
        if body[0] & 0x80:
            raise ModbusExceptionResponse(body[1])
        return body

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed by device")
            buf += chunk
        return buf

    def _drop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
