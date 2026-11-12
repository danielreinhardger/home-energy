"""Data-driven register maps: what a value is, where it lives, how to decode it.

A device map is a list of `Point`s (read-only values) and `Setting`s (values
the user may change). Everything else (reading in blocks, the settings UI, the
API) is generated from these definitions, so supporting a new value means adding
one line to a device map.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

Table = Literal["input", "holding"]
Words = Sequence[int]
Decoder = Callable[[Words], "float | int | str | None"]


# -- decoders -----------------------------------------------------------------
# Each decoder takes the raw 16-bit words of one value and returns a Python value,
# or None when the device reports "not available".

def s16(scale: float = 1.0, na: int | None = 0x8000) -> Decoder:
    def decode(w: Words):
        if na is not None and w[0] == na:
            return None
        v = w[0] - 0x10000 if w[0] >= 0x8000 else w[0]
        return round(v * scale, 6) if scale != 1 else v
    return decode


def u16(scale: float = 1.0, na: int | None = 0x8000) -> Decoder:
    def decode(w: Words):
        if na is not None and w[0] == na:
            return None
        return round(w[0] * scale, 6) if scale != 1 else w[0]
    return decode


def kwh_mwh_pair(w: Words):
    """Stiebel energy totals: word 0 = kWh part (0..999), word 1 = MWh part. Returns kWh."""
    if 0x8000 in (w[0], w[1]):
        return None
    return w[1] * 1000 + w[0]


def f32_cdab(scale: float = 1.0) -> Decoder:
    """IEEE float over two words, low word first (Kostal's default byte order)."""
    def decode(w: Words):
        v = struct.unpack(">f", struct.pack(">HH", w[1], w[0]))[0]
        if v != v:  # NaN
            return None
        return v * scale
    return decode


def u32_cdab(w: Words):
    return (w[1] << 16) | w[0]


def ascii_string(w: Words):
    return struct.pack(f">{len(w)}H", *w).split(b"\0", 1)[0].decode("ascii", "replace").strip()


def bits(w: Words):
    return w[0]


# -- definitions --------------------------------------------------------------
@dataclass(frozen=True)
class Point:
    """A read-only value."""
    key: str
    label: str
    register: int                 # vendor register number (see DeviceSpec.register_base)
    decode: Decoder
    unit: str = ""
    group: str = ""
    words: int = 1
    table: Table = "input"
    digits: int = 1               # display precision hint for the UI
    options: dict[int, str] = field(default_factory=dict)   # status codes: the text the page shows


@dataclass(frozen=True)
class Setting:
    """A user-changeable value stored as a scaled 16-bit holding register.

    `verified` says how we know the register is the one the device page shows:
    "write-test" (written and seen on the page), "value-match" (unique current
    value matches the page) or "" (unverified: shown read-only).
    """
    key: str
    label: str
    register: int
    group: str
    unit: str = ""
    scale: float = 1.0
    minimum: float = 0
    maximum: float = 0
    step: float = 1
    options: dict[int, str] = field(default_factory=dict)
    verified: str = ""
    note: str = ""

    @property
    def writable(self) -> bool:
        return bool(self.verified)

    def decode(self, raw: int) -> float | int | None:
        if raw == 0x8000:
            return None
        v = raw - 0x10000 if raw >= 0x8000 else raw
        return v if self.scale == 1 else round(v * self.scale, 3)

    def encode(self, value: float) -> int:
        """Validate a user value and convert it to the raw register value."""
        if self.options:
            if int(value) not in self.options or int(value) != value:
                raise ValueError(f"{self.label}: {value} ist keine gültige Option")
            return int(value)
        if not self.minimum <= value <= self.maximum:
            raise ValueError(f"{self.label}: {value} außerhalb {self.minimum}..{self.maximum} {self.unit}")
        steps = round((value - self.minimum) / self.step)
        if abs(self.minimum + steps * self.step - value) > 1e-6:
            raise ValueError(f"{self.label}: {value} passt nicht in {self.step}er-Schritte")
        raw = round(value / self.scale)
        return raw & 0xFFFF


@dataclass(frozen=True)
class DeviceSpec:
    key: str
    name: str
    port: int
    unit_id: int
    register_base: int            # 1 when the vendor numbers registers from 1
    points: tuple[Point, ...]
    settings: tuple[Setting, ...] = ()
    settings_table: Table = "holding"
    read_max_gap: int = 8         # how many unmapped registers a block read may span
    page_only: tuple[str, ...] = ()  # options on the device's own page with no Modbus register

    def address(self, register: int) -> int:
        return register - self.register_base


# -- block planning -----------------------------------------------------------
@dataclass(frozen=True)
class Block:
    table: Table
    start: int                    # vendor register number
    count: int


def plan_blocks(spans: Sequence[tuple[Table, int, int]], max_gap: int = 8,
                max_len: int = 100) -> list[Block]:
    """Merge (table, register, words) spans into few contiguous read requests.

    Gaps up to `max_gap` registers are read through rather than split. Devices that
    reject reads over unmapped registers need a small `max_gap` (the ISG needs 0).
    """
    blocks: list[Block] = []
    for table in ("input", "holding"):
        items = sorted((r, n) for t, r, n in spans if t == table)
        start = end = None
        for reg, n in items:
            if start is not None and reg - end <= max_gap and reg + n - start <= max_len:
                end = max(end, reg + n)
                continue
            if start is not None:
                blocks.append(Block(table, start, end - start))
            start, end = reg, reg + n
        if start is not None:
            blocks.append(Block(table, start, end - start))
    return blocks
