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

from .i18n import fnum, t
from .rules import Decision, RunState, Trace, held, minutes_since, stage
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
    enabled: bool = flag(True)
    # False = test mode: decide and log, never write
    apply: bool = flag(False)
    # the room target you normally want (Raumtemp. Tag HK1)
    normal_temp: float = num(22.0, '°C', 10, 30, 0.5)
    # comfort floor: battery first, cold evenings, nights
    room_min: float = num(20.0, '°C', 10, 30, 0.5)
    # highest buffer temperature with PV
    room_max: float = num(24.0, '°C', 10, 30, 0.5)
    # heat the house stores per K (estimate, docs/strategy.md)
    house_kwh_per_k: float = num(20.0, 'kWh/K', 2, 100, 1)
    # where the real room temperature comes from (the LWZ has no room sensor of its own)
    room_sensor_max_age_min: float = num(180, 'min', 15, 1440, 15)
    room_from_isg: bool = flag(False)
    # PV forecast below this = no sun worth waiting for
    poor_day_kwh: float = num(8.0, 'kWh', 0, 100, 0.5)
    # heating season: outside colder than this
    outside_max: float = num(15.0, '°C', 0, 25, 0.5)
    # against flip-flopping between normal, setback and battery first
    mode_min_min: float = num(45.0, 'min', 0, 240, 5)
    # PV fills the battery before the house is preheated
    battery_first: bool = flag(True)
    # battery counts as full from here
    soc_full: float = num(95.0, '%', 50, 100, 1)
    battery_fill_safety: float = num(1.2, '', 1, 3, 0.05)
    # export/charging beyond the battery's need to start preheating
    start_surplus_w: float = num(800.0, 'W', 200, 8000, 50)
    start_delay_min: float = num(10.0, 'min', 0, 60, 1)
    # first target = room + this (slow compressor start)
    start_offset_k: float = num(0.3, 'K', 0.1, 2, 0.1)
    # fine adjustment per step
    step_k: float = num(0.2, 'K', 0.1, 1, 0.1)
    # at most one adjustment per this many minutes
    adjust_every_min: float = num(10.0, 'min', 5, 60, 1)
    # still exporting this much -> step up
    raise_export_w: float = num(300.0, 'W', 0, 5000, 50)
    # drawing this much from grid/battery -> step down
    lower_deficit_w: float = num(200.0, 'W', 0, 5000, 50)
    stop_deficit_w: float = num(300.0, 'W', 0, 5000, 50)
    stop_delay_min: float = num(15.0, 'min', 0, 60, 1)
    min_boost_min: float = num(30.0, 'min', 0, 240, 5)
    # True: also yield to PV hot water heating
    dhw_first: bool = flag(False)
    min_write_interval_min: float = num(10.0, 'min', 5, 120, 1)
    max_writes_per_day: int = num(30, '', 1, 48, 1)

    def validate(self) -> None:
        if not 10 <= self.room_min <= self.normal_temp <= self.room_max <= 30:
            raise ValueError(t("heat.raumtemperaturen_10_minimum_normal"))
        if self.house_kwh_per_k <= 0:
            raise ValueError(t("heat.speicherfaehigkeit_des_hauses_muss"))
        if not 0.1 <= self.step_k <= 1:
            raise ValueError(t("heat.schrittweite_0_1_bis_1_k"))


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
        return cfg.normal_temp, t("heat.kein_puffer_eingestellt")
    sit = inp.sit
    full_wh = span * cfg.house_kwh_per_k * 1000 / max(1.0, inp.cop)       # electricity for the whole span
    dull_tomorrow = sit.tomorrow_wh is not None and sit.tomorrow_wh < cfg.poor_day_kwh * 1000
    if dull_tomorrow:
        share, why = 1.0, t("heat.morgen_trueb_voller_puffer")
    elif sit.free_pv_today_wh is None:
        share, why = 0.5, t("heat.keine_prognose_halber_puffer")
    else:
        free = max(0.0, sit.free_pv_today_wh - sit.battery_need_wh)       # after the battery
        share = min(1.0, free / full_wh)
        why = t("heat.nach_der_batterie_noch_kwh_pv", free_k=fnum(free / 1000, 1), full_kwh=fnum(full_wh / 1000, 1))
    return min(cfg.room_max, _r(cfg.normal_temp + max(0.3, span * share))), why


