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

from .i18n import fnum, t
from .forecast import ForecastSummary
from .rules import Decision, RunState, Trace, minutes_since, stage
from .situation import Situation
from .settings import clock, flag, num

WATER_WH_PER_L_K = 1.163
HOUSE_IDLE_W = 100.0                # compressor below this while the house is preheated: the heat pump stands still


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
    enabled: bool = flag(True)
    # False = dry run: decide and log, never write
    apply: bool = flag(False)
    comfort_temp: float = num(46.0, '°C', 10, 65, 0.5)
    min_temp: float = num(42.0, '°C', 10, 65, 0.5)
    # reserve before a dull day (no more daily 55 °C boost)
    boost_temp: float = num(50.0, '°C', 10, 65, 0.5)
    tank_liters: float = num(235.0, 'L', 50, 1000, 5)
    dhw_power_w: float = num(1300.0, 'W', 200, 6000, 50)
    cop: float = num(2.6, '', 1, 6, 0.1)
    start_surplus_ratio: float = num(0.9, '', 0.3, 2, 0.05)
    start_delay_min: float = num(5.0, 'min', 0, 60, 1)
    stop_deficit_w: float = num(300.0, 'W', 0, 5000, 50)
    stop_delay_min: float = num(10.0, 'min', 0, 60, 1)
    min_boost_min: float = num(20.0, 'min', 0, 120, 1)
    soc_boost_min: float = num(95.0, '%', 0, 100, 1)
    # daily PV reheat only once the battery fills (or with export)
    battery_first: bool = flag(True)
    battery_fill_safety: float = num(1.2, '', 1, 3, 0.05)
    base_load_w: float = num(500.0, 'W', 0, 5000, 50)
    poor_day_kwh: float = num(8.0, 'kWh', 0, 100, 0.5)
    solar_wait: bool = flag(True)
    # above min_temp only with PV; otherwise hold the minimum
    pv_only: bool = flag(True)
    # night before a dull day: let the tank drop to min_temp
    dull_night_min: bool = flag(True)
    # hot water power limit (ISG "WW-LEISTUNG", written via the ISG web page; dhwpower.py)
    power_control: bool = flag(True)
    power_normal: float = num(80, '%', 30, 100, 5)
    power_low: float = num(50, '%', 30, 100, 5)
    power_high: float = num(100, '%', 30, 100, 5)
    power_every_min: float = num(30, 'min', 10, 240, 5)
    power_max_writes: float = num(8, '', 1, 24, 1)
    # anti-legionella with PV
    hygiene_enabled: bool = flag(True)
    hygiene_temp: float = num(60.0, '°C', 55, 65, 0.5)
    hygiene_days: float = num(7, 'days', 1, 30, 1)
    hygiene_max_days: float = num(14, 'days', 2, 60, 1)
    hygiene_max_min: float = num(240, 'min', 30, 600, 10)
    bath_temp: float = num(55.0, '°C', 10, 65, 0.5)
    bath_default_time: str = clock("17:30")
    # safety margin before the bath (write interval + reaction)
    bath_margin_min: float = num(20.0, 'min', 0, 120, 5)

    def validate(self) -> None:
        if not 10 <= self.min_temp <= self.comfort_temp <= self.boost_temp <= 65:
            raise ValueError(t("dhw.temperaturen_10_minimum_komfort_vorrat"))
        if not 30 <= self.power_low <= self.power_normal <= self.power_high <= 100:
            raise ValueError(t("dhw.ww_leistung_30_knapp_normal_viel"))
        if self.hygiene_max_days < self.hygiene_days:
            raise ValueError(t("dhw.legionellenschutz_spaetestens_nach_muss"))
        if not self.min_temp <= self.bath_temp <= 65:
            raise ValueError(t("dhw.badtemperatur_zwischen_mindesttemperatur"))
        parse_hhmm(self.bath_default_time)
        if self.cop <= 0 or self.dhw_power_w <= 0 or self.tank_liters <= 0:
            raise ValueError(t("dhw.cop_leistung_und_speichervolumen_muessen"))


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
        raise ValueError(t("dhw.uhrzeit_bitte_als_hh_mm", value=value)) from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(t("dhw.uhrzeit_ungueltig", value=value))
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
        decision.reasons.insert(0, t("dhw.legionellenschutz_c_in_min_nicht", hygiene_temp=fnum(cfg.hygiene_temp, 0), hygiene_max_min=fnum(cfg.hygiene_max_min, 0), temp=fnum(temp, 1), hygiene_days=fnum(cfg.hygiene_days, 0)))
    return decision, state


