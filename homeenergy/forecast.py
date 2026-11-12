"""PV production forecast from Open-Meteo tilted irradiance.

Open-Meteo is free, needs no API key and returns hourly `global_tilted_irradiance`
(W/m² on the panel plane) per array orientation. Energy per hour is

    E_hour [Wh] = kWp * GTI [W/m²] / 1000 [W/m²] * 1 h * 1000 * performance_ratio

The raw model is then scaled by a calibration factor learned from the inverter's
actual daily yield, which absorbs shading, soiling and a wrong kWp guess.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from .i18n import t
from .settings import flag, hidden, num

API = "https://api.open-meteo.com/v1/forecast"
DAY_PARTS = (("forenoon", 9), ("midday", 12), ("afternoon", 15))   # local hour starts (tomorrow): "fc.part.<code>"
# Phases of the day for "now" and "next" on today's card: (start hour, code, length in hours): "fc.phase.<code>"
PHASES = ((0, "night", 6), (6, "morning", 4), (10, "midday", 4), (14, "afternoon", 4), (18, "evening", 4),
          (22, "night", 8))


def phase_at(dt: datetime) -> tuple[str, datetime, int]:
    """Code, start and length of the phase of day that contains `dt` (local time)."""
    for start_h, name, length in reversed(PHASES):
        if dt.hour >= start_h:
            return name, dt.replace(hour=start_h, minute=0, second=0, microsecond=0), length
    raise AssertionError("unreachable")


def next_phase(dt: datetime) -> tuple[str, datetime, int]:
    """The next phase with a different name (night runs 22:00 to 06:00 as one phase)."""
    name, start, length = phase_at(dt)
    probe = start + timedelta(hours=length)
    for _ in range(8):
        n, s, ln = phase_at(probe)
        if n != name:
            return n, s, ln
        probe = s + timedelta(hours=ln)
    raise AssertionError("unreachable")


@dataclass
class PvArray:
    kwp: float
    tilt: float = 30.0          # degrees from horizontal
    azimuth: float = 0.0        # Open-Meteo convention: 0 = south, -90 = east, 90 = west


@dataclass
class ForecastConfig:
    enabled: bool = flag(False)
    latitude: float | None = num(None, '°', -90, 90, 0.0001)
    longitude: float | None = num(None, '°', -180, 180, 0.0001)
    performance_ratio: float = num(0.85, '', 0.5, 1, 0.01)
    refresh_minutes: int = num(60, 'min', 15, 360, 5)
    arrays: list[PvArray] = hidden(default_factory=lambda: [PvArray(kwp=11.0)], why='edited in its own table')

    @property
    def usable(self) -> bool:
        return self.enabled and self.latitude is not None and self.longitude is not None


@dataclass
class HourlyPv:
    """Expected PV energy for the hour starting at `start` (timezone-aware, UTC)."""
    start: datetime
    wh: float


@dataclass
class DailyWeather:
    day: date
    code: int | None            # WMO weather code
    tmax: float | None
    tmin: float | None
    sunshine_h: float | None
    precip_mm: float | None = None
    precip_prob: float | None = None   # % (max over the day)
    wind_max: float | None = None      # km/h


@dataclass
class Weather:
    """Weather symbols for the dashboard (not used by the PV model)."""
    hourly_codes: dict[datetime, int]   # hour start (UTC) -> WMO code
    daily: list[DailyWeather]
    hourly_is_day: dict[datetime, bool] = field(default_factory=dict)
    hourly_temp: dict[datetime, float] = field(default_factory=dict)


FORECAST_DAYS = 4          # today + the 3-day outlook


def fetch_weather(cfg: ForecastConfig, fetch=None) -> Weather:
    fetch = fetch or _http_get_json
    query = urllib.parse.urlencode({
        "latitude": cfg.latitude, "longitude": cfg.longitude,
        "hourly": "weather_code,is_day,temperature_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunshine_duration,"
                 "precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
        "forecast_days": FORECAST_DAYS, "timezone": "GMT",
    })
    data = fetch(f"{API}?{query}")
    hourly, is_day, temps = {}, {}, {}
    h = data["hourly"]
    n = len(h["time"])
    for t, code, day, temp in zip(h["time"], h["weather_code"], h.get("is_day") or [None] * n,
                                  h.get("temperature_2m") or [None] * n):
        # weather_code describes the hour ending at t, like the radiation values
        start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - timedelta(hours=1)
        if code is not None:
            hourly[start] = int(code)
        if day is not None:
            is_day[start] = bool(day)
        if temp is not None:
            temps[start] = float(temp)
    d = data["daily"]
    nd = len(d["time"])
    daily = [DailyWeather(date.fromisoformat(day), None if c is None else int(c), tx, tn,
                          None if s is None else s / 3600, pr, pp, wm)
             for day, c, tx, tn, s, pr, pp, wm in zip(
                 d["time"], d["weather_code"], d["temperature_2m_max"], d["temperature_2m_min"],
                 d["sunshine_duration"], d.get("precipitation_sum") or [None] * nd,
                 d.get("precipitation_probability_max") or [None] * nd, d.get("wind_speed_10m_max") or [None] * nd)]
    return Weather(hourly, daily, is_day, temps)


OUTLOOK_HOURS = (6, 9, 12, 15, 18, 21)       # local hour starts shown per day


def outlook(hours: list[HourlyPv], weather: Weather | None, now: datetime, calibration: float = 1.0,
            days: int = 3) -> list[dict]:
    """The next `days` days (from tomorrow): weather, temperatures, rain, wind and the calibrated
    PV forecast per local day (None while a day isn't fully in the forecast yet)."""
    local = now.tzinfo
    per_day: dict[date, list[tuple[datetime, float]]] = {}
    for h in hours:
        start = h.start.astimezone(local)
        per_day.setdefault(start.date(), []).append((start, h.wh * calibration))
    utc = lambda dt: dt.astimezone(timezone.utc)
    out = []
    for offset in range(1, days + 1):
        day = now.date() + timedelta(days=offset)
        w = next((x for x in weather.daily if x.day == day), None) if weather else None
        pv = per_day.get(day, [])
        weekday = t(f"fc.weekday.{day.weekday()}")
        label = t("fc.tomorrow") if offset == 1 else t("fc.day_after_tomorrow") if offset == 2 else weekday
        parts = []
        for hour in OUTLOOK_HOURS:
            at = datetime.combine(day, datetime.min.time(), local).replace(hour=hour)
            parts.append({"hour": hour,
                          "code": weather.hourly_codes.get(utc(at)) if weather else None,
                          "night": (weather.hourly_is_day.get(utc(at)) is False) if weather else False,
                          "temp": weather.hourly_temp.get(utc(at)) if weather else None})
        out.append({
            "date": day.isoformat(), "label": label, "weekday": weekday,
            "code": w.code if w else None, "tmin": w.tmin if w else None, "tmax": w.tmax if w else None,
            "sunshine_h": None if not w or w.sunshine_h is None else round(w.sunshine_h, 1),
            "precip_mm": None if not w or w.precip_mm is None else round(w.precip_mm, 1),
            "precip_prob": w.precip_prob if w else None,
            "wind_max": None if not w or w.wind_max is None else round(w.wind_max),
            "pv_kwh": round(sum(wh for _, wh in pv) / 1000, 1) if len(pv) >= 23 else None,
            "pv_hours": [{"hour": s.hour, "kwh": round(wh / 1000, 2)} for s, wh in sorted(pv) if 5 <= s.hour <= 20],
            "parts": parts,
        })
    return out


@dataclass
class ForecastSummary:
    fetched_at: datetime
    calibration: float
    remaining_today_wh: float
    today_wh: float
    tomorrow_wh: float
    sun_hours_left_today: float          # hours with meaningful PV left today
    hours: list[HourlyPv]
    weather: Weather | None = None
    now: datetime | None = None

    def to_json(self) -> dict:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "calibration": round(self.calibration, 3),
            "remaining_today_kwh": round(self.remaining_today_wh / 1000, 2),
            "today_kwh": round(self.today_wh / 1000, 2),
            "tomorrow_kwh": round(self.tomorrow_wh / 1000, 2),
            "sun_hours_left_today": round(self.sun_hours_left_today, 1),
            "hours": [{"start": h.start.isoformat(), "kwh": round(h.wh / 1000, 3),
                       "code": self._code(h.start)} for h in self.hours],
            "days": self._days(),
        }

    def _code(self, start: datetime) -> int | None:
        if self.weather is None:
            return None
        return self.weather.hourly_codes.get(start.astimezone(timezone.utc))

    def _night(self, start: datetime) -> bool:
        if self.weather is None:
            return False
        return self.weather.hourly_is_day.get(start.astimezone(timezone.utc)) is False

    def _part(self, label: str, sub: str, at: datetime) -> dict:
        at = at.replace(minute=0, second=0, microsecond=0)
        return {"label": label, "sub": sub, "hour": at.hour, "code": self._code(at), "night": self._night(at)}

    def _days(self) -> list[dict]:
        now = self.now or self.fetched_at
        today = now.date()
        out = []
        for offset, label in ((0, t("fc.today")), (1, t("fc.tomorrow"))):
            day = today + timedelta(days=offset)
            w = next((x for x in self.weather.daily if x.day == day), None) if self.weather else None
            if offset == 0:
                # today: the phase we're in (weather of this hour) and the next phase (its middle hour)
                cur_name, _, _ = phase_at(now)
                nxt_name, nxt_start, nxt_len = next_phase(now)
                parts = [self._part(t("fc.now"), t(f"fc.phase.{cur_name}"), now),
                         self._part(t("fc.ab_uhr", hour=nxt_start.hour), t(f"fc.phase.{nxt_name}"),
                                    nxt_start + timedelta(hours=nxt_len // 2))]
            else:
                parts = [self._part(t(f"fc.part.{part}"), t("fc.uhr", hour=hour, hour2=hour + 1),
                                    now.replace(hour=hour) + timedelta(days=offset))
                         for part, hour in DAY_PARTS]
            out.append({"label": label, "date": day.isoformat(), "parts": parts,
                        "pv_kwh": round((self.today_wh if offset == 0 else self.tomorrow_wh) / 1000, 1),
                        "code": w.code if w else None,
                        "tmax": w.tmax if w else None, "tmin": w.tmin if w else None,
                        "sunshine_h": None if not w or w.sunshine_h is None else round(w.sunshine_h, 1)})
        return out


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "home-energy-dashboard/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def fetch_hourly(cfg: ForecastConfig, fetch=_http_get_json) -> list[HourlyPv]:
    """Fetch today+tomorrow hourly PV energy (uncalibrated), summed over all arrays."""
    if not cfg.usable:
        raise ValueError("forecast needs enabled=true, latitude and longitude")
    totals: dict[datetime, float] = {}
    for arr in cfg.arrays:
        query = urllib.parse.urlencode({
            "latitude": cfg.latitude, "longitude": cfg.longitude,
            "hourly": "global_tilted_irradiance", "tilt": arr.tilt, "azimuth": arr.azimuth,
            "forecast_days": FORECAST_DAYS, "timezone": "GMT",
        })
        data = fetch(f"{API}?{query}")
        times, gti = data["hourly"]["time"], data["hourly"]["global_tilted_irradiance"]
        for t, g in zip(times, gti):
            # Open-Meteo radiation is the mean over the hour *ending* at t.
            start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - timedelta(hours=1)
            totals[start] = totals.get(start, 0.0) + arr.kwp * (g or 0.0) * cfg.performance_ratio
    return [HourlyPv(s, wh) for s, wh in sorted(totals.items())]


def summarize(hours: list[HourlyPv], now: datetime, calibration: float = 1.0,
              fetched_at: datetime | None = None, min_hour_wh: float = 150.0,
              weather: Weather | None = None) -> ForecastSummary:
    """Summarize for the local day of `now` (an aware datetime in local time)."""
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    local = now.tzinfo
    cal = [HourlyPv(h.start.astimezone(local), h.wh * calibration) for h in hours]
    remaining = 0.0
    sun_hours = 0.0
    for h in cal:
        if h.start.date() != today:
            continue
        end = h.start + timedelta(hours=1)
        if end <= now:
            continue
        frac = 1.0 if h.start >= now else (end - now).total_seconds() / 3600
        remaining += h.wh * frac
        if h.wh >= min_hour_wh:
            sun_hours += frac
    return ForecastSummary(
        fetched_at=fetched_at or now,
        calibration=calibration,
        remaining_today_wh=remaining,
        today_wh=sum(h.wh for h in cal if h.start.date() == today),
        tomorrow_wh=sum(h.wh for h in cal if h.start.date() == tomorrow),
        sun_hours_left_today=sun_hours,
        hours=[h for h in cal if h.start.date() in (today, tomorrow)],
        weather=weather,
        now=now,
    )


@dataclass
class Calibration:
    """Real yield / day-ahead forecast, as the ratio of sums over the recent days.

    Sums weigh a sunny 20 kWh day more than a grey 2 kWh one (whose ratio is mostly noise),
    and the day-ahead forecast is what the rules lean on ("tomorrow"), so that is the one
    being calibrated. Until `min_days` usable days are logged the previous factor stays.
    """
    factor: float = 1.0
    days: int = 0                  # usable days behind the factor
    lo: float = 0.3
    hi: float = 1.6
    window: int = 21               # newest days with a measured yield
    min_days: int = 3
    min_wh: float = 1000.0         # days forecast below this are left out (ratio is noise)

    @classmethod
    def from_json(cls, d: dict) -> "Calibration":
        c = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if "last_day" in d:          # the older moving average: keep its factor, but no day counts as fitted
            c.days = 0
        return c

    def refit(self, log: dict[str, dict]) -> bool:
        """Recompute the factor from the forecast log (day → ahead_wh, same_wh, actual_wh)."""
        done = [d for _, d in sorted(log.items()) if d.get("actual_wh") is not None][-self.window:]
        usable = [d for d in done if (d.get("ahead_wh") or 0) >= self.min_wh]
        if len(usable) < self.min_days:
            return False
        ratio = sum(d["actual_wh"] for d in usable) / sum(d["ahead_wh"] for d in usable)
        self.factor, self.days = min(self.hi, max(self.lo, ratio)), len(usable)
        return True


def local_day_totals(hours: list[HourlyPv], tz) -> dict[date, tuple[float, int]]:
    """Forecast energy per local day, with the number of hours behind it (24 = complete)."""
    out: dict[date, tuple[float, int]] = {}
    for h in hours:
        day = h.start.astimezone(tz).date()
        wh, n = out.get(day, (0.0, 0))
        out[day] = (wh + h.wh, n + 1)
    return out
