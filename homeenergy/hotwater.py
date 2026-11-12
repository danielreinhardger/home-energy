"""PV-led hot water strategy: pure decision logic, no I/O.

Hot water is heated when PV is available, but only as much as is needed until
the next PV window (see economics.py: heat stored beyond that just displaces
tomorrow's free PV at a worse COP). Targets for the heat pump's "WW Soll Tag":

  REHEAT      Daily need: PV surplus heats the tank to comfort_temp. The battery
              doesn't have to be full: the energy is needed before the next sun
              anyway, and PV now beats battery or grid tonight.
  BOOST       Reserve before a dull day: tomorrow's PV won't cover hot water, the
              battery still fills and (in winter) the house is already preheated:
              heat to boost_temp (default 50 °C).
  SOLAR_WAIT  Sun is coming (today or tomorrow): hold min_temp so the heat pump
              doesn't reheat the tank from the battery or the grid.
  SAVE        Night before a dull day (evening, or after midnight before the sun is up):
              no PV will refill the tank, so let it drop to min_temp overnight – less
              standing loss, and the reheat waits for the day (warmer air, better COP)
              instead of running at night from battery or grid.
  PV_ONLY     Otherwise without sun (pv_only, default): hold min_temp. The tank gets above
              the minimum only with PV (REHEAT, BOOST); below it the heat pump keeps the
              minimum itself. A requested bath is always heated.
  NORMAL      As PV_ONLY with pv_only switched off: the user's comfort_temp.
  BATH        A bath was requested: bath_temp by the requested time, PV first.
  HYGIENE     Anti-legionella: once every hygiene_days the tank goes to hygiene_temp (60 °C),
              with PV surplus like a reserve. Without a sunny day for hygiene_max_days it runs
              anyway in the warmest hours (11–15 h, best COP). Done when the tank reaches the
              temperature (whatever heated it) or after hygiene_max_min. The ISG's own
              anti-legionella program stays off (its temperature is 10 °C): this one picks the sun.

See docs/strategy.md for the reasoning and the research behind it. The energy situation
(surplus, battery need, free PV, sun up) comes from situation.py; the start/stop timers
and the Decision/Trace types from rules.py – shared with the heating strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .forecast import ForecastSummary
from .rules import Decision, RunState, Trace, de, minutes_since, stage
from .situation import Situation
from .settings import clock, flag, num

WATER_WH_PER_L_K = 1.163


class DhwMode(str, Enum):
    OFF = "off"
    NORMAL = "normal"
    SOLAR_WAIT = "solar_wait"
    SAVE = "save"
    PV_ONLY = "pv_only"
    BOOST = "boost"
    REHEAT = "reheat"
    BATH = "bath"
    HYGIENE = "hygiene"


@dataclass
class DhwConfig:
    enabled: bool = flag(True, 'PV-Warmwasserregelung aktiv', 'Berechnet laufend die beste Warmwassertemperatur.')
    # False = dry run: decide and log, never write
    apply: bool = flag(False, 'An Wärmepumpe senden', 'Aus = Testbetrieb: Entscheidungen werden nur angezeigt.')
    comfort_temp: float = num(46.0, 'Komforttemperatur', '°C', 10, 65, 0.5, 'Normales WW Soll Tag.')
    min_temp: float = num(42.0, 'Mindesttemperatur', '°C', 10, 65, 0.5, 'Untergrenze beim Warten auf Sonne.')
    # reserve before a dull day (no more daily 55 °C boost)
    boost_temp: float = num(50.0, 'Vorrat vor trübem Tag', '°C', 10, 65, 0.5,
                            'Nur wenn morgen wenig Sonne kommt, die Batterie voll wird und das Haus warm ist.')
    tank_liters: float = num(235.0, 'Speichervolumen', 'L', 50, 1000, 5)
    dhw_power_w: float = num(1300.0, 'Elektr. Leistung WW-Betrieb', 'W', 200, 6000, 50)
    cop: float = num(2.6, 'COP Warmwasser', '', 1, 6, 0.1)
    start_surplus_ratio: float = num(0.9, 'Startschwelle (Anteil der WW-Leistung)', '', 0.3, 2, 0.05)
    start_delay_min: float = num(5.0, 'Startverzögerung', 'min', 0, 60, 1)
    stop_deficit_w: float = num(300.0, 'Stopp bei Netz-/Batteriebezug über', 'W', 0, 5000, 50)
    stop_delay_min: float = num(10.0, 'Stoppverzögerung', 'min', 0, 60, 1)
    min_boost_min: float = num(20.0, 'Mindestlaufzeit Boost', 'min', 0, 120, 1)
    soc_boost_min: float = num(95.0, 'Vorrat ohne Prognose ab SOC', '%', 0, 100, 1)
    # daily PV reheat only once the battery fills (or with export)
    battery_first: bool = flag(True, 'Batterie zuerst',
                               'Nachheizen mit PV nur bei Einspeisung oder wenn die Batterie heute sicher voll wird.')
    battery_fill_safety: float = num(1.2, 'Sicherheitsfaktor Batterie', '', 1, 3, 0.05)
    base_load_w: float = num(500.0, 'Grundlast Haus', 'W', 0, 5000, 50)
    poor_day_kwh: float = num(8.0, 'Schwacher Tag unter', 'kWh', 0, 100, 0.5)
    solar_wait: bool = flag(True, 'Auf Sonne warten', 'Vor Sonnenstunden auf Mindesttemperatur halten.')
    # above min_temp only with PV; otherwise hold the minimum
    pv_only: bool = flag(True, 'Nur mit PV über Minimum heizen',
                         'Über die Mindesttemperatur wird nur mit PV-Überschuss geheizt; sonst hält die Wärmepumpe nur das '
                         'Minimum. Ein angemeldetes Bad wird immer geheizt. Aus = sonst Komforttemperatur.')
    # night before a dull day: let the tank drop to min_temp
    dull_night_min: bool = flag(True, 'Vor trübem Tag absinken lassen',
                                'Nachts vor einem trüben Tag bis zur Mindesttemperatur absinken lassen – nachgeheizt wird tagsüber '
                                'statt nachts aus Batterie oder Netz.')
    # hot water power limit (ISG "WW-LEISTUNG", written via the ISG web page; dhwpower.py)
    power_control: bool = flag(True, 'WW-Leistung an PV anpassen',
                               'Beim Heizen mit PV: weniger Leistung bei knappem Überschuss, mehr bei Einspeisung '
                               '(ISG „WW-Leistung“, über die ISG-Webseite).')
    power_normal: float = num(80, 'WW-Leistung normal', '%', 30, 100, 5, 'Ohne PV-Heizen (euer bisheriger Wert).')
    power_low: float = num(50, 'WW-Leistung bei knappem PV', '%', 30, 100, 5)
    power_high: float = num(100, 'WW-Leistung bei viel PV', '%', 30, 100, 5)
    power_every_min: float = num(30, 'WW-Leistung ändern höchstens alle', 'min', 10, 240, 5)
    power_max_writes: float = num(8, 'WW-Leistung max. Änderungen pro Tag', '', 1, 24, 1)
    # anti-legionella with PV
    hygiene_enabled: bool = flag(True, 'Legionellenschutz mit PV',
                                 'Einmal pro Intervall den Speicher auf die Schutztemperatur heizen – bevorzugt mit PV.')
    hygiene_temp: float = num(60.0, 'Legionellenschutz-Temperatur', '°C', 55, 65, 0.5)
    hygiene_days: float = num(7, 'Legionellenschutz alle', 'Tage', 1, 30, 1)
    hygiene_max_days: float = num(14, 'Spätestens nach', 'Tage', 2, 60, 1,
                                  'Kam so lange kein PV-Tag, wird mittags (11–15 Uhr) trotzdem geheizt.')
    hygiene_max_min: float = num(240, 'Legionellenschutz höchstens', 'min', 30, 600, 10,
                                 'Erreicht der Speicher die Temperatur so lange nicht, gilt der Versuch als beendet.')
    bath_temp: float = num(55.0, 'Badtemperatur', '°C', 10, 65, 0.5, 'Ziel, wenn ein Bad angemeldet ist.')
    bath_default_time: str = clock("17:30", 'Standard-Badezeit', 'Vorschlag im Bad-Knopf.')
    # safety margin before the bath (write interval + reaction)
    bath_margin_min: float = num(20.0, 'Sicherheitspuffer vor dem Bad', 'min', 0, 120, 5)

    def validate(self) -> None:
        if not 10 <= self.min_temp <= self.comfort_temp <= self.boost_temp <= 65:
            raise ValueError("Temperaturen: 10 ≤ Minimum ≤ Komfort ≤ Vorrat ≤ 65 °C")
        if not 30 <= self.power_low <= self.power_normal <= self.power_high <= 100:
            raise ValueError("WW-Leistung: 30 ≤ knapp ≤ normal ≤ viel PV ≤ 100 %")
        if self.hygiene_max_days < self.hygiene_days:
            raise ValueError("Legionellenschutz: „spätestens nach“ muss mindestens das Intervall sein")
        if not self.min_temp <= self.bath_temp <= 65:
            raise ValueError("Badtemperatur: zwischen Mindesttemperatur und 65 °C")
        parse_hhmm(self.bath_default_time)
        if self.cop <= 0 or self.dhw_power_w <= 0 or self.tank_liters <= 0:
            raise ValueError("COP, Leistung und Speichervolumen müssen positiv sein")


@dataclass
class DhwInputs:
    sit: Situation
    bath: "BathRequest | None" = None
    heating_season: bool = False        # it's cold: preheating the house beats extra hot water
    heating_boost: bool = False         # the heating controller is storing PV heat in the house right now

    @property
    def now(self) -> datetime:
        return self.sit.now


@dataclass
class BathRequest:
    """Hot water wanted for a bath at `deadline`, at least `temp` °C."""
    deadline: datetime
    temp: float

    def to_json(self) -> dict:
        return {"deadline": self.deadline.isoformat(), "temp": self.temp,
                "time": self.deadline.strftime("%H:%M")}

    @classmethod
    def from_json(cls, d: dict) -> "BathRequest":
        return cls(datetime.fromisoformat(d["deadline"]), float(d["temp"]))


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(value).strip().split(":"))
    except ValueError:
        raise ValueError(f"Uhrzeit '{value}' bitte als HH:MM") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Uhrzeit '{value}' ungültig")
    return h, m


def next_occurrence(now: datetime, hhmm: str) -> datetime:
    """The next time the clock shows hh:mm (today if still ahead, else tomorrow)."""
    h, m = parse_hhmm(hhmm)
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


@dataclass
class DhwState(RunState):
    MODE = DhwMode
    mode: DhwMode = DhwMode.NORMAL
    hygiene_at: datetime | None = None    # last time the tank reached the anti-legionella temperature


def dhw_energy_wh(cfg: DhwConfig, from_temp: float, to_temp: float) -> float:
    """Electrical energy to heat the tank from `from_temp` to `to_temp`."""
    return cfg.tank_liters * WATER_WH_PER_L_K * max(0.0, to_temp - from_temp) / cfg.cop


def hygiene_status(cfg: DhwConfig, prev: DhwState, now: datetime) -> tuple[bool, bool, datetime]:
    """(due, overdue, last). Without any record the interval counts as just over: due now."""
    last = prev.hygiene_at or now - timedelta(days=cfg.hygiene_days)
    age = now - last
    return (cfg.hygiene_enabled and age >= timedelta(days=cfg.hygiene_days),
            cfg.hygiene_enabled and age >= timedelta(days=cfg.hygiene_max_days), last)


def decide(inp: DhwInputs, cfg: DhwConfig, prev: DhwState) -> tuple[Decision, DhwState]:
    """The rules below; plus the anti-legionella bookkeeping, whatever mode results."""
    decision, state = _decide(inp, cfg, prev)
    now, temp = inp.now, inp.sit.dhw_temp
    _, _, last = hygiene_status(cfg, prev, now)
    state.hygiene_at = last
    if temp is not None and temp >= cfg.hygiene_temp - 1.0:
        state.hygiene_at = now                         # hot enough, whatever heated it (also a bath)
    elif (prev.mode == DhwMode.HYGIENE and decision.mode != DhwMode.HYGIENE
          and minutes_since(prev.mode_since, now) >= cfg.hygiene_max_min):
        state.hygiene_at = now                         # gave up: try again next interval, not every day
        decision.reasons.insert(0, f"Legionellenschutz: {cfg.hygiene_temp:.0f} °C in {cfg.hygiene_max_min:.0f} min "
                                   f"nicht erreicht ({temp:.1f} °C) – nächster Versuch in {cfg.hygiene_days:.0f} Tagen")
    return decision, state


def _decide(inp: DhwInputs, cfg: DhwConfig, prev: DhwState) -> tuple[Decision, DhwState]:
    sit, now = inp.sit, inp.now
    tr = Trace()
    if not tr.check("enabled", cfg.enabled, "Regelung eingeschaltet" if cfg.enabled else "Regelung ausgeschaltet"):
        return (Decision(DhwMode.OFF, cfg.comfort_temp, ["Regelung aus: Komforttemperatur"], trace=tr),
                DhwState(DhwMode.OFF, now))
    if sit.dhw_temp is None:
        tr.check("sensor", False, "keine Warmwassertemperatur vom Gerät")
        return (Decision(DhwMode.NORMAL, cfg.comfort_temp, ["Keine Warmwassertemperatur: Komfort"], trace=tr),
                DhwState(DhwMode.NORMAL, now))

    surplus_w, deficit_w = sit.surplus_w, sit.deficit_w
    battery_need_wh, free_today_wh = sit.battery_need_wh, sit.free_pv_today_wh
    fc = sit.forecast
    dull_tomorrow = fc is not None and fc.tomorrow_wh < cfg.poor_day_kwh * 1000
    reserve_need_wh = dhw_energy_wh(cfg, sit.dhw_temp, cfg.boost_temp)
    needed_w = cfg.start_surplus_ratio * cfg.dhw_power_w

    metrics = {
        "surplus_w": round(surplus_w), "deficit_w": round(deficit_w),
        "battery_need_kwh": round(battery_need_wh / 1000, 2),
        "boost_need_kwh": round(reserve_need_wh / 1000, 2),
        "free_pv_today_kwh": None if free_today_wh is None else round(free_today_wh / 1000, 2),
        "boost_target": cfg.boost_temp, "comfort_temp": cfg.comfort_temp,
        "dhw_temp": sit.dhw_temp, "soc": round(sit.soc), "heating_season": inp.heating_season,
    }

    # Would a reserve heat-up still let the battery fill today?
    battery_ok = sit.battery_fills(cfg.soc_boost_min, cfg.battery_fill_safety, reserve_need_wh)
    if sit.soc >= cfg.soc_boost_min:
        battery_reason = f"Batterie {sit.soc:.0f} % ≥ {cfg.soc_boost_min:.0f} %"
    elif battery_ok:
        battery_reason = (
            f"Prognose: {free_today_wh / 1000:.1f} kWh frei, reicht für Batterie "
            f"({battery_need_wh / 1000:.1f} kWh) und Vorrat ({reserve_need_wh / 1000:.1f} kWh)")
    else:
        battery_reason = (
            f"Batterie hat Vorrang ({sit.soc:.0f} % < {cfg.soc_boost_min:.0f} %"
            + (", Prognose reicht nicht)" if free_today_wh is not None else ", keine Prognose)"))

    state = DhwState(prev.mode if prev.mode != DhwMode.OFF else DhwMode.NORMAL,
                     prev.mode_since or now, prev.start_since, prev.stop_since)

    # ---- bath requested: it overrides everything else -----------------------
    bath_active = inp.bath is not None and now < inp.bath.deadline
    tr.check("bath", bath_active, f"Bad um {inp.bath.deadline:%H:%M} angemeldet" if bath_active else "kein Bad angemeldet")
    if bath_active:
        return _bath(inp, cfg, fc, state, metrics, surplus_w, deficit_w, tr)
    if state.mode == DhwMode.BATH:                       # request finished or cancelled
        state = DhwState(DhwMode.NORMAL, now)

    # ---- a PV heat-up (daily reheat or reserve) is running --------------------
    running = state.mode in (DhwMode.BOOST, DhwMode.REHEAT, DhwMode.HYGIENE)
    hygiene = state.mode == DhwMode.HYGIENE
    goal = {DhwMode.BOOST: cfg.boost_temp, DhwMode.HYGIENE: cfg.hygiene_temp}.get(state.mode, cfg.comfort_temp)
    what = {DhwMode.BOOST: "Vorrat", DhwMode.HYGIENE: "Legionellenschutz"}.get(state.mode, "Nachheizen")
    _, overdue, _ = hygiene_status(cfg, prev, now)
    if tr.check("running", running,
                f"{what} auf {goal:.1f} °C läuft seit "
                f"{minutes_since(state.mode_since, now):.0f} min" if running else "kein PV-Heizen des Speichers aktiv"):
        if tr.check("run_full", sit.dhw_temp >= goal - 1.0, f"Speicher {sit.dhw_temp:.1f} °C, Ziel {goal:.1f} °C"):
            return _leave_boost(inp, cfg, fc, metrics, f"Speicher auf {sit.dhw_temp:.1f} °C geladen", tr)
        if hygiene and minutes_since(state.mode_since, now) >= cfg.hygiene_max_min:
            return _leave_boost(inp, cfg, fc, metrics, "Legionellenschutz: Zeit abgelaufen", tr)
        stop = state.track_stop(deficit_w, cfg.stop_deficit_w, now, cfg.stop_delay_min, cfg.min_boost_min)
        if hygiene and overdue:
            stop = False                                   # overdue: finish it, PV or not
        tr.check("run_deficit", stop,
                 f"Defizit {deficit_w:.0f} W (Grenze {cfg.stop_deficit_w:.0f} W) seit "
                 f"{minutes_since(state.stop_since, now):.0f} von {cfg.stop_delay_min:.0f} min")
        if stop:
            return _leave_boost(inp, cfg, fc, metrics,
                                f"Zu wenig PV: {deficit_w:.0f} W aus Netz/Batterie seit {cfg.stop_delay_min:.0f} min", tr)
        reasons = [f"{ {DhwMode.BOOST: 'Vorrat vor trübem Tag', DhwMode.HYGIENE: 'Legionellenschutz'}.get(state.mode, 'Tagesbedarf mit PV')}: "
                   f"Speicher auf {goal:.1f} °C"]
        if state.stop_since:
            reasons.append(f"Defizit {deficit_w:.0f} W – Stopp nach {cfg.stop_delay_min:.0f} min")
        tr.check({DhwMode.BOOST: "out_boost", DhwMode.HYGIENE: "out_hygiene"}.get(state.mode, "out_reheat"), True,
                 f"Ziel {goal:.1f} °C")
        return Decision(state.mode, goal, reasons, metrics, tr), state

    # ---- start? both kinds need surplus for start_delay_min ------------------------
    # battery first: until the battery will be full, only power that goes to the grid counts
    reheat_need_wh = dhw_energy_wh(cfg, sit.dhw_temp, cfg.comfort_temp)
    bat_fills = sit.battery_fills(cfg.soc_boost_min, cfg.battery_fill_safety, reheat_need_wh)
    usable_w = surplus_w if (bat_fills or not cfg.battery_first) else sit.export_w
    metrics["usable_w"] = round(usable_w)
    surplus_ok = usable_w >= needed_w
    held = state.track_start(surplus_ok, now, cfg.start_delay_min)

    # 1) daily need: tank below comfort -> PV heats it, battery doesn't have to be full
    need = sit.dhw_temp < cfg.comfort_temp - 2.0
    tr.check("need", need, f"Speicher {sit.dhw_temp:.1f} °C, Tagesbedarf bis {cfg.comfort_temp:.1f} °C")
    tr.check("surplus", surplus_ok,
             f"{usable_w:.0f} W frei für Warmwasser (Schwelle {needed_w:.0f} W)"
             + ("" if bat_fills or not cfg.battery_first else " – Batterie zuerst: nur Einspeisung zählt"))
    tr.check("held", held if surplus_ok else None,
             f"seit {minutes_since(state.start_since, now):.0f} von {cfg.start_delay_min:.0f} min" if surplus_ok else "nicht geprüft")
    if need and held:
        tr.check("out_reheat", True, f"Ziel {cfg.comfort_temp:.1f} °C")
        return (Decision(DhwMode.REHEAT, cfg.comfort_temp,
                         [f"Tagesbedarf mit PV: {usable_w:.0f} W frei, Speicher auf {cfg.comfort_temp:.1f} °C"],
                         metrics, tr), DhwState(DhwMode.REHEAT, now))

    # 2) anti-legionella: due -> with PV like a reserve; overdue -> in the warmest hours anyway
    due, overdue, last = hygiene_status(cfg, prev, now)
    house_free = not inp.heating_season or not inp.heating_boost
    if cfg.hygiene_enabled:
        hyg_need_wh = dhw_energy_wh(cfg, sit.dhw_temp, cfg.hygiene_temp)
        hyg_battery = sit.soc >= cfg.soc_boost_min or sit.battery_fills(cfg.soc_boost_min, cfg.battery_fill_safety,
                                                                        hyg_need_wh)
        midday = 11 <= now.hour < 15
        days = (now - last).total_seconds() / 86400
        tr.check("hygiene_due", due, f"zuletzt {cfg.hygiene_temp:.0f} °C vor {days:.0f} Tagen "
                                     f"(alle {cfg.hygiene_days:.0f}, spätestens nach {cfg.hygiene_max_days:.0f} Tagen)")
        pv_go = due and held and hyg_battery and house_free
        forced = overdue and midday
        if (pv_go or forced) and sit.dhw_temp < cfg.hygiene_temp - 1.0:
            tr.check("out_hygiene", True, f"Ziel {cfg.hygiene_temp:.1f} °C")
            why = (f"{usable_w:.0f} W PV frei" if pv_go else
                   f"seit {days:.0f} Tagen kein PV-Tag – mittags trotzdem (bester COP)")
            return (Decision(DhwMode.HYGIENE, cfg.hygiene_temp,
                             [f"Legionellenschutz: Speicher auf {cfg.hygiene_temp:.0f} °C ({why})"], metrics, tr),
                    DhwState(DhwMode.HYGIENE, now))

    # 3) reserve: only before a dull day, battery fills anyway, house already warm in winter
    tr.check("dull_tomorrow", dull_tomorrow if fc is not None else None,
             f"morgen {fc.tomorrow_wh / 1000:.1f} kWh PV (trüb unter {cfg.poor_day_kwh:.0f} kWh)" if fc is not None
             else "keine Prognose – kein Vorrat")
    tr.check("battery", battery_ok, battery_reason)
    house_ok = not inp.heating_season or not inp.heating_boost
    tr.check("house_first", house_ok, "Heizperiode: Haus wird gerade mit PV vorgeheizt – das hat Vorrang"
             if not house_ok else ("Haus braucht den Überschuss nicht" if inp.heating_season else "keine Heizperiode"))
    worth = sit.dhw_temp < cfg.boost_temp - 2.0
    tr.check("reserve_need", worth, f"Speicher {sit.dhw_temp:.1f} °C, Vorrat bis {cfg.boost_temp:.1f} °C")
    if dull_tomorrow and battery_ok and house_ok and worth and held:
        tr.check("out_boost", True, f"Ziel {cfg.boost_temp:.1f} °C")
        return (Decision(DhwMode.BOOST, cfg.boost_temp,
                         [f"Vorrat vor trübem Tag ({fc.tomorrow_wh / 1000:.1f} kWh morgen): {cfg.boost_temp:.1f} °C",
                          battery_reason], metrics, tr), DhwState(DhwMode.BOOST, now))

    decision, base_mode = _baseline(inp, cfg, fc, tr)
    decision.metrics = metrics
    if surplus_ok and need and not held:
        decision.reasons.insert(0, f"Überschuss {surplus_w:.0f} W – Nachheizen nach {cfg.start_delay_min:.0f} min")
    if base_mode != state.mode:
        state.mode, state.mode_since = base_mode, now
    state.start_since = state.start_since if surplus_ok else None
    return decision, state


def _leave_boost(inp, cfg, fc, metrics, why: str, tr: Trace) -> tuple[Decision, DhwState]:
    decision, mode = _baseline(inp, cfg, fc, tr)
    decision.reasons.insert(0, why)
    decision.metrics = metrics
    return decision, DhwState(mode, inp.now)


def _baseline(inp: DhwInputs, cfg: DhwConfig, fc: ForecastSummary | None, tr: Trace) -> tuple[Decision, DhwMode]:
    """Target when not boosting: wait for the sun if the forecast covers the tank."""
    def done(mode: DhwMode, target: float, reason: str, out: str) -> tuple[Decision, DhwMode]:
        tr.check(out, True, f"Ziel {target:.1f} °C")
        return Decision(mode, target, [reason], trace=tr), mode

    def no_sun(why: str) -> tuple[Decision, DhwMode]:
        tr.check("pv_only", cfg.pv_only, "nur mit PV über das Minimum" if cfg.pv_only else "ausgeschaltet – Komfort")
        if cfg.pv_only:
            return done(DhwMode.PV_ONLY, cfg.min_temp,
                        f"Nur mit PV über {cfg.min_temp:.0f} °C ({why}): Minimum halten, kein Nachheizen aus Netz oder Batterie",
                        "out_pv_only")
        return done(DhwMode.NORMAL, cfg.comfort_temp, f"Komfort ({why})", "out_comfort")

    if not cfg.solar_wait:
        tr.check("sun_today", None, "„Auf Sonne warten“ ist ausgeschaltet")
        return no_sun("Warten auf Sonne aus")
    if fc is None:
        tr.check("sun_today", None, "keine Prognose")
        return no_sun("keine Prognose")
    sit = inp.sit
    need_wh = dhw_energy_wh(cfg, min(sit.dhw_temp or cfg.min_temp, cfg.min_temp), cfg.comfort_temp)
    free_today = sit.free_pv_today_wh
    sun_today = fc.sun_hours_left_today >= 1.0 and free_today >= need_wh * 2
    tr.check("sun_today", sun_today,
             f"heute noch {free_today / 1000:.1f} kWh frei in {fc.sun_hours_left_today:.1f} Sonnenstunden "
             f"(nötig {2 * need_wh / 1000:.1f} kWh)")
    if sun_today:
        return done(DhwMode.SOLAR_WAIT, cfg.min_temp, f"Warten auf Sonne: heute noch {free_today / 1000:.1f} kWh frei",
                    "out_wait")
    evening = fc.sun_hours_left_today < 1.0
    sunny_tomorrow = evening and fc.tomorrow_wh >= cfg.poor_day_kwh * 1000
    tr.check("sun_tomorrow", sunny_tomorrow if evening else None,
             f"morgen {fc.tomorrow_wh / 1000:.1f} kWh PV (guter Tag ab {cfg.poor_day_kwh:.0f} kWh)" if evening
             else "noch Sonnenstunden heute – nicht geprüft")
    if sunny_tomorrow:
        return done(DhwMode.SOLAR_WAIT, cfg.min_temp, f"Warten auf morgen: {fc.tomorrow_wh / 1000:.1f} kWh PV erwartet",
                    "out_wait")
    # night before a dull day: in the evening the coming day is tomorrow, after midnight
    # (nothing of today's PV has come yet) it is today
    # (not `evening` above: on a very dull day that is true all day long)
    before_sunrise = sit.before_sunrise
    night = before_sunrise or sit.after_sunset
    coming_wh = fc.today_wh if before_sunrise else fc.tomorrow_wh
    dull_night = cfg.dull_night_min and night and coming_wh < cfg.poor_day_kwh * 1000
    tr.check("dull_night", dull_night if cfg.dull_night_min and night else None,
             ("ausgeschaltet" if not cfg.dull_night_min else
              f"{'heute' if before_sunrise else 'morgen'} nur {coming_wh / 1000:.1f} kWh PV "
              f"(trüb unter {cfg.poor_day_kwh:.0f} kWh)" if night else "tagsüber – nicht geprüft"))
    if dull_night:
        return done(DhwMode.SAVE, cfg.min_temp,
                    f"Trüber Tag kommt ({coming_wh / 1000:.1f} kWh PV): über Nacht bis {cfg.min_temp:.0f} °C absinken lassen",
                    "out_save")
    return no_sun("zu wenig Sonne erwartet")


def bath_plan(bath: BathRequest, dhw_temp: float | None, cfg: DhwConfig) -> dict:
    """Heat-up time and latest start for the requested bath."""
    need_wh = dhw_energy_wh(cfg, dhw_temp if dhw_temp is not None else cfg.min_temp, bath.temp)
    heat_min = need_wh / cfg.dhw_power_w * 60
    latest = bath.deadline - timedelta(minutes=heat_min * 1.3 + cfg.bath_margin_min)
    return {"deadline": bath.deadline, "temp": bath.temp, "heat_min": heat_min,
            "latest_start": latest, "need_kwh": need_wh / 1000}


def _bath(inp: DhwInputs, cfg: DhwConfig, fc: ForecastSummary | None, state: DhwState, metrics: dict,
          surplus_w: float, deficit_w: float, tr: Trace) -> tuple[Decision, DhwState]:
    """Have bath.temp in the tank by the deadline; use PV surplus first, force at the latest start."""
    now, bath, dhw_temp = inp.now, inp.bath, inp.sit.dhw_temp
    plan = bath_plan(bath, dhw_temp, cfg)
    latest = plan["latest_start"]
    when = bath.deadline.strftime("%H:%M")
    metrics["bath"] = {"time": when, "temp": bath.temp, "latest_start": latest.strftime("%H:%M"),
                       "heat_minutes": round(plan["heat_min"]), "need_kwh": round(plan["need_kwh"], 2)}
    since = state.mode_since if state.mode == DhwMode.BATH else now

    def out(mode: DhwMode, target: float, reason: str, new_state: DhwState) -> tuple[Decision, DhwState]:
        tr.check("out_bath" if mode == DhwMode.BATH else "out_wait", True, f"Ziel {target:.1f} °C")
        return Decision(mode, target, [reason], metrics, tr), new_state

    if tr.check("bath_ready", dhw_temp >= bath.temp - 1.0,
                f"Speicher {dhw_temp:.1f} °C, Bad braucht {bath.temp:.0f} °C"):
        metrics["bath"]["status"] = "bereit"
        return out(DhwMode.BATH, bath.temp, f"Bad um {when}: Speicher bereit ({dhw_temp:.1f} °C), "
                                         f"{bath.temp:.0f} °C werden gehalten", DhwState(DhwMode.BATH, since))
    if tr.check("bath_latest", now >= latest,
                f"spätester Start {latest:%H:%M} (Aufheizen ca. {plan['heat_min']:.0f} min + Puffer)"):
        metrics["bath"]["status"] = "heizt (spätester Start)"
        return out(DhwMode.BATH, bath.temp, f"Bad um {when}: spätester Start {latest:%H:%M} erreicht – "
                                         "heizt jetzt, notfalls aus Batterie/Netz", DhwState(DhwMode.BATH, since))

    # Before the latest start: heat only while PV surplus covers it. The energy is
    # needed before the deadline anyway, so PV now beats battery or grid later,
    # even if the battery isn't full yet.
    needed_w = cfg.start_surplus_ratio * cfg.dhw_power_w
    if state.mode in (DhwMode.BATH, DhwMode.BOOST, DhwMode.REHEAT):   # already heating with PV: carry on as bath heating
        if not state.track_stop(deficit_w, cfg.stop_deficit_w, now, cfg.stop_delay_min, cfg.min_boost_min):
            tr.check("bath_pv", True, f"heizt bereits mit PV, Defizit {deficit_w:.0f} W")
            metrics["bath"]["status"] = "heizt mit PV"
            return out(DhwMode.BATH, bath.temp, f"Bad um {when}: heizt mit PV-Überschuss auf {bath.temp:.0f} °C", state)
        state = DhwState(DhwMode.NORMAL, now)        # surplus gone: pause until it returns or latest start
    start_now = surplus_w >= needed_w
    pv_ok = state.track_start(start_now, now, cfg.start_delay_min)
    tr.check("bath_pv", pv_ok, f"{surplus_w:.0f} W frei (Schwelle {needed_w:.0f} W"
             + (f", seit {minutes_since(state.start_since, now):.0f} von {cfg.start_delay_min:.0f} min)" if start_now else ")"))
    if pv_ok:
        metrics["bath"]["status"] = "heizt mit PV"
        return out(DhwMode.BATH, bath.temp, f"Bad um {when}: PV-Überschuss {surplus_w:.0f} W wird genutzt",
                   DhwState(DhwMode.BATH, now))

    decision, base_mode = _baseline(inp, cfg, fc, tr)
    decision.reasons.insert(0, f"Bad um {when}: wartet auf PV-Überschuss, spätestens ab {latest:%H:%M}")
    decision.metrics = metrics
    metrics["bath"]["status"] = "wartet auf PV"
    if base_mode != state.mode:
        state.mode, state.mode_since = base_mode, now
    return decision, state


# ---- explanation: the steps above, in the order decide() checks them -----------------------
OUTCOMES = {
    "out_off": "Aus → Komforttemperatur", "out_bath": "Bad vorbereiten", "out_reheat": "Tagesbedarf mit PV",
    "out_boost": "Vorrat vor trübem Tag", "out_wait": "Warten auf Sonne", "out_save": "Vor trübem Tag absinken lassen",
    "out_pv_only": "Minimum – nur mit PV heizen", "out_comfort": "Komfort",
    "out_hygiene": "Legionellenschutz",
}
OFF_TEXT = "Die Regelung ist ausgeschaltet: die Wärmepumpe läuft mit der Komforttemperatur."
MODE_LABELS = {  # how the pages name each mode
    DhwMode.REHEAT: "Tagesbedarf mit PV", DhwMode.BOOST: "Vorrat vor trübem Tag", DhwMode.SOLAR_WAIT: "Warten auf Sonne",
    DhwMode.SAVE: "Vor trübem Tag absinken", DhwMode.PV_ONLY: "Minimum – nur mit PV", DhwMode.NORMAL: "Komfort",
    DhwMode.OFF: "Aus", DhwMode.BATH: "Bad vorbereiten", DhwMode.HYGIENE: "Legionellenschutz",
}


def stages(c: DhwConfig) -> list[dict]:
    """The steps of decide() with their questions, for the explanation tab (texts with live settings)."""
    return [
        stage("bath", "bath", "Bad angemeldet?", "bath", "Ein angemeldetes Bad hat Vorrang vor allem anderen.", [
            ("bath_ready", "Ist der Speicher schon warm genug?", "Dann wird die Badtemperatur bis zur Uhrzeit gehalten."),
            ("bath_latest", "Ist der späteste Start erreicht?",
             "Aufheizdauer × 1,3 + Puffer vor der Badezeit: ab dann wird geheizt, notfalls aus Batterie/Netz."),
            ("bath_pv", "Gibt es genug PV-Überschuss?", "Vorher wird nur mit Sonnenstrom geheizt."),
        ], gate="bath", gate_no="nein → weiter"),
        stage("running", "running", "Heizt der Speicher gerade mit PV?", "pv",
              "Ein laufendes Aufheizen endet nicht bei jeder Wolke.", [
                  ("run_full", "Ist das Ziel erreicht?", "Tagesbedarf bzw. Vorrat ist im Speicher."),
                  ("run_deficit", "Kommt zu lange Strom aus Netz/Batterie?",
                   f"Stopp, wenn mehr als {de(c.stop_deficit_w, 0)} W Defizit {de(c.stop_delay_min, 0)} min lang "
                   f"anliegen (nach mind. {de(c.min_boost_min, 0)} min)."),
              ], gate="running", gate_no="nein → weiter"),
        stage("reheat", "need", "Tagesbedarf mit PV nachheizen?", "heatpump",
              f"Bis {de(c.comfort_temp)} °C – auch wenn die Batterie noch lädt: die Wärme wird bis zur nächsten "
              "Sonne ohnehin gebraucht.", [
                  ("need", "Ist der Speicher unter dem Tagesbedarf?", f"Unter {de(c.comfort_temp - 2)} °C."),
                  ("surplus", "Genug Überschuss?",
                   f"Einspeisung + Batterieladung ≥ {de(c.start_surplus_ratio * c.dhw_power_w, 0)} W."),
                  ("held", "Hält das schon eine Weile an?", f"{de(c.start_delay_min, 0)} min am Stück."),
              ], all_needed=True),
        stage("hygiene", "hygiene_due", "Legionellenschutz fällig?", "bath",
              f"Alle {de(c.hygiene_days, 0)} Tage einmal {de(c.hygiene_temp, 0)} °C im Speicher – am liebsten mit "
              f"Sonnenstrom. Kam {de(c.hygiene_max_days, 0)} Tage kein PV-Tag, wird mittags (11–15 Uhr, wärmste Luft) "
              "trotzdem geheizt. Erreicht der Speicher die Temperatur auf anderem Weg (Bad), zählt das auch.", [
                  ("hygiene_due", "Ist das Intervall um?", f"Letztes Mal {de(c.hygiene_temp, 0)} °C erreicht."),
                  ("surplus", "Genug Überschuss?", "Wie beim Nachheizen."),
                  ("held", "Hält der Überschuss an?", f"{de(c.start_delay_min, 0)} min am Stück."),
              ], all_needed=True) if c.hygiene_enabled else
        stage("hygiene", "hygiene_due", "Legionellenschutz", "bath", "Ausgeschaltet.", []),
        stage("reserve", "dull_tomorrow", "Vorrat vor einem trüben Tag?", "battery",
              f"Nur dann lohnt Wärme über den Bedarf ({de(c.boost_temp)} °C). Vor sonnigen Tagen würde sie nur "
              "morgigen Gratis-Strom verdrängen.", [
                  ("dull_tomorrow", "Wird morgen trüb?", f"Prognose unter {de(c.poor_day_kwh, 0)} kWh."),
                  ("battery", "Wird die Batterie trotzdem voll?", f"Über {de(c.soc_boost_min, 0)} % oder laut Prognose."),
                  ("house_first", "Ist das Haus schon versorgt?",
                   "Im Winter geht Vorheizen des Hauses vor (COP 4,6 statt 2,6)."),
                  ("reserve_need", "Ist noch Platz im Speicher?", f"Unter {de(c.boost_temp - 2)} °C."),
                  ("held", "Hält der Überschuss an?", f"{de(c.start_delay_min, 0)} min am Stück."),
              ], all_needed=True),
        stage("base", "sun_today", "Grundzustand", "house",
              "Ohne PV-Heizen entscheidet die Prognose, ob die Wärmepumpe jetzt nachheizen darf.", [
                  ("sun_today", "Kommt heute noch genug Sonne?", f"Dann nur {de(c.min_temp)} °C halten und auf PV warten."),
                  ("sun_tomorrow", "Abends: wird morgen ein guter PV-Tag?",
                   f"Ab {de(c.poor_day_kwh, 0)} kWh: über Nacht nur {de(c.min_temp)} °C."),
                  ("dull_night", "Nachts: kommt ein trüber Tag?",
                   f"Unter {de(c.poor_day_kwh, 0)} kWh: bis {de(c.min_temp)} °C absinken lassen."),
                  ("pv_only", "Nur mit PV über das Minimum?",
                   f"Dann hält die Wärmepumpe nur {de(c.min_temp)} °C – darüber heizt nur PV-Überschuss."),
              ], fallback=(f"Sonst: Minimum {de(c.min_temp)} °C – über das Minimum nur mit PV." if c.pv_only
                           else f"Sonst: Komforttemperatur {de(c.comfort_temp)} °C.")),
    ]