def battery_ok(inp: HeatingInputs, cfg: HeatingConfig) -> tuple[bool, str]:
    """Will the battery be full today even if the house takes PV now?"""
    sit = inp.sit
    free, need = sit.free_pv_today_wh, sit.battery_need_wh
    if sit.soc >= cfg.soc_full:
        return True, t("heat.batterie_voll", soc=fnum(sit.soc, 0))
    if sit.battery_fills(cfg.soc_full, cfg.battery_fill_safety):
        return True, (t("heat.prognose_kwh_pv_frei_batterie_braucht", free_k=fnum(free / 1000, 1), need_k=fnum(need / 1000, 1)))
    return False, (t("heat.batterie_wird_heute_nicht_voll", soc=fnum(sit.soc, 0)) +
                   (t("heat.frei_kwh_braucht_kwh", free_k=fnum(free / 1000, 1), need_k=fnum(need / 1000, 1))
                    if free is not None else t("heat.keine_prognose")))


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
            tr.check("h_dwell", False, t("heat.erst_seit_von_min_wechsel_zu", mode=mode_label(prev.mode), age=fnum(age, 0), mode_min_min=fnum(cfg.mode_min_min, 0), new_mode=mode_label(decision.mode)))
            kept = HeatState(prev.mode, prev.mode_since, state.start_since, state.stop_since, prev.target,
                             prev.adjusted_at)
            reason = (t("heat.bleibt_noch_min_dann", mode=mode_label(prev.mode), mode_min_min=fnum(cfg.mode_min_min - age, 0), reason=decision.reasons[0]))
            return Decision(prev.mode, prev.target, [reason], decision.metrics, tr), kept
    tr.check("h_dwell", True, t("heat.kein_wechsel") if decision.mode == prev.mode else t("heat.wechsel_erlaubt"))
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
        tr.check(node, True, t("heat.ziel_c", target=fnum(target, 1)))
        st = state or HeatState(mode, prev.mode_since if prev.mode == mode else now)
        st.target = target
        return Decision(mode, target, [reason], metrics, tr), st

    if not tr.check("h_enabled", cfg.enabled, t("heat.pv_heizen_eingeschaltet") if cfg.enabled else t("heat.pv_heizen_ausgeschaltet")):
        return out(HeatMode.OFF, normal, t("heat.pv_heizen_aus_normale_raumtemperatur"), "h_out_normal")
    in_season = prev.mode in SEASON_MODES                     # already heating-season behaviour: 1 K margin
    season_max = cfg.outside_max + (SEASON_MARGIN_K if in_season else 0.0)
    season = sit.outside_temp is not None and sit.outside_temp < season_max
    tr.check("h_season", season, t("heat.keine_aussentemperatur") if sit.outside_temp is None else
             t("heat.aussen_c_heizperiode_unter_c", outside_temp=fnum(sit.outside_temp, 1), season_max=fnum(season_max))
             + (t("heat.1_k_weil_schon_heizbetrieb") if in_season else ")"))
    if not season:
        return out(HeatMode.NORMAL, normal, t("heat.keine_heizperiode_normale_raumtemperatur"), "h_out_normal")

    yield_dhw = inp.dhw_mode == "bath" or (cfg.dhw_first and inp.dhw_mode in ("boost", "reheat"))
    room = sit.room_temp
    pv_now = sit.pv_w >= (PV_ON_W if prev.mode != HeatMode.HOLD else PV_OFF_W)   # clouds: no on/off per minute
    state = HeatState(prev.mode if prev.mode != HeatMode.OFF else HeatMode.NORMAL,
                      prev.mode_since or now, prev.start_since, prev.stop_since, prev.target, prev.adjusted_at)

    # ---- battery first: while PV produces and the battery won't fill, the house waits --------
    hold = cfg.battery_first and pv_now and not bat_ok
    tr.check("h_battery_first", hold if cfg.battery_first else None,
             (t("heat.pv_laeuft_aber") + bat_why) if hold else (bat_why if pv_now else t("heat.kein_pv_gerade")))
    if hold:
        reason = t("heat.batterie_zuerst_kein_vorheizen_haus", bat_why=bat_why, normal=fnum(normal, 1))
        return out(HeatMode.HOLD, normal, reason, "h_out_hold")

    # ---- preheating with PV: follow the surplus in small steps --------------------------------
    if tr.check("h_boosting", state.mode == HeatMode.BOOST,
                t("heat.vorheizen_laeuft_seit_min_ziel_c", minutes=fnum(minutes_since(state.mode_since, now), 0), value=fnum(state.target or 0, 1))
                if state.mode == HeatMode.BOOST else t("heat.kein_vorheizen_aktiv")):
        if tr.check("h_dhw", yield_dhw, t("heat.bad_wird_vorbereitet_hat_vorrang") if yield_dhw else t("heat.kein_vorrang_fuer_warmwasser")):
            return out(HeatMode.NORMAL, normal, t("heat.bad_hat_vorrang_vorheizen_beendet"), "h_out_normal")
        warm = room is not None and room >= cap
        if tr.check("h_room_warm", warm, t("heat.raum_c_puffer_bis_c", room=fnum(room, 1), cap=fnum(cap, 1)) if room is not None else t("heat.keine_raumtemperatur")):
            return out(HeatMode.NORMAL, normal, t("heat.puffer_voll_c_waerme_ist_gespeichert", room=fnum(room, 1)), "h_out_normal")
        stop = state.track_stop(deficit_w, cfg.stop_deficit_w, now, cfg.stop_delay_min, cfg.min_boost_min)
        tr.check("h_deficit", stop, t("heat.defizit_w_seit_von_min_vorheizen", deficit_w=fnum(deficit_w, 0), minutes=fnum(minutes_since(state.stop_since, now), 0), stop_delay_min=fnum(cfg.stop_delay_min, 0), min_boost_min=fnum(cfg.min_boost_min, 0)))
        if not stop:
            target = state.target if state.target is not None else _r((room or normal) + cfg.start_offset_k)
            step_note, moved = t("heat.ziel_gehalten"), False
            if state.adjusted_at is None or held(state.adjusted_at, now, cfg.adjust_every_min):
                if deficit_w >= cfg.lower_deficit_w:
                    target, step_note, moved = _r(target - cfg.step_k), t("heat.k_bezug_w", step_k=fnum(cfg.step_k, 1), deficit_w=fnum(deficit_w, 0)), True
                elif export_w >= cfg.raise_export_w or (bat_ok and usable_w >= cfg.raise_export_w):
                    target, step_note, moved = _r(target + cfg.step_k), t("heat.k_noch_w_frei", step_k=fnum(cfg.step_k, 1), usable_w=fnum(usable_w, 0)), True
                if moved:
                    state.adjusted_at = now
            floor = max(lowered, _r((room or normal) - 0.2))
            target = min(cap, max(floor, target))
            tr.check("h_adjust", True, step_note if sit.compressor_w is None else
                     t("heat.step_with_compressor", step_note=step_note, compressor_w=fnum(sit.compressor_w, 0)))
            return out(HeatMode.BOOST, target, t("heat.vorheizen_mit_pv_ziel_c", target=fnum(target, 1), step_note=step_note), "h_out_boost", state)
        state = HeatState(HeatMode.NORMAL, now)        # surplus gone: fall through to setback/normal
    else:
        # ---- start? -------------------------------------------------------------------------
        ok_surplus = tr.check("h_surplus", usable_w >= cfg.start_surplus_w,
                              t("heat.w_frei_fuer_das_haus_schwelle", usable_w=fnum(usable_w, 0), start_surplus_w=fnum(cfg.start_surplus_w, 0)))
        ok_battery = tr.check("h_battery", bat_ok or not cfg.battery_first, bat_why)
        ok_dhw = tr.check("h_dhw", not yield_dhw, t("heat.bad_wird_vorbereitet_hat_vorrang") if yield_dhw
                          else t("heat.kein_vorrang_fuer_warmwasser"))
        ok_room = tr.check("h_room", room is None or room < cap - 0.2,
                           t("heat.raum_c_puffer_bis_c", room=fnum(room, 1), cap=fnum(cap, 1)) if room is not None else t("heat.keine_raumtemperatur"))
        start_now = ok_surplus and ok_battery and ok_dhw and ok_room
        started = state.track_start(start_now, now, cfg.start_delay_min)
        tr.check("h_held", started if start_now else None,
                 t("heat.erfuellt_seit_von_min", minutes=fnum(minutes_since(state.start_since, now), 0), start_delay_min=fnum(cfg.start_delay_min, 0)) if start_now
                 else t("heat.nicht_geprueft"))
        if started:
            target = min(cap, _r((room if room is not None else normal) + cfg.start_offset_k))
            return out(HeatMode.BOOST, target, t("heat.pv_uebrig_w_vorheizen_startet_sanft", usable_w=fnum(usable_w, 0), target=fnum(target, 1)),
                       "h_out_boost", HeatState(HeatMode.BOOST, now, adjusted_at=now))
        if start_now:
            return _keep(out(HeatMode.NORMAL, normal, t("heat.w_frei_vorheizen_nach_min", usable_w=fnum(usable_w, 0), start_delay_min=fnum(cfg.start_delay_min, 0)),
                             "h_out_normal"), state)

    # ---- no PV heat now: let the stored heat carry the house until the next sun ----------------
    sun_left, tomorrow_wh = sit.sun_hours_left_today, sit.tomorrow_wh
    sun_later_today = sun_left is not None and sun_left >= 2
    good_from = cfg.poor_day_kwh * (TOMORROW_KEEP if prev.mode == HeatMode.SETBACK else 1.0)   # forecast wobbles
    sun_tomorrow = tomorrow_wh is not None and tomorrow_wh >= good_from * 1000
    if sun_left is None:
        tr.check("h_sun_coming", None, t("heat.keine_prognose_keine_absenkung"))
        return _keep(out(HeatMode.NORMAL, normal, t("heat.normale_raumtemperatur_keine_prognose"), "h_out_normal"), state)
    coming = not pv_now and (sun_later_today or (sun_left < 1 and sun_tomorrow))
    tr.check("h_sun_coming", coming,
             t("heat.pv_liefert_gerade") if pv_now else
             (t("heat.heute_noch_sonnenstunden", sun_left=fnum(sun_left, 1)) if sun_later_today else
              t("heat.morgen_kwh_pv_gut_ab_kwh", tomorrow_kwh=fnum(tomorrow_wh / 1000, 1), good_from=fnum(good_from))))
    if coming and lowered < normal:
        return _keep(out(HeatMode.SETBACK, lowered, t("heat.waerme_aus_dem_haus_reicht_bis", lowered=fnum(lowered, 1)),
                         "h_out_setback"), state)
    return _keep(out(HeatMode.NORMAL, normal, t("heat.normale_raumtemperatur"), "h_out_normal"), state)


