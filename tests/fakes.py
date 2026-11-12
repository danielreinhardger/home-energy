"""Test doubles: an in-process Modbus TCP server and a register-backed client."""
from __future__ import annotations

import socket
import socketserver
import struct
import threading


class FakeRegisters:
    """Register-backed stand-in for ModbusTcpClient (protocol addresses)."""

    def __init__(self, input_regs: dict[int, int] | None = None, holding: dict[int, int] | None = None):
        self.input = dict(input_regs or {})
        self.holding = dict(holding or {})
        self.writes: list[tuple[int, int]] = []

    def read_input(self, address, count):
        return [self.input.get(address + i, 0x8000) for i in range(count)]

    def read_holding(self, address, count):
        return [self.holding.get(address + i, 0x8000) for i in range(count)]

    def write_single(self, address, value):
        self.writes.append((address, value))
        self.holding[address] = value

    def close(self):
        pass


class FakeModbusServer:
    """Minimal Modbus TCP server for FC 3/4/6; unknown addresses raise exception 2."""

    def __init__(self, regs: FakeRegisters):
        self.regs = regs
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                sock: socket.socket = self.request
                while True:
                    hdr = sock.recv(7)
                    if len(hdr) < 7:
                        return
                    tid, _, length, unit = struct.unpack(">HHHB", hdr)
                    pdu = sock.recv(length - 1)
                    sock.sendall(outer._answer(tid, unit, pdu))

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _answer(self, tid, unit, pdu) -> bytes:
        fc = pdu[0]
        table = {3: self.regs.holding, 4: self.regs.input, 6: self.regs.holding}.get(fc)
        if table is None:
            resp = bytes([fc | 0x80, 1])
        elif fc in (3, 4):
            addr, count = struct.unpack(">HH", pdu[1:5])
            if any(addr + i not in table for i in range(count)):
                resp = bytes([fc | 0x80, 2])
            else:
                words = [table[addr + i] for i in range(count)]
                resp = bytes([fc, 2 * count]) + struct.pack(f">{count}H", *words)
        else:
            addr, value = struct.unpack(">HH", pdu[1:5])
            if addr not in table:
                resp = bytes([fc | 0x80, 2])
            else:
                table[addr] = value
                resp = pdu[:5]
        return struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