def _decide(inp: DhwInputs, cfg: DhwConfig, prev: DhwState) -> tuple[Decision, DhwState]:
    sit, now = inp.sit, inp.now
    tr = Trace()
    if not tr.check("enabled", cfg.enabled, t("dhw.regelung_eingeschaltet") if cfg.enabled else t("dhw.regelung_ausgeschaltet")):
        return (Decision(DhwMode.OFF, cfg.comfort_temp, [t("dhw.regelung_aus_komforttemperatur")], trace=tr),
                DhwState(DhwMode.OFF, now))
    if sit.dhw_temp is None:
        tr.check("sensor", False, t("dhw.keine_warmwassertemperatur_vom_geraet"))
        return (Decision(DhwMode.NORMAL, cfg.comfort_temp, [t("dhw.keine_warmwassertemperatur_komfort")], trace=tr),
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
        battery_reason = t("dhw.batterie", soc=fnum(sit.soc, 0), soc_boost_min=fnum(cfg.soc_boost_min, 0))
    elif battery_ok:
        battery_reason = (
            t("dhw.prognose_kwh_frei_reicht_fuer_batterie", free_today_kwh=fnum(free_today_wh / 1000, 1), battery_need_kwh=fnum(battery_need_wh / 1000, 1), reserve_need_kwh=fnum(reserve_need_wh / 1000, 1)))
    else:
        battery_reason = (
            t("dhw.batterie_hat_vorrang", soc=fnum(sit.soc, 0), soc_boost_min=fnum(cfg.soc_boost_min, 0))
            + (t("dhw.prognose_reicht_nicht") if free_today_wh is not None else t("dhw.keine_prognose_2")))

    state = DhwState(prev.mode if prev.mode != DhwMode.OFF else DhwMode.NORMAL,
                     prev.mode_since or now, prev.start_since, prev.stop_since)

    # ---- bath requested: it overrides everything else -----------------------
    bath_active = inp.bath is not None and now < inp.bath.deadline
    tr.check("bath", bath_active, t("dhw.bad_um_angemeldet", deadline=f"{inp.bath.deadline:%H:%M}") if bath_active else t("dhw.kein_bad_angemeldet"))
    if bath_active:
        return _bath(inp, cfg, fc, state, metrics, surplus_w, deficit_w, tr)
    if state.mode == DhwMode.BATH:                       # request finished or cancelled
        state = DhwState(DhwMode.NORMAL, now)

    # ---- a PV heat-up (daily reheat or reserve) is running --------------------
    running = state.mode in (DhwMode.BOOST, DhwMode.REHEAT, DhwMode.HYGIENE)
    hygiene = state.mode == DhwMode.HYGIENE
    goal = {DhwMode.BOOST: cfg.boost_temp, DhwMode.HYGIENE: cfg.hygiene_temp}.get(state.mode, cfg.comfort_temp)
    what = {DhwMode.BOOST: t("dhw.vorrat"), DhwMode.HYGIENE: t("dhw.legionellenschutz")}.get(state.mode, t("dhw.nachheizen"))
    _, overdue, _ = hygiene_status(cfg, prev, now)
    if tr.check("running", running,
                t("dhw.auf_c_laeuft_seit_min", what=what, goal=fnum(goal, 1), minutes=fnum(minutes_since(state.mode_since, now), 0)) if running else t("dhw.kein_pv_heizen_des_speichers_aktiv")):
        if tr.check("run_full", sit.dhw_temp >= goal - 1.0, t("dhw.speicher_c_ziel_c", dhw_temp=fnum(sit.dhw_temp, 1), goal=fnum(goal, 1))):
            return _leave_boost(inp, cfg, fc, metrics, t("dhw.speicher_auf_c_geladen", dhw_temp=fnum(sit.dhw_temp, 1)), tr)
        if hygiene and minutes_since(state.mode_since, now) >= cfg.hygiene_max_min:
            return _leave_boost(inp, cfg, fc, metrics, t("dhw.legionellenschutz_zeit_abgelaufen"), tr)
        stop = state.track_stop(deficit_w, cfg.stop_deficit_w, now, cfg.stop_delay_min, cfg.min_boost_min)
        if hygiene and overdue:
            stop = False                                   # overdue: finish it, PV or not
        tr.check("run_deficit", stop,
                 t("dhw.defizit_w_grenze_w_seit_von", deficit_w=fnum(deficit_w, 0), stop_deficit_w=fnum(cfg.stop_deficit_w, 0), minutes=fnum(minutes_since(state.stop_since, now), 0), stop_delay_min=fnum(cfg.stop_delay_min, 0)))
        if stop:
            return _leave_boost(inp, cfg, fc, metrics,
                                t("dhw.zu_wenig_pv_w_aus_netz", deficit_w=fnum(deficit_w, 0), stop_delay_min=fnum(cfg.stop_delay_min, 0)), tr)
        reasons = [t("dhw.speicher_auf_c", value={DhwMode.BOOST: t("dhw.vorrat_vor_truebem_tag"), DhwMode.HYGIENE: t("dhw.legionellenschutz")}.get(state.mode, t("dhw.tagesbedarf_mit_pv")), goal=fnum(goal, 1))]
        if state.stop_since:
            reasons.append(t("dhw.defizit_w_stopp_nach_min", deficit_w=fnum(deficit_w, 0), stop_delay_min=fnum(cfg.stop_delay_min, 0)))
        tr.check({DhwMode.BOOST: "out_boost", DhwMode.HYGIENE: "out_hygiene"}.get(state.mode, "out_reheat"), True,
                 t("dhw.ziel_c", goal=fnum(goal, 1)))
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
    tr.check("need", need, t("dhw.speicher_c_tagesbedarf_bis_c", dhw_temp=fnum(sit.dhw_temp, 1), comfort_temp=fnum(cfg.comfort_temp, 1)))
    tr.check("surplus", surplus_ok,
             t("dhw.w_frei_fuer_warmwasser_schwelle_w", usable_w=fnum(usable_w, 0), needed_w=fnum(needed_w, 0))
             + ("" if bat_fills or not cfg.battery_first else t("dhw.batterie_zuerst_nur_einspeisung_zaehlt")))
    tr.check("held", held if surplus_ok else None,
             t("dhw.seit_von_min", minutes=fnum(minutes_since(state.start_since, now), 0), start_delay_min=fnum(cfg.start_delay_min, 0)) if surplus_ok else t("dhw.nicht_geprueft"))
    if need and held:
        tr.check("out_reheat", True, t("dhw.ziel_c_2", comfort_temp=fnum(cfg.comfort_temp, 1)))
        return (Decision(DhwMode.REHEAT, cfg.comfort_temp,
                         [t("dhw.tagesbedarf_mit_pv_w_frei_speicher", usable_w=fnum(usable_w, 0), comfort_temp=fnum(cfg.comfort_temp, 1))],
                         metrics, tr), DhwState(DhwMode.REHEAT, now))

    # 2) anti-legionella: due -> with PV like a reserve; overdue -> in the warmest hours anyway
    due, overdue, last = hygiene_status(cfg, prev, now)
    # the house comes first only while the heat pump really works for it: a preheat that stands still
    # (target reached, no demand) would just leave the surplus to the grid
    house_idle = inp.heating_boost and sit.compressor_w is not None and sit.compressor_w < HOUSE_IDLE_W
    house_free = not inp.heating_season or not inp.heating_boost or house_idle
    if cfg.hygiene_enabled:
        hyg_need_wh = dhw_energy_wh(cfg, sit.dhw_temp, cfg.hygiene_temp)
        hyg_battery = sit.soc >= cfg.soc_boost_min or sit.battery_fills(cfg.soc_boost_min, cfg.battery_fill_safety,
                                                                        hyg_need_wh)
        midday = 11 <= now.hour < 15
        days = (now - last).total_seconds() / 86400
        tr.check("hygiene_due", due, t("dhw.zuletzt_c_vor_tagen_alle_spaetestens", hygiene_temp=fnum(cfg.hygiene_temp, 0), days=fnum(days, 0), hygiene_days=fnum(cfg.hygiene_days, 0), hygiene_max_days=fnum(cfg.hygiene_max_days, 0)))
        pv_go = due and held and hyg_battery and house_free
        forced = overdue and midday
        if (pv_go or forced) and sit.dhw_temp < cfg.hygiene_temp - 1.0:
            tr.check("out_hygiene", True, t("dhw.ziel_c_5", hygiene_temp=fnum(cfg.hygiene_temp, 1)))
            why = (t("dhw.w_pv_frei", usable_w=fnum(usable_w, 0)) if pv_go else
                   t("dhw.seit_tagen_kein_pv_tag_mittags", days=fnum(days, 0)))
            return (Decision(DhwMode.HYGIENE, cfg.hygiene_temp,
                             [t("dhw.legionellenschutz_speicher_auf_c", hygiene_temp=fnum(cfg.hygiene_temp, 0), why=why)], metrics, tr),
                    DhwState(DhwMode.HYGIENE, now))

    # 3) reserve: only before a dull day, battery fills anyway, house already warm in winter
    tr.check("dull_tomorrow", dull_tomorrow if fc is not None else None,
             t("dhw.morgen_kwh_pv_trueb_unter_kwh", tomorrow_kwh=fnum(fc.tomorrow_wh / 1000, 1), poor_day_kwh=fnum(cfg.poor_day_kwh, 0)) if fc is not None
             else t("dhw.keine_prognose_kein_vorrat"))
    tr.check("battery", battery_ok, battery_reason)
    house_ok = house_free
    tr.check("house_first", house_ok, t("dhw.heizperiode_haus_wird_gerade_mit_pv") if not house_ok
             else t("dhw.vorheizen_ruht_waermepumpe_steht", compressor_w=fnum(sit.compressor_w, 0)) if inp.heating_season and house_idle
             else (t("dhw.haus_braucht_den_ueberschuss_nicht") if inp.heating_season else t("dhw.keine_heizperiode")))
    worth = sit.dhw_temp < cfg.boost_temp - 2.0
    tr.check("reserve_need", worth, t("dhw.speicher_c_vorrat_bis_c", dhw_temp=fnum(sit.dhw_temp, 1), boost_temp=fnum(cfg.boost_temp, 1)))
    if dull_tomorrow and battery_ok and house_ok and worth and held:
        tr.check("out_boost", True, t("dhw.ziel_c_3", boost_temp=fnum(cfg.boost_temp, 1)))
        return (Decision(DhwMode.BOOST, cfg.boost_temp,
                         [t("dhw.vorrat_vor_truebem_tag_kwh_morgen", tomorrow_kwh=fnum(fc.tomorrow_wh / 1000, 1), boost_temp=fnum(cfg.boost_temp, 1)),
                          battery_reason], metrics, tr), DhwState(DhwMode.BOOST, now))

    decision, base_mode = _baseline(inp, cfg, fc, tr)
    decision.metrics = metrics
    if surplus_ok and need and not held:
        decision.reasons.insert(0, t("dhw.ueberschuss_w_nachheizen_nach_min", surplus_w=fnum(surplus_w, 0), start_delay_min=fnum(cfg.start_delay_min, 0)))
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
        tr.check(out, True, t("dhw.ziel_c_4", target=fnum(target, 1)))
        return Decision(mode, target, [reason], trace=tr), mode

    def no_sun(why: str) -> tuple[Decision, DhwMode]:
        tr.check("pv_only", cfg.pv_only, t("dhw.nur_mit_pv_ueber_das_minimum") if cfg.pv_only else t("dhw.ausgeschaltet_komfort"))
        if cfg.pv_only:
            return done(DhwMode.PV_ONLY, cfg.min_temp,
                        t("dhw.nur_mit_pv_ueber_c_minimum", min_temp=fnum(cfg.min_temp, 0), why=why),
                        "out_pv_only")
        return done(DhwMode.NORMAL, cfg.comfort_temp, t("dhw.komfort", why=why), "out_comfort")

    if not cfg.solar_wait:
        tr.check("sun_today", None, t("dhw.auf_sonne_warten_ist_ausgeschaltet"))
        return no_sun(t("dhw.warten_auf_sonne_aus"))
    if fc is None:
        tr.check("sun_today", None, t("dhw.keine_prognose"))
        return no_sun(t("dhw.keine_prognose"))
    sit = inp.sit
    need_wh = dhw_energy_wh(cfg, min(sit.dhw_temp or cfg.min_temp, cfg.min_temp), cfg.comfort_temp)
    free_today = sit.free_pv_today_wh
    sun_today = fc.sun_hours_left_today >= 1.0 and free_today >= need_wh * 2
    tr.check("sun_today", sun_today,
             t("dhw.heute_noch_kwh_frei_in_sonnenstunden", free_today_k=fnum(free_today / 1000, 1), sun_hours_left_today=fnum(fc.sun_hours_left_today, 1), value=fnum(2 * need_wh / 1000, 1)))
    if sun_today:
        return done(DhwMode.SOLAR_WAIT, cfg.min_temp, t("dhw.warten_auf_sonne_heute_noch_kwh", free_today_k=fnum(free_today / 1000, 1)),
                    "out_wait")
    evening = fc.sun_hours_left_today < 1.0
    sunny_tomorrow = evening and fc.tomorrow_wh >= cfg.poor_day_kwh * 1000
    tr.check("sun_tomorrow", sunny_tomorrow if evening else None,
             t("dhw.morgen_kwh_pv_guter_tag_ab", tomorrow_kwh=fnum(fc.tomorrow_wh / 1000, 1), poor_day_kwh=fnum(cfg.poor_day_kwh, 0)) if evening
             else t("dhw.noch_sonnenstunden_heute_nicht_geprueft"))
    if sunny_tomorrow:
        return done(DhwMode.SOLAR_WAIT, cfg.min_temp, t("dhw.warten_auf_morgen_kwh_pv_erwartet", tomorrow_kwh=fnum(fc.tomorrow_wh / 1000, 1)),
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
              t("dhw.nur_kwh_pv_trueb_unter_kwh", value=t("dhw.heute") if before_sunrise else t("dhw.morgen"), coming_kwh=fnum(coming_wh / 1000, 1), poor_day_kwh=fnum(cfg.poor_day_kwh, 0)) if night else t("dhw.tagsueber_nicht_geprueft")))
    if dull_night:
        return done(DhwMode.SAVE, cfg.min_temp,
                    t("dhw.trueber_tag_kommt_kwh_pv_ueber", coming_kwh=fnum(coming_wh / 1000, 1), min_temp=fnum(cfg.min_temp, 0)),
                    "out_save")
    return no_sun(t("dhw.zu_wenig_sonne_erwartet"))


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
        tr.check("out_bath" if mode == DhwMode.BATH else "out_wait", True, t("dhw.ziel_c_4", target=fnum(target, 1)))
        return Decision(mode, target, [reason], metrics, tr), new_state

    if tr.check("bath_ready", dhw_temp >= bath.temp - 1.0,
                t("dhw.speicher_c_bad_braucht_c", dhw_temp=fnum(dhw_temp, 1), temp=fnum(bath.temp, 0))):
        metrics["bath"]["status"] = "bereit"
        return out(DhwMode.BATH, bath.temp, t("dhw.bad_um_speicher_bereit_c_c", when=when, dhw_temp=fnum(dhw_temp, 1), temp=fnum(bath.temp, 0)), DhwState(DhwMode.BATH, since))
    if tr.check("bath_latest", now >= latest,
                t("dhw.spaetester_start_aufheizen_ca_min_puffer", latest=f"{latest:%H:%M}", heat_min=fnum(plan['heat_min'], 0))):
        metrics["bath"]["status"] = t("dhw.heizt_spaetester_start")
        return out(DhwMode.BATH, bath.temp, t("dhw.bad_um_spaetester_start_erreicht_heizt", when=when, latest=f"{latest:%H:%M}"), DhwState(DhwMode.BATH, since))

    # Before the latest start: heat only while PV surplus covers it. The energy is
    # needed before the deadline anyway, so PV now beats battery or grid later,
    # even if the battery isn't full yet.
    needed_w = cfg.start_surplus_ratio * cfg.dhw_power_w
    if state.mode in (DhwMode.BATH, DhwMode.BOOST, DhwMode.REHEAT):   # already heating with PV: carry on as bath heating
        if not state.track_stop(deficit_w, cfg.stop_deficit_w, now, cfg.stop_delay_min, cfg.min_boost_min):
            tr.check("bath_pv", True, t("dhw.heizt_bereits_mit_pv_defizit_w", deficit_w=fnum(deficit_w, 0)))
            metrics["bath"]["status"] = t("dhw.heizt_mit_pv")
            return out(DhwMode.BATH, bath.temp, t("dhw.bad_um_heizt_mit_pv_ueberschuss", when=when, temp=fnum(bath.temp, 0)), state)
        state = DhwState(DhwMode.NORMAL, now)        # surplus gone: pause until it returns or latest start
    start_now = surplus_w >= needed_w
    pv_ok = state.track_start(start_now, now, cfg.start_delay_min)
    tr.check("bath_pv", pv_ok, t("dhw.w_frei_schwelle_w", surplus_w=fnum(surplus_w, 0), needed_w=fnum(needed_w, 0))
             + (t("dhw.seit_von_min_2", minutes=fnum(minutes_since(state.start_since, now), 0), start_delay_min=fnum(cfg.start_delay_min, 0)) if start_now else ")"))
    if pv_ok:
        metrics["bath"]["status"] = t("dhw.heizt_mit_pv")
        return out(DhwMode.BATH, bath.temp, t("dhw.bad_um_pv_ueberschuss_w_wird", when=when, surplus_w=fnum(surplus_w, 0)),
                   DhwState(DhwMode.BATH, now))

    decision, base_mode = _baseline(inp, cfg, fc, tr)
    decision.reasons.insert(0, t("dhw.bad_um_wartet_auf_pv_ueberschuss", when=when, latest=f"{latest:%H:%M}"))
    decision.metrics = metrics
    metrics["bath"]["status"] = t("dhw.wartet_auf_pv")
    if base_mode != state.mode:
        state.mode, state.mode_since = base_mode, now
    return decision, state


