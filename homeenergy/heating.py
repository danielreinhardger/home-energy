"""PV-led space heating: pure decision logic, no I/O.

The house is a good heat store (KfW40+, floor heating screed, concrete slab), but on
this site a full battery comes first: during the day the battery should fill, because
household devices empty it every evening. The heating therefore works in this order:

  HOLD       Battery first: PV is producing, but the battery won't fill today (SOC below
             soc_full and too little free PV in the forecast). Hold the normal room target:
             no preheating that would take the PV the battery needs, but no cooling below
             normal either (the user chose comfort over the last bit of battery).
  BOOST      Surplus beyond what the battery needs (battery full, or the forecast says it
             fills anyway): preheat the house. Fine-grained: start just above the current
             room temperature (low compressor speed), then move the target in 0.1-K steps
             every few minutes – up while power still goes to the grid, down when the house
             draws from grid or battery. The inverter compressor follows the gap between
             target and room, so this steers its power towards the actual surplus.
             The ceiling is the forecast-scaled buffer (normal … room_max).
  SETBACK    No PV now, sun is coming: room_min, the stored heat carries the house.
  NORMAL     Otherwise: the normal room temperature.

Against flip-flopping (seen live: 20 ↔ 22 °C six times a day) the passive modes NORMAL,
SETBACK and HOLD change at most every `mode_min_min`, and the three thresholds that decide
between them have a margin once a mode is reached: the heating season ends only 1 K above
`outside_max`, a running setback keeps waiting for tomorrow's sun down to 85 % of
`poor_day_kwh`, and "PV is producing" ends below 100 W (starts at 300 W).

The LWZ 5 S Plus has no power setpoint over Modbus/ISG; the room target is the lever.
A requested bath has priority (it needs the heat pump for hot water).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .rules import Decision, RunState, Trace, de, held, minutes_since, stage
from .situation import Situation
from .settings import flag, num


class HeatMode(str, Enum):
    OFF = "off"
    NORMAL = "normal"
    SETBACK = "solar_wait"     # no PV now, sun is coming: let the stored heat carry the house
    BOOST = "boost"            # preheating with PV
    HOLD = "hold"              # battery first: no preheating while the battery fills


@dataclass
class HeatState(RunState):
    MODE = HeatMode
    mode: HeatMode = HeatMode.NORMAL
    target: float | None = None           # last target (fine control steps from here)
    adjusted_at: datetime | None = None   # when that target was last stepped


@dataclass
class HeatingConfig:
    enabled: bool = flag(True, 'PV-Heizen aktiv', 'Im Winter PV-Überschuss als Wärme im Haus speichern.')
    # False = test mode: decide and log, never write
    apply: bool = flag(False, 'An Wärmepumpe senden', 'Aus = Testbetrieb: Entscheidungen werden nur angezeigt.')
    # the room target you normally want (Raumtemp. Tag HK1)
    normal_temp: float = num(22.0, 'Normale Raumtemperatur', '°C', 10, 30, 0.5,
                             'Was ihr normalerweise wollt (Raumtemp. Tag HK1).')
    # comfort floor: battery first, cold evenings, nights
    room_min: float = num(20.0, 'Minimum ohne PV', '°C', 10, 30, 0.5,
                          'Abends/nachts, wenn Sonne kommt: bis hierher darf es abkühlen, bevor die Wärmepumpe heizt.')
    # highest buffer temperature with PV
    room_max: float = num(24.0, 'Maximum als Wärmepuffer', '°C', 10, 30, 0.5,
                          'Bis hierher darf PV das Haus aufheizen – je mehr Sonne noch kommt, desto höher.')
    # heat the house stores per K (estimate, docs/strategy.md)
    house_kwh_per_k: float = num(20.0, 'Speicherfähigkeit Haus', 'kWh/K', 2, 100, 1,
                                 'Schätzung; bestimmt, wie viel PV ein voller Puffer braucht.')
    # where the real room temperature comes from (the LWZ has no room sensor of its own)
    room_sensor_max_age_min: float = num(180, 'Raumfühler gilt', 'min', 15, 1440, 15,
                                         'So lange zählt die letzte Meldung des Raumfühlers. Fühler meldet an '
                                         '/api/sensor/room?t=…&h=… (Shelly H&T: Webhook, siehe README). '
                                         'Ohne gültigen Wert heizt PV bis zum Puffer-Ziel, ohne „Puffer voll“-Prüfung.')
    room_from_isg: bool = flag(False, 'Raumtemperatur von der Wärmepumpe',
                               'Nur mit Stiebel-Raumbedienung (FET). Ohne sie meldet die Wärmepumpe nur ihr Raum-Soll zurück.')
    # PV forecast below this = no sun worth waiting for
    poor_day_kwh: float = num(8.0, 'Trüber Tag unter', 'kWh', 0, 100, 0.5,
                              'Dann keine Absenkung – es kommt keine Sonne.')
    # heating season: outside colder than this
    outside_max: float = num(15.0, 'Heizperiode unter', '°C', 0, 25, 0.5, 'Nur wenn es draußen kälter ist.')
    # against flip-flopping between normal, setback and battery first
    mode_min_min: float = num(45.0, 'Mindestdauer je Betriebsart', 'min', 0, 240, 5,
                              'Normal, Absenkung und „Batterie zuerst“ wechseln frühestens nach dieser Zeit – '
                              'keine Sprünge bei Wolken oder schwankender Prognose.')
    # PV fills the battery before the house is preheated
    battery_first: bool = flag(True, 'Batterie zuerst',
                               'Tagsüber wird erst die Batterie voll. Solange sie das heute nicht schafft, wird nicht vorgeheizt – '
                               'das Haus bleibt bei der normalen Raumtemperatur.')
    # battery counts as full from here
    soc_full: float = num(95.0, 'Batterie gilt als voll ab', '%', 50, 100, 1)
    battery_fill_safety: float = num(1.2, 'Sicherheitsfaktor Batterie', '', 1, 3, 0.05,
                                     'Prognose muss den Batteriebedarf so oft decken, bevor das Haus Vorrang bekommt.')
    # export/charging beyond the battery's need to start preheating
    start_surplus_w: float = num(800.0, 'Startschwelle Überschuss', 'W', 200, 8000, 50)
    start_delay_min: float = num(10.0, 'Startverzögerung', 'min', 0, 60, 1)
    # first target = room + this (slow compressor start)
    start_offset_k: float = num(0.3, 'Sanfter Start: Raum +', 'K', 0.1, 2, 0.1,
                                'Das erste Ziel liegt knapp über der Raumtemperatur – der Verdichter läuft langsam an.')
    # fine adjustment per step
    step_k: float = num(0.2, 'Schrittweite Feinregelung', 'K', 0.1, 1, 0.1,
                        'Kleine Schritte = feine Leistung: die Inverter-WP moduliert mit dem Abstand Ziel–Raum.')
    # at most one adjustment per this many minutes
    adjust_every_min: float = num(10.0, 'Nachführen alle', 'min', 5, 60, 1)
    # still exporting this much -> step up
    raise_export_w: float = num(300.0, 'Höher bei Einspeisung über', 'W', 0, 5000, 50)
    # drawing this much from grid/battery -> step down
    lower_deficit_w: float = num(200.0, 'Tiefer bei Netz-/Batteriebezug über', 'W', 0, 5000, 50)
    stop_deficit_w: float = num(300.0, 'Stopp bei Netz-/Batteriebezug über', 'W', 0, 5000, 50)
    stop_delay_min: float = num(15.0, 'Stoppverzögerung', 'min', 0, 60, 1)
    min_boost_min: float = num(30.0, 'Mindestdauer Anhebung', 'min', 0, 240, 5)
    # True: also yield to PV hot water heating
    dhw_first: bool = flag(False, 'Warmwasser zuerst',
                           'Aus = Winter-Reihenfolge: erst Haus, dann Warmwasser (nur ein Bad hat immer Vorrang).')
    min_write_interval_min: float = num(10.0, 'Min. Abstand zwischen Schreibvorgängen', 'min', 5, 120, 1)
    max_writes_per_day: int = num(30, 'Max. Schreibvorgänge pro Tag', '', 1, 48, 1)

    def validate(self) -> None:
        if not 10 <= self.room_min <= self.normal_temp <= self.room_max <= 30:
            raise ValueError("Raumtemperaturen: 10 ≤ Minimum ≤ Normal ≤ Maximum ≤ 30 °C")
        if self.house_kwh_per_k <= 0:
            raise ValueError("Speicherfähigkeit des Hauses muss positiv sein")
        if not 0.1 <= self.step_k <= 1:
            raise ValueError("Schrittweite: 0,1 bis 1 K")


@dataclass
class HeatingInputs:
    sit: Situation
    dhw_mode: str | None = None         # mode of the hot water controller ("bath", "reheat", ...)
    cop: float = 4.6                    # heating COP (live model)

    @property
    def now(self) -> datetime:
        return self.sit.now


def _r(v: float) -> float:
    return round(v * 10) / 10


def buffer_target(inp: HeatingInputs, cfg: HeatingConfig) -> tuple[float, str]:
    """How warm the house may get with PV: the more free PV still comes today, the fuller the buffer."""
    span = cfg.room_max - cfg.normal_temp
    if span <= 0:
        return cfg.normal_temp, "kein Puffer eingestellt"
    sit = inp.sit
    full_wh = span * cfg.house_kwh_per_k * 1000 / max(1.0, inp.cop)       # electricity for the whole span
    dull_tomorrow = sit.tomorrow_wh is not None and sit.tomorrow_wh < cfg.poor_day_kwh * 1000
    if dull_tomorrow:
        share, why = 1.0, "morgen trüb – voller Puffer"
    elif sit.free_pv_today_wh is None:
        share, why = 0.5, "keine Prognose – halber Puffer"
    else:
        free = max(0.0, sit.free_pv_today_wh - sit.battery_need_wh)       # after the battery
        share = min(1.0, free / full_wh)
        why = f"nach der Batterie noch {free / 1000:.1f} kWh PV frei, voller Puffer braucht {full_wh / 1000:.1f} kWh"
    return min(cfg.room_max, _r(cfg.normal_temp + max(0.3, span * share))), why


def battery_ok(inp: HeatingInputs, cfg: HeatingConfig) -> tuple[bool, str]:
    """Will the battery be full today even if the house takes PV now?"""
    sit = inp.sit
    free, need = sit.free_pv_today_wh, sit.battery_need_wh
    if sit.soc >= cfg.soc_full:
        return True, f"Batterie {sit.soc:.0f} % – voll"
    if sit.battery_fills(cfg.soc_full, cfg.battery_fill_safety):
        return True, (f"Prognose: {free / 1000:.1f} kWh PV frei, Batterie braucht "
                      f"{need / 1000:.1f} kWh – wird trotzdem voll")
    return False, (f"Batterie {sit.soc:.0f} % wird heute nicht voll" +
                   (f" (frei {free / 1000:.1f} kWh, braucht {need / 1000:.1f} kWh)"
                    if free is not None else " (keine Prognose)"))


PASSIVE = (HeatMode.NORMAL, HeatMode.SETBACK, HeatMode.HOLD)
SEASON_MODES = (HeatMode.SETBACK, HeatMode.HOLD, HeatMode.BOOST)
SEASON_MARGIN_K = 1.0
TOMORROW_KEEP = 0.85
PV_ON_W, PV_OFF_W = 300, 100


def decide_heating(inp: HeatingInputs, cfg: HeatingConfig, prev: HeatState) -> tuple[Decision, HeatState]:
    """The rules below, plus a minimum time per passive mode: NORMAL / SETBACK / HOLD switch among
    themselves only after `mode_min_min` (preheating, a bath and switching off are never delayed)."""
    decision, state = _decide(inp, cfg, prev)
    now, tr = inp.now, decision.trace
    if (prev.mode in PASSIVE and decision.mode in PASSIVE and decision.mode != prev.mode
            and prev.target is not None and prev.mode_since is not None and cfg.mode_min_min > 0):
        age = minutes_since(prev.mode_since, now)
        if age < cfg.mode_min_min:
            tr.check("h_dwell", False, f"„{MODE_LABELS[prev.mode]}“ erst seit {age:.0f} von {cfg.mode_min_min:.0f} min "
                                       f"– Wechsel zu „{MODE_LABELS[decision.mode]}“ wartet")
            kept = HeatState(prev.mode, prev.mode_since, state.start_since, state.stop_since, prev.target,
                             prev.adjusted_at)
            reason = (f"{MODE_LABELS[prev.mode]} bleibt noch {cfg.mode_min_min - age:.0f} min "
                      f"(dann: {decision.reasons[0]})")
            return Decision(prev.mode, prev.target, [reason], decision.metrics, tr), kept
    tr.check("h_dwell", True, "kein Wechsel" if decision.mode == prev.mode else "Wechsel erlaubt")
    return decision, state


def _decide(inp: HeatingInputs, cfg: HeatingConfig, prev: HeatState) -> tuple[Decision, HeatState]:
    sit, now, tr = inp.sit, inp.now, Trace()
    cap, buffer_why = buffer_target(inp, cfg)
    normal, lowered = cfg.normal_temp, cfg.room_min
    surplus_w, export_w, deficit_w = sit.surplus_w, sit.export_w, sit.deficit_w
    bat_ok, bat_why = battery_ok(inp, cfg)
    # with battery first, only what the battery doesn't need counts as surplus for the house
    usable_w = surplus_w if (bat_ok or not cfg.battery_first) else export_w
    metrics = {"surplus_w": round(surplus_w), "usable_w": round(usable_w), "deficit_w": round(deficit_w),
               "outside_temp": sit.outside_temp, "room_temp": sit.room_temp, "boost_target": cap,
               "setback_target": lowered, "buffer_reason": buffer_why, "battery_ok": bat_ok,
               "compressor_w": sit.compressor_w}

    def out(mode: HeatMode, target: float, reason: str, node: str, state: HeatState | None = None) -> tuple[Decision, HeatState]:
        tr.check(node, True, f"Ziel {target:.1f} °C")
        st = state or HeatState(mode, prev.mode_since if prev.mode == mode else now)
        st.target = target
        return Decision(mode, target, [reason], metrics, tr), st

    if not tr.check("h_enabled", cfg.enabled, "PV-Heizen eingeschaltet" if cfg.enabled else "PV-Heizen ausgeschaltet"):
        return out(HeatMode.OFF, normal, "PV-Heizen aus: normale Raumtemperatur", "h_out_normal")
    in_season = prev.mode in SEASON_MODES                     # already heating-season behaviour: 1 K margin
    season_max = cfg.outside_max + (SEASON_MARGIN_K if in_season else 0.0)
    season = sit.outside_temp is not None and sit.outside_temp < season_max
    tr.check("h_season", season, "keine Außentemperatur" if sit.outside_temp is None else
             f"außen {sit.outside_temp:.1f} °C (Heizperiode unter {de(season_max)} °C"
             + (", +1 K weil schon Heizbetrieb)" if in_season else ")"))
    if not season:
        return out(HeatMode.NORMAL, normal, "Keine Heizperiode: normale Raumtemperatur", "h_out_normal")

    yield_dhw = inp.dhw_mode == "bath" or (cfg.dhw_first and inp.dhw_mode in ("boost", "reheat"))
    room = sit.room_temp
    pv_now = sit.pv_w >= (PV_ON_W if prev.mode != HeatMode.HOLD else PV_OFF_W)   # clouds: no on/off per minute
    state = HeatState(prev.mode if prev.mode != HeatMode.OFF else HeatMode.NORMAL,
                      prev.mode_since or now, prev.start_since, prev.stop_since, prev.target, prev.adjusted_at)

    # ---- battery first: while PV produces and the battery won't fill, the house waits --------
    hold = cfg.battery_first and pv_now and not bat_ok
    tr.check("h_battery_first", hold if cfg.battery_first else None,
             ("PV läuft, aber " + bat_why) if hold else (bat_why if pv_now else "kein PV gerade"))
    if hold:
        reason = f"Batterie zuerst: {bat_why} – kein Vorheizen, Haus bleibt bei {normal:.1f} °C"
        return out(HeatMode.HOLD, normal, reason, "h_out_hold")

    # ---- preheating with PV: follow the surplus in small steps --------------------------------
    if tr.check("h_boosting", state.mode == HeatMode.BOOST,
                f"Vorheizen läuft seit {minutes_since(state.mode_since, now):.0f} min, Ziel {state.target or 0:.1f} °C"
                if state.mode == HeatMode.BOOST else "kein Vorheizen aktiv"):
        if tr.check("h_dhw", yield_dhw, "Bad wird vorbereitet – hat Vorrang" if yield_dhw else "kein Vorrang für Warmwasser"):
            return out(HeatMode.NORMAL, normal, "Bad hat Vorrang: Vorheizen beendet", "h_out_normal")
        warm = room is not None and room >= cap
        if tr.check("h_room_warm", warm, f"Raum {room:.1f} °C, Puffer bis {cap:.1f} °C" if room is not None else "keine Raumtemperatur"):
            return out(HeatMode.NORMAL, normal, f"Puffer voll ({room:.1f} °C) – Wärme ist gespeichert", "h_out_normal")
        stop = state.track_stop(deficit_w, cfg.stop_deficit_w, now, cfg.stop_delay_min, cfg.min_boost_min)
        tr.check("h_deficit", stop, f"Defizit {deficit_w:.0f} W seit {minutes_since(state.stop_since, now):.0f} von "
                 f"{cfg.stop_delay_min:.0f} min (Vorheizen mind. {cfg.min_boost_min:.0f} min)")
        if not stop:
            target = state.target if state.target is not None else _r((room or normal) + cfg.start_offset_k)
            step_note = "Ziel gehalten"
            if state.adjusted_at is None or held(state.adjusted_at, now, cfg.adjust_every_min):
                if deficit_w >= cfg.lower_deficit_w:
                    target, step_note = _r(target - cfg.step_k), f"−{cfg.step_k:.1f} K (Bezug {deficit_w:.0f} W)"
                elif export_w >= cfg.raise_export_w or (bat_ok and usable_w >= cfg.raise_export_w):
                    target, step_note = _r(target + cfg.step_k), f"+{cfg.step_k:.1f} K (noch {usable_w:.0f} W frei)"
                if step_note != "Ziel gehalten":
                    state.adjusted_at = now
            floor = max(lowered, _r((room or normal) - 0.2))
            target = min(cap, max(floor, target))
            comp = f", Verdichter {sit.compressor_w:.0f} W" if sit.compressor_w is not None else ""
            tr.check("h_adjust", True, f"{step_note}{comp}")
            return out(HeatMode.BOOST, target, f"Vorheizen mit PV: Ziel {target:.1f} °C ({step_note})", "h_out_boost", state)
        state = HeatState(HeatMode.NORMAL, now)        # surplus gone: fall through to setback/normal
    else:
        # ---- start? -------------------------------------------------------------------------
        ok_surplus = tr.check("h_surplus", usable_w >= cfg.start_surplus_w,
                              f"{usable_w:.0f} W frei für das Haus (Schwelle {cfg.start_surplus_w:.0f} W)")
        ok_battery = tr.check("h_battery", bat_ok or not cfg.battery_first, bat_why)
        ok_dhw = tr.check("h_dhw", not yield_dhw, "Bad wird vorbereitet – hat Vorrang" if yield_dhw
                          else "kein Vorrang für Warmwasser")
        ok_room = tr.check("h_room", room is None or room < cap - 0.2,
                           f"Raum {room:.1f} °C, Puffer bis {cap:.1f} °C" if room is not None else "keine Raumtemperatur")
        start_now = ok_surplus and ok_battery and ok_dhw and ok_room
        started = state.track_start(start_now, now, cfg.start_delay_min)
        tr.check("h_held", started if start_now else None,
                 f"erfüllt seit {minutes_since(state.start_since, now):.0f} von {cfg.start_delay_min:.0f} min" if start_now
                 else "nicht geprüft")
        if started:
            target = min(cap, _r((room if room is not None else normal) + cfg.start_offset_k))
            return out(HeatMode.BOOST, target, f"PV übrig ({usable_w:.0f} W): Vorheizen startet sanft mit {target:.1f} °C",
                       "h_out_boost", HeatState(HeatMode.BOOST, now, adjusted_at=now))
        if start_now:
            return _keep(out(HeatMode.NORMAL, normal, f"{usable_w:.0f} W frei – Vorheizen nach {cfg.start_delay_min:.0f} min",
                             "h_out_normal"), state)

    # ---- no PV heat now: let the stored heat carry the house until the next sun ----------------
    sun_left, tomorrow_wh = sit.sun_hours_left_today, sit.tomorrow_wh
    sun_later_today = sun_left is not None and sun_left >= 2
    good_from = cfg.poor_day_kwh * (TOMORROW_KEEP if prev.mode == HeatMode.SETBACK else 1.0)   # forecast wobbles
    sun_tomorrow = tomorrow_wh is not None and tomorrow_wh >= good_from * 1000
    if sun_left is None:
        tr.check("h_sun_coming", None, "keine Prognose – keine Absenkung")
        return _keep(out(HeatMode.NORMAL, normal, "Normale Raumtemperatur (keine Prognose)", "h_out_normal"), state)
    coming = not pv_now and (sun_later_today or (sun_left < 1 and sun_tomorrow))
    tr.check("h_sun_coming", coming,
             "PV liefert gerade" if pv_now else
             (f"heute noch {sun_left:.1f} Sonnenstunden" if sun_later_today else
              f"morgen {tomorrow_wh / 1000:.1f} kWh PV (gut ab {de(good_from)} kWh)"))
    if coming and lowered < normal:
        return _keep(out(HeatMode.SETBACK, lowered, f"Wärme aus dem Haus reicht bis zur Sonne: Raumziel {lowered:.1f} °C",
                         "h_out_setback"), state)
    return _keep(out(HeatMode.NORMAL, normal, "Normale Raumtemperatur", "h_out_normal"), state)


def _keep(result: tuple[Decision, HeatState], state: HeatState) -> tuple[Decision, HeatState]:
    """Carry the start timer over so a sustained surplus keeps counting."""
    decision, new_state = result
    new_state.start_since = state.start_since
    return decision, new_state


# ---- explanation: the steps above, in the order decide_heating() checks them ----------------
OUTCOMES = {"h_out_hold": "Batterie zuerst – normale Raumtemperatur", "h_out_boost": "Haus vorheizen",
            "h_out_setback": "Absenkung – Sonne kommt", "h_out_normal": "Normale Raumtemperatur"}
OFF_TEXT = "PV-Heizen ist ausgeschaltet: die normale Raumtemperatur gilt."
MODE_LABELS = {  # how the pages name each mode
    HeatMode.BOOST: "Haus vorheizen (PV)", HeatMode.HOLD: "Batterie zuerst", HeatMode.SETBACK: "Absenkung – Sonne kommt",
    HeatMode.NORMAL: "Normale Raumtemperatur", HeatMode.OFF: "Aus",
}


def stages(h: HeatingConfig) -> list[dict]:
    """The steps of decide_heating() with their questions, for the explanation tab."""
    return [
        stage("season", "h_season", "Ist Heizperiode?", "house",
              "PV-Heizen lohnt nur, wenn das Haus ohnehin Wärme braucht.",
              [("h_season", "Ist es draußen kalt genug?", f"Außentemperatur unter {de(h.outside_max, 0)} °C.")],
              gate_no="ja → weiter"),
        stage("h_battery_first", "h_battery_first", "Batterie zuerst?", "battery",
              "Tagsüber soll erst die Batterie voll werden. Schafft sie das heute nicht, wird nicht vorgeheizt: das Haus "
              f"bleibt bei der normalen Raumtemperatur ({de(h.normal_temp)} °C) und nimmt der Batterie keinen PV-Strom "
              "für zusätzliche Wärme weg.",
              [("h_battery_first", "Läuft PV und wird die Batterie heute nicht voll?",
                f"Voll ab {de(h.soc_full, 0)} %; die Prognose muss den Bedarf {de(h.battery_fill_safety, 1)}-fach decken.")],
              gate="h_battery_first", gate_no="nein → weiter"),
        stage("h_boosting", "h_boosting", "Wird gerade vorgeheizt?", "pv",
              "Ein laufendes Vorheizen endet erst, wenn es nicht mehr sinnvoll ist.", [
                  ("h_dhw", "Wird ein Bad vorbereitet?", "Das Bad braucht die Wärmepumpe für Warmwasser."),
                  ("h_room_warm", "Ist der Puffer voll?", f"Höchstens {de(h.room_max)} °C – je nach Sonne auch früher."),
                  ("h_deficit", "Kommt zu lange Strom aus Netz/Batterie?",
                   f"Über {de(h.stop_deficit_w, 0)} W für {de(h.stop_delay_min, 0)} min "
                   f"(nach mind. {de(h.min_boost_min, 0)} min)."),
                  ("h_adjust", "Feinregelung",
                   f"Alle {de(h.adjust_every_min, 0)} min ±{de(h.step_k, 1)} K: höher bei über {de(h.raise_export_w, 0)} W "
                   f"Einspeisung, tiefer bei über {de(h.lower_deficit_w, 0)} W Bezug. Die Inverter-WP moduliert ihre "
                   "Leistung mit dem Abstand Ziel–Raum."),
              ], gate="h_boosting", gate_no="nein → weiter"),
        stage("h_start", "h_surplus", "Vorheizen starten?", "heatpump",
              f"„Raumtemp. Tag“ (HK1, Fußbodenheizung) wird sanft angehoben: zuerst nur {de(h.start_offset_k, 1)} K über "
              "die Raumtemperatur, dann in kleinen Schritten dem Überschuss nach – höchstens bis zum Pufferziel (je mehr "
              f"Sonne nach der Batterie übrig ist, desto höher, bis {de(h.room_max)} °C). Estrich und Bodenplatte "
              "speichern die Wärme.", [
                  ("h_surplus", "Genug Überschuss?",
                   f"Mindestens {de(h.start_surplus_w, 0)} W gehen ins Netz oder in die Batterie."),
                  ("h_battery", "Wird die Batterie heute voll?",
                   f"Ab {de(h.soc_full, 0)} % oder sicher laut Prognose – sonst zählt nur Einspeisung."),
                  ("h_dhw", "Kein Bad in Vorbereitung?", "Nur ein Bad hat Vorrang."),
                  ("h_room", "Ist im Puffer noch Platz?",
                   f"Raum mindestens 0,3 K unter dem Pufferziel (höchstens {de(h.room_max)} °C)."),
                  ("h_held", "Hält das schon eine Weile an?", f"{de(h.start_delay_min, 0)} min am Stück."),
              ], all_needed=True),
        stage("h_setback", "h_sun_coming", "Ohne PV: reicht die gespeicherte Wärme?", "battery",
              f"Kommt bald Sonne, darf es abends und nachts bis {de(h.room_min)} °C abkühlen – die Wärmepumpe heizt "
              "dann nicht aus Batterie oder Netz.",
              [("h_sun_coming", "Kommt Sonne (später heute oder morgen)?",
                f"Morgen gut ab {de(h.poor_day_kwh, 0)} kWh Prognose.")],
              fallback=f"Sonst: normale Raumtemperatur {de(h.normal_temp)} °C."),
        stage("h_dwell", "h_dwell", "Nicht zu schnell wechseln", "house",
              "Wolken, eine schwankende Prognose oder 15 °C draußen sollen das Ziel nicht ständig zwischen "
              f"{de(h.room_min)} und {de(h.normal_temp)} °C hin- und herschalten.",
              [("h_dwell", "Läuft die jetzige Betriebsart lange genug?",
                f"Normal, Absenkung und Batterie zuerst wechseln frühestens nach {de(h.mode_min_min, 0)} min; "
                "Vorheizen und Bad warten nie.")]),
    ]
