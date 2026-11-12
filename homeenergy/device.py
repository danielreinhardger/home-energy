"""Generic Modbus device driven by a DeviceSpec."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .modbus import ModbusTcpClient
from . import i18n
from .registers import DeviceSpec, Point, Setting, device_name, plan_blocks, setting_label


@dataclass
class Reading:
    values: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)


class ModbusDevice:
    def __init__(self, spec: DeviceSpec, host: str, client: ModbusTcpClient | None = None):
        self.spec = spec
        max_gap = spec.read_max_gap
        self.host = host
        self.client = client or ModbusTcpClient(host, spec.port, spec.unit_id)
        self._points = {p.key: p for p in spec.points}
        self._settings = {s.key: s for s in spec.settings}
        self._point_blocks = plan_blocks([(p.table, p.register, p.words) for p in spec.points], max_gap)
        self._setting_blocks = plan_blocks([(spec.settings_table, s.register, 1) for s in spec.settings], max_gap)

    # -- reading ------------------------------------------------------------
    def read_values(self) -> dict[str, Any]:
        raw = self._read_blocks(self._point_blocks)
        return {p.key: p.decode([raw[(p.table, p.register + i)] for i in range(p.words)])
                for p in self.spec.points}

    def read_settings(self) -> dict[str, Any]:
        raw = self._read_blocks(self._setting_blocks)
        return {s.key: s.decode(raw[(self.spec.settings_table, s.register)]) for s in self.spec.settings}

    def _read_blocks(self, blocks) -> dict[tuple[str, int], int]:
        raw: dict[tuple[str, int], int] = {}
        for b in blocks:
            read = self.client.read_input if b.table == "input" else self.client.read_holding
            for i, word in enumerate(read(self.spec.address(b.start), b.count)):
                raw[(b.table, b.start + i)] = word
        return raw

    # -- writing ------------------------------------------------------------
    def setting(self, key: str) -> Setting:
        try:
            return self._settings[key]
        except KeyError:
            raise KeyError(i18n.t("device.error.no_setting", device=device_name(self.spec.key), key=key)) from None

    def write_setting(self, key: str, value: float) -> Any:
        """Validate, write and read back one setting. Returns the value read back."""
        s = self.setting(key)
        label = setting_label(self.spec.key, key)
        if not s.writable:
            raise PermissionError(i18n.t("device.error.read_only", label=label))
        raw = s.encode(value, label)
        address = self.spec.address(s.register)
        self.client.write_single(address, raw)
        back = self.client.read_holding(address, 1)[0]
        if back != raw:
            raise IOError(i18n.t("device.error.read_back", label=label, written=raw, back=back))
        return s.decode(back)

    def point(self, key: str) -> Point:
        return self._points[key]

    def close(self) -> None:
        self.client.close()