def _keep(result: tuple[Decision, HeatState], state: HeatState) -> tuple[Decision, HeatState]:
    """Carry the start timer over so a sustained surplus keeps counting."""
    decision, new_state = result
    new_state.start_since = state.start_since
    return decision, new_state


# ---- explanation: the steps above, in the order decide_heating() checks them ----------------
OUTCOMES = ('h_out_hold', 'h_out_boost', 'h_out_setback', 'h_out_normal')   # trace nodes that end a decision: texts "heat.outcome.<node>"


def outcome_texts() -> dict[str, str]:
    return {node: t(f"heat.outcome.{node}") for node in OUTCOMES}


def off_text() -> str:
    return t("heat.off")


def mode_label(mode: HeatMode) -> str:
    """How the pages name each mode (text "heat.mode.<mode>")."""
    return t(f"heat.mode.{mode.value}")


def stages(h: HeatingConfig) -> list[dict]:
    """The steps of decide_heating() with their questions, for the explanation tab."""
    return [
        stage("season", "h_season", t("heat.ist_heizperiode"), "house",
              t("heat.pv_heizen_lohnt_nur_wenn_das"),
              [("h_season", t("heat.ist_es_draussen_kalt_genug"), t("heat.aussentemperatur_unter_c", outside_max=fnum(h.outside_max, 0)))],
              gate_no=t("heat.ja_weiter")),
        stage("h_battery_first", "h_battery_first", t("heat.batterie_zuerst"), "battery",
              t("heat.tagsueber_soll_erst_die_batterie_voll", normal_temp=fnum(h.normal_temp)),
              [("h_battery_first", t("heat.laeuft_pv_und_wird_die_batterie"),
                t("heat.voll_ab_die_prognose_muss_den", soc_full=fnum(h.soc_full, 0), battery_fill_safety=fnum(h.battery_fill_safety, 1)))],
              gate="h_battery_first", gate_no=t("heat.nein_weiter")),
        stage("h_boosting", "h_boosting", t("heat.wird_gerade_vorgeheizt"), "pv",
              t("heat.ein_laufendes_vorheizen_endet_erst_wenn"), [
                  ("h_dhw", t("heat.wird_ein_bad_vorbereitet"), t("heat.das_bad_braucht_die_waermepumpe_fuer")),
                  ("h_room_warm", t("heat.ist_der_puffer_voll"), t("heat.hoechstens_c_je_nach_sonne_auch", room_max=fnum(h.room_max))),
                  ("h_deficit", t("heat.kommt_zu_lange_strom_aus_netz"),
                   t("heat.ueber_w_fuer_min_nach_mind", stop_deficit_w=fnum(h.stop_deficit_w, 0), stop_delay_min=fnum(h.stop_delay_min, 0), min_boost_min=fnum(h.min_boost_min, 0))),
                  ("h_adjust", t("heat.feinregelung"),
                   t("heat.alle_min_k_hoeher_bei_ueber", adjust_every_min=fnum(h.adjust_every_min, 0), step_k=fnum(h.step_k, 1), raise_export_w=fnum(h.raise_export_w, 0), lower_deficit_w=fnum(h.lower_deficit_w, 0))),
              ], gate="h_boosting", gate_no=t("heat.nein_weiter")),
        stage("h_start", "h_surplus", t("heat.vorheizen_starten"), "heatpump",
              t("heat.raumtemp_tag_hk1_fussbodenheizung_wird", start_offset_k=fnum(h.start_offset_k, 1), room_max=fnum(h.room_max)), [
                  ("h_surplus", t("heat.genug_ueberschuss"),
                   t("heat.mindestens_w_gehen_ins_netz_oder", start_surplus_w=fnum(h.start_surplus_w, 0))),
                  ("h_battery", t("heat.wird_die_batterie_heute_voll"),
                   t("heat.ab_oder_sicher_laut_prognose_sonst", soc_full=fnum(h.soc_full, 0))),
                  ("h_dhw", t("heat.kein_bad_in_vorbereitung"), t("heat.nur_ein_bad_hat_vorrang")),
                  ("h_room", t("heat.ist_im_puffer_noch_platz"),
                   t("heat.raum_mindestens_0_3_k_unter", room_max=fnum(h.room_max))),
                  ("h_held", t("heat.haelt_das_schon_eine_weile_an"), t("heat.min_am_stueck", start_delay_min=fnum(h.start_delay_min, 0))),
              ], all_needed=True),
        stage("h_setback", "h_sun_coming", t("heat.ohne_pv_reicht_die_gespeicherte_waerme"), "battery",
              t("heat.kommt_bald_sonne_darf_es_abends", room_min=fnum(h.room_min)),
              [("h_sun_coming", t("heat.kommt_sonne_spaeter_heute_oder_morgen"),
                t("heat.morgen_gut_ab_kwh_prognose", poor_day_kwh=fnum(h.poor_day_kwh, 0)))],
              fallback=t("heat.sonst_normale_raumtemperatur_c", normal_temp=fnum(h.normal_temp))),
        stage("h_dwell", "h_dwell", t("heat.nicht_zu_schnell_wechseln"), "house",
              t("heat.wolken_eine_schwankende_prognose_oder_15", room_min=fnum(h.room_min), normal_temp=fnum(h.normal_temp)),
              [("h_dwell", t("heat.laeuft_die_jetzige_betriebsart_lange"),
                t("heat.normal_absenkung_und_batterie_zuerst", mode_min_min=fnum(h.mode_min_min, 0)))]),
    ]