# ---- explanation: the steps above, in the order decide() checks them -----------------------
OUTCOMES = ('out_off', 'out_bath', 'out_reheat', 'out_boost', 'out_wait', 'out_save', 'out_pv_only', 'out_comfort', 'out_hygiene')   # trace nodes that end a decision: texts "dhw.outcome.<node>"


def outcome_texts() -> dict[str, str]:
    return {node: t(f"dhw.outcome.{node}") for node in OUTCOMES}


def off_text() -> str:
    return t("dhw.off")


def mode_label(mode: DhwMode) -> str:
    """How the pages name each mode (text "dhw.mode.<mode>")."""
    return t(f"dhw.mode.{mode.value}")


def stages(c: DhwConfig) -> list[dict]:
    """The steps of decide() with their questions, for the explanation tab (texts with live settings)."""
    return [
        stage("bath", "bath", t("dhw.bad_angemeldet"), "bath", t("dhw.ein_angemeldetes_bad_hat_vorrang_vor"), [
            ("bath_ready", t("dhw.ist_der_speicher_schon_warm_genug"), t("dhw.dann_wird_die_badtemperatur_bis_zur")),
            ("bath_latest", t("dhw.ist_der_spaeteste_start_erreicht"),
             t("dhw.aufheizdauer_1_3_puffer_vor_der")),
            ("bath_pv", t("dhw.gibt_es_genug_pv_ueberschuss"), t("dhw.vorher_wird_nur_mit_sonnenstrom_geheizt")),
        ], gate="bath", gate_no=t("dhw.nein_weiter")),
        stage("running", "running", t("dhw.heizt_der_speicher_gerade_mit_pv"), "pv",
              t("dhw.ein_laufendes_aufheizen_endet_nicht_bei"), [
                  ("run_full", t("dhw.ist_das_ziel_erreicht"), t("dhw.tagesbedarf_bzw_vorrat_ist_im_speicher")),
                  ("run_deficit", t("dhw.kommt_zu_lange_strom_aus_netz"),
                   t("dhw.stopp_wenn_mehr_als_w_defizit", stop_deficit_w=fnum(c.stop_deficit_w, 0), stop_delay_min=fnum(c.stop_delay_min, 0), min_boost_min=fnum(c.min_boost_min, 0))),
              ], gate="running", gate_no=t("dhw.nein_weiter")),
        stage("reheat", "need", t("dhw.tagesbedarf_mit_pv_nachheizen"), "heatpump",
              t("dhw.bis_c_auch_wenn_die_batterie", comfort_temp=fnum(c.comfort_temp)), [
                  ("need", t("dhw.ist_der_speicher_unter_dem_tagesbedarf"), t("dhw.unter_c", comfort_temp=fnum(c.comfort_temp - 2))),
                  ("surplus", t("dhw.genug_ueberschuss"),
                   t("dhw.einspeisung_batterieladung_w", start_surplus_ratio=fnum(c.start_surplus_ratio * c.dhw_power_w, 0))),
                  ("held", t("dhw.haelt_das_schon_eine_weile_an"), t("dhw.min_am_stueck", start_delay_min=fnum(c.start_delay_min, 0))),
              ], all_needed=True),
        stage("hygiene", "hygiene_due", t("dhw.legionellenschutz_faellig"), "bath",
              t("dhw.alle_tage_einmal_c_im_speicher", hygiene_days=fnum(c.hygiene_days, 0), hygiene_temp=fnum(c.hygiene_temp, 0), hygiene_max_days=fnum(c.hygiene_max_days, 0)), [
                  ("hygiene_due", t("dhw.ist_das_intervall_um"), t("dhw.letztes_mal_c_erreicht", hygiene_temp=fnum(c.hygiene_temp, 0))),
                  ("surplus", t("dhw.genug_ueberschuss"), t("dhw.wie_beim_nachheizen")),
                  ("held", t("dhw.haelt_der_ueberschuss_an"), t("dhw.min_am_stueck", start_delay_min=fnum(c.start_delay_min, 0))),
              ], all_needed=True) if c.hygiene_enabled else
        stage("hygiene", "hygiene_due", t("dhw.legionellenschutz"), "bath", t("dhw.ausgeschaltet"), []),
        stage("reserve", "dull_tomorrow", t("dhw.vorrat_vor_einem_trueben_tag"), "battery",
              t("dhw.nur_dann_lohnt_waerme_ueber_den", boost_temp=fnum(c.boost_temp)), [
                  ("dull_tomorrow", t("dhw.wird_morgen_trueb"), t("dhw.prognose_unter_kwh", poor_day_kwh=fnum(c.poor_day_kwh, 0))),
                  ("battery", t("dhw.wird_die_batterie_trotzdem_voll"), t("dhw.ueber_oder_laut_prognose", soc_boost_min=fnum(c.soc_boost_min, 0))),
                  ("house_first", t("dhw.ist_das_haus_schon_versorgt"),
                   t("dhw.im_winter_geht_vorheizen_des_hauses")),
                  ("reserve_need", t("dhw.ist_noch_platz_im_speicher"), t("dhw.unter_c_2", boost_temp=fnum(c.boost_temp - 2))),
                  ("held", t("dhw.haelt_der_ueberschuss_an"), t("dhw.min_am_stueck", start_delay_min=fnum(c.start_delay_min, 0))),
              ], all_needed=True),
        stage("base", "sun_today", t("dhw.grundzustand"), "house",
              t("dhw.ohne_pv_heizen_entscheidet_die_prognose"), [
                  ("sun_today", t("dhw.kommt_heute_noch_genug_sonne"), t("dhw.dann_nur_c_halten_und_auf", min_temp=fnum(c.min_temp))),
                  ("sun_tomorrow", t("dhw.abends_wird_morgen_ein_guter_pv"),
                   t("dhw.ab_kwh_ueber_nacht_nur_c", poor_day_kwh=fnum(c.poor_day_kwh, 0), min_temp=fnum(c.min_temp))),
                  ("dull_night", t("dhw.nachts_kommt_ein_trueber_tag"),
                   t("dhw.unter_kwh_bis_c_absinken_lassen", poor_day_kwh=fnum(c.poor_day_kwh, 0), min_temp=fnum(c.min_temp))),
                  ("pv_only", t("dhw.nur_mit_pv_ueber_das_minimum_2"),
                   t("dhw.dann_haelt_die_waermepumpe_nur_c", min_temp=fnum(c.min_temp))),
              ], fallback=(t("dhw.sonst_minimum_c_ueber_das_minimum", min_temp=fnum(c.min_temp)) if c.pv_only
                           else t("dhw.sonst_komforttemperatur_c", comfort_temp=fnum(c.comfort_temp)))),
    ]
