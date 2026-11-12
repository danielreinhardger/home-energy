"""The running service: wires the parts together and is what the web layer talks to.

  polling.Poller         reads every device in its own thread (latest values, freshness)
  forecaster.Forecaster  fetches the PV forecast and calibrates it against the real yield
  control.Control        one control cycle: energy situation → heating → hot water
  bookkeeping.Bookkeeper daily ledger, minute history, database upkeep
  views                  the JSON the pages show (read only)

The web layer only reads snapshots and calls the write/config methods below, so it never
talks Modbus directly and many browser tabs cost the devices nothing.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import simulate, views
from .bookkeeping import Bookkeeper
from .control import Control
from .controller import DhwController, HeatingController
from .cop import CopModel, LiveFit, calibrate, fit_live
from .device import ModbusDevice
from .devices import SPECS
from .devices.isg_web import IsgWebDevice
from .devices.openwb import OpenWbDevice
from .finance import Ledger
from .alerts import Alerts, BackupWatch, detect, send_push
from .roomsensor import RoomSensor
from .rules import de
from . import eventlog
from .forecaster import Forecaster
from .migrate import import_legacy
from .polling import Poller
from .store import Store

log = logging.getLogger(__name__)

DB_NAME = "home-energy.sqlite"


class Service:
    def __init__(self, config_path: Path, data_dir: Path, devices: dict[str, ModbusDevice] | None = None,
                 clock=None):
        self.config_path = config_path
        self.data_dir = data_dir
        self.cfg = config_mod.load(config_path)
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state_path = data_dir / "state.json"
        state = self._load_state()

        self.poller = Poller(devices if devices is not None else self._make_devices(), self.lock,
                             lambda: self.cfg.poll_seconds)
        self.store = Store(data_dir / DB_NAME)
        import_legacy(self.store, data_dir)              # once: the JSON files used before SQLite
        self.ledger = Ledger(self.store)
        self.books = Bookkeeper(self.store, self.ledger, data_dir / "backup", lambda: self.clock(), self.lock)
        self.forecaster = Forecaster(lambda: self.cfg.forecast, lambda: self.clock(), self.lock, state, self._save_state)
        self.control = Control(self.poller.devices["isg"], lambda: self.cfg, self.store, state, self.lock,
                               lambda: self.clock(), self._save_state)
        self.alerts = Alerts(lambda: self.clock())
        self.backup = BackupWatch(state.get("backup_heater"))
        self.room = RoomSensor(state.get("room_sensor"))
        self._last_history = 0.0
        self._last_control = 0.0
        self._log_handler: logging.Handler | None = None
        self._live_fit: LiveFit | None = None
        self._live_fit_at = -1e9                          # monotonic time of the last fit (never)
        self._sim_key: str | None = None
        self._sim_rows: list[dict] = []
        self.perf: dict[str, Any] = {"control_ms": None, "control_ms_max": 0.0, "api": {}}

    def _make_devices(self) -> dict:
        hosts = self.cfg.devices
        devices = {"isg": ModbusDevice(SPECS["isg"], hosts.isg), "kostal": ModbusDevice(SPECS["kostal"], hosts.kostal)}
        if hosts.openwb:
            devices["openwb"] = OpenWbDevice(hosts.openwb)
        if hosts.isg:
            devices["isg_web"] = IsgWebDevice(hosts.isg)
        return devices

    @property
    def devices(self) -> dict:
        return self.poller.devices

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        self._log_handler = eventlog.install(self.store)        # warnings and errors -> store.events
        log.info("Dienst gestartet", extra=eventlog.EVENT)
        loops = [(self._loop, (), "control"), (self.forecaster.loop, (self._stop,), "forecast")]
        loops += [(self.poller.loop, (key, self._stop), f"read-{key}") for key in self.devices]
        for target, args, name in loops:
            t = threading.Thread(target=target, args=args, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        if self._log_handler:
            eventlog.uninstall(self._log_handler)
        self._save_state()
        self.ledger.save()
        self.store.close()
        for d in self.devices.values():
            d.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:  # keep the service alive whatever a device does
                log.exception("control cycle failed")
            self._stop.wait(max(1.0, self.cfg.poll_seconds - (time.monotonic() - started)))

    def poll_once(self) -> None:
        """Read every device once, then run one cycle (tests and one-shot use)."""
        self.poller.read_all()
        self.tick()

    def tick(self) -> None:
        """Bookkeeping and control on the latest device data; never reads devices itself."""
        now_mono = time.monotonic()
        self.forecaster.track_yield(self.poller.latest("kostal").get("yield_day"))
        totals = views.day_totals(self.poller)
        if totals is not None:
            totals["backup_kwh"] = self.backup.total          # Heizstab today (alerts.BackupWatch)
            eco = self.cfg.economy
            self.books.track_finance(totals, self.poller.latest("isg").get("outside_temp"), eco.price_buy, eco.price_feed)
        if now_mono - self._last_history >= 60:
            self._last_history = now_mono
            self.books.record_history({**views.energy(self.poller), **self.room_sample()})
        self.books.upkeep(now_mono)
        if now_mono - self._last_control >= self.cfg.control_seconds:
            self._last_control = now_mono
            started = time.monotonic()
            self.control.run(self.poller, self.forecaster.summary(), self.cop_model(), self.room_temp())
            ms = (time.monotonic() - started) * 1000
            with self.lock:
                self.perf["control_ms"] = round(ms, 1)
                self.perf["control_ms_max"] = round(max(self.perf["control_ms_max"], ms), 1)
            self.check_alerts()

    def check_alerts(self) -> None:
        """Once per control cycle: what is wrong right now (alerts.py adds the delays and the push)."""
        p, fc = self.poller, self.forecaster
        devices = {k: (dev.spec.name, p.fresh(k), p.status[k].error) for k, dev in p.devices.items()}
        actions = {kind: (ctl.last or {}).get("action", "")
                   for kind, ctl in (("dhw", self.control.dhw), ("heating", self.control.heating))}
        actions["dhw_power"] = (self.control.dhw_power.last or {}).get("action", "")
        age_h = None if fc.fetched is None else (self.clock() - fc.fetched).total_seconds() / 3600
        problems = detect(self.cfg.alerts, devices, p.latest("isg").get("dhw_temp"), actions,
                          fc.error if self.cfg.forecast.usable else None, age_h)
        isg = p.latest("isg")
        with self.lock:
            self.alerts.update(problems, self.cfg.alerts)
            note = self.backup.update(self.clock(), isg.get("heat_backup_heating_total"),
                                      isg.get("heat_backup_dhw_total"), self.cfg.alerts.backup_kwh)
            if note:
                eur = self.backup.total * self.cfg.economy.price_buy
                out = isg.get("outside_temp")
                extra = f" ≈ {de(eur, 2)} €" + (f" · draußen {de(out)} °C" if out is not None else "")
                self.alerts.notice(self.cfg.alerts, note + extra)

    # -- the room temperature ------------------------------------------------------------
    def room_temp(self) -> float | None:
        """The living room's temperature for the heating strategy: the pushed sensor reading while
        it is fresh, or – only with a Stiebel room unit – the heat pump's own value."""
        h = self.cfg.heating
        if h.room_from_isg:
            return self.poller.latest("isg").get("room_hc1")
        r = self.room.current(self.clock(), h.room_sensor_max_age_min)
        return r.temp if r else None

    def room_reading(self, temp, humidity=None) -> dict:
        """A reading pushed by the room sensor (GET/POST /api/sensor/room)."""
        with self.lock:
            r = self.room.update(self.clock(), temp, humidity)
        self._save_state()
        return {"temp": r.temp, "humidity": r.humidity}

    def room_sample(self) -> dict:
        """For the minute history: the room temperature the strategy used, and the humidity."""
        r = self.room.current(self.clock(), self.cfg.heating.room_sensor_max_age_min)
        return {"room_sensor": self.room_temp(), "room_humidity": r.humidity if r else None}

    def test_push(self) -> None:
        """Send a test message to the configured push address (raises on failure)."""
        url = self.cfg.alerts.push_url
        if not url:
            raise ValueError("keine Push-Adresse eingetragen")
        send_push(url, "Testnachricht: Hinweise vom Smarthome 28 kommen hier an.", tags="bell")

    # -- models used by control and the pages -----------------------------------------
    def cop_model(self) -> CopModel:
        """COP model calibrated on the heat pump's own counters (lifetime + recent days)."""
        today = self.clock().date().isoformat()
        return calibrate(self.poller.latest("isg"), self.ledger.recent_hp(14, today), self.cfg.economy.cop_heating,
                         self.cfg.economy.cop_dhw, self.ledger.heating_days(14, today), self.live_cop_fit())

    LIVE_FIT_DAYS = 30

    def live_cop_fit(self) -> LiveFit | None:
        """Live heating COP over the outside temperature, last 30 days (refit at most once an hour)."""
        now = time.monotonic()
        if now - self._live_fit_at > 3600:
            start = int(self.clock().timestamp()) - self.LIVE_FIT_DAYS * 86400
            self._live_fit = fit_live(self.store.pairs("outside_temp", "cop_heating_live", start))
            self._live_fit_at = now
        return self._live_fit

    @property
    def forecast_error(self) -> str | None:
        return self.forecaster.error

    def simulation(self) -> list[dict]:
        """Strategy vs. fixed targets on model days, with the live settings (cached per settings)."""
        cop = self.cop_model()
        key = repr((self.cfg.dhw, self.cfg.heating, self.cfg.economy, round(cop.heating_scale, 2), round(cop.dhw_cop, 2)))
        if self._sim_key != key:
            self._sim_rows = simulate.compare(self.cfg.economy, self.cfg.dhw, self.cfg.heating, cop)
            self._sim_key = key
        return self._sim_rows

    def controlled_setting(self, key: str) -> bool:
        """Is this ISG setting currently written by a live controller (then not editable by hand)?"""
        return ((key == HeatingController.SETTING and self.cfg.heating.enabled and self.cfg.heating.apply)
                or (key == DhwController.SETTING and self.cfg.dhw.enabled and self.cfg.dhw.apply))

    # -- actions from the web layer ------------------------------------------
    def write_setting(self, device: str, key: str, value: float) -> Any:
        dev = self.devices[device]
        if device == "isg" and self.controlled_setting(key):
            raise PermissionError(
                "WW Soll Tag wird gerade von der PV-Regelung gesteuert. "
                "Komforttemperatur in der Regelung ändern oder Regelung ausschalten." if key == DhwController.SETTING else
                "Raumtemp. Tag wird gerade vom PV-Heizen gesteuert. "
                "Normale Raumtemperatur unter PV-Regelung ändern oder PV-Heizen ausschalten.")
        back = dev.write_setting(key, value)
        self.poller.refresh_settings(device)
        return back

    def refresh_forecast(self) -> None:
        self.forecaster.refresh()

    def request_bath(self, hhmm: str | None = None, temp: float | None = None) -> dict:
        """Ask for bath-ready hot water at the next hh:mm (default from config)."""
        req = self.control.request_bath(hhmm, temp)
        self._last_control = 0.0            # act on the next cycle
        return req.to_json()

    def cancel_bath(self) -> None:
        self.control.cancel_bath()
        self._last_control = 0.0

    def update_config(self, section: str, patch: dict) -> dict:
        with self.lock:
            new = config_mod.update_section(self.cfg, section, patch)
            forecast_changed = section == "forecast" and new.forecast != self.cfg.forecast
            self.cfg = new
            self.control.apply_limits(new)
            config_mod.save(new, self.config_path)
            if forecast_changed:
                self.forecaster.reset()
        if section in ("dhw", "heating"):
            self._last_control = 0.0  # re-evaluate on the next cycle
        return getattr(new, section).__dict__

    def record_api(self, path: str, raw_bytes: int, sent_bytes: int, ms: float) -> None:
        with self.lock:
            a = self.perf["api"].setdefault(path, {"calls": 0, "raw_kb": 0.0, "sent_kb": 0.0, "ms_avg": None})
            a["calls"] += 1
            a["raw_kb"], a["sent_kb"] = round(raw_bytes / 1024, 1), round(sent_bytes / 1024, 1)
            a["ms_avg"] = round(ms if a["ms_avg"] is None else 0.9 * a["ms_avg"] + 0.1 * ms, 1)

    # -- persistence ------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        # (called from the parts' constructors' callbacks only after __init__ finished)
        with self.lock:
            data = {**self.forecaster.state_json(), **self.control.state_json(),
                    "backup_heater": self.backup.to_json(), "room_sensor": self.room.to_json()}
        try:
            config_mod.write_json_atomic(self._state_path, data)
        except OSError as exc:
            log.warning("saving state: %s", exc)
