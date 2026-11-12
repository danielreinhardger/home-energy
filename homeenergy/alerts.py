"""Alerts: notice when something stops working, show it on the page, optionally push it.

`detect()` is pure: it turns the current state into the problems that exist right now.
`Alerts` adds time: a problem becomes an alert only after it lasted its delay (a single
missed read is no alert), and it sends one push when an alert starts and one when it is
over. The push goes to an ntfy topic (https://ntfy.sh/<topic> or an own ntfy server) or
any URL that takes a plain-text POST; without an address alerts only show on the page.
"""
from __future__ import annotations

import logging
import threading
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable


from .controller import FAILED
from .i18n import fnum, t
from .settings import num, text

log = logging.getLogger(__name__)
TITLE = "Smarthome 28"


@dataclass
class AlertConfig:
    push_url: str = text('')
    device_minutes: float = num(10, 'min', 2, 240, 1)
    dhw_low_temp: float = num(36, '°C', 25, 50, 0.5)
    backup_kwh: float = num(1, 'kWh', 0, 50, 1)


@dataclass(frozen=True)
class Problem:
    key: str
    text: str
    delay_min: float              # how long it must last before it is an alert


def detect(cfg: AlertConfig, devices: dict[str, tuple[str, bool, str | None]], dhw_temp: float | None,
           last_actions: dict[str, str], forecast_error: str | None, forecast_age_h: float | None) -> list[Problem]:
    """The problems right now. devices: key -> (name, fresh, error); last_actions: controller -> (outcome code, action text)."""
    out = []
    for key, (name, fresh, error) in devices.items():
        if not fresh or error:
            out.append(Problem(f"device:{key}", t("alert.keine_daten", name=name, value=error or t("alert.keine_antwort")),
                               cfg.device_minutes))
    for kind, (outcome, action) in last_actions.items():
        if outcome == FAILED:
            name = t(f"alert.write_target.{kind}")
            out.append(Problem(f"write:{kind}", t("alert.konnte_nicht_an_die_waermepumpe_gesendet", name=name, action=action), 0))
    if dhw_temp is not None and dhw_temp < cfg.dhw_low_temp:
        out.append(Problem("dhw:cold", t("alert.warmwasser_nur_c_unter_c", dhw_temp=fnum(dhw_temp), dhw_low_temp=fnum(cfg.dhw_low_temp)), 30))
    if forecast_error and (forecast_age_h is None or forecast_age_h > 3):
        out.append(Problem("forecast", t("alert.wetterprognose_nicht_abrufbar", forecast_error=forecast_error), 0))
    return out


def send_push(url: str, message: str, title: str = TITLE, tags: str = "", priority: str = "default",
              opener: Callable = urllib.request.urlopen) -> None:
    """POST the message as plain text (ntfy reads Title/Tags/Priority from the headers)."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(t("alert.push_adresse_muss_mit_http_oder"))
    headers = {"Title": title, "Priority": priority, "User-Agent": "home-energy"}
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
    with opener(req, timeout=10) as resp:
        resp.read()


class BackupWatch:
    """Today's energy of the electric backup heater (Heizstab, "NE" on the ISG).

    The ISG only has lifetime counters for its heat (WM NE Heizen / WW, whole kWh); today's
    share is the growth since the first reading of the day (kept in state.json, so a restart
    keeps the day). Its heat is its electricity, 1:1. `update()` returns a message once per day,
    when today's total first reaches the threshold."""

    def __init__(self, state: dict | None = None):
        s = state or {}
        self.day: str | None = s.get("day")
        self.start: dict[str, float] = dict(s.get("start") or {})
        self.today: dict[str, float] = dict(s.get("today") or {"heating": 0.0, "dhw": 0.0})
        self.notified: bool = bool(s.get("notified"))

    def to_json(self) -> dict:
        return {"day": self.day, "start": self.start, "today": self.today, "notified": self.notified}

    @property
    def total(self) -> float:
        return sum(self.today.values())

    def update(self, now: datetime, heating_total: float | None, dhw_total: float | None,
               threshold_kwh: float) -> str | None:
        if heating_total is None or dhw_total is None:
            return None
        totals = {"heating": float(heating_total), "dhw": float(dhw_total)}
        day = now.date().isoformat()
        if day != self.day or set(self.start) != set(totals):
            self.day, self.start, self.notified = day, totals, False
        # a counter that went backwards (replaced controller, reset): start the day over from here
        if any(totals[k] < self.start[k] for k in totals):
            self.start = totals
        self.today = {k: round(totals[k] - self.start[k], 1) for k in totals}
        if threshold_kwh > 0 and not self.notified and self.total >= threshold_kwh:
            self.notified = True
            return (t("alert.heizstab_lief_heute_kwh_heizen", total=fnum(self.total, 0), heating=fnum(self.today['heating'], 0), dhw=fnum(self.today['dhw'], 0)))
        return None


class Alerts:
    """Turns problems into alerts (after their delay), keeps a short history and pushes changes."""

    def __init__(self, clock: Callable[[], datetime], push: Callable[..., None] = send_push, background: bool = True):
        self._clock = clock
        self._push = push
        self._background = background
        self.first_seen: dict[str, datetime] = {}         # problem key -> since when it exists
        self.active: dict[str, dict] = {}                 # alert key -> {"text", "since"}
        self.history: deque[dict] = deque(maxlen=30)      # newest first: started / over
        self.push_error: str | None = None

    def update(self, problems: list[Problem], cfg: AlertConfig) -> None:
        now = self._clock()
        current = {p.key: p for p in problems}
        for key in list(self.first_seen):
            if key not in current:
                del self.first_seen[key]
        for key, p in current.items():
            since = self.first_seen.setdefault(key, now)
            if key in self.active:
                self.active[key]["text"] = p.text             # keep the latest detail
            elif now - since >= timedelta(minutes=p.delay_min):
                self.active[key] = {"key": key, "text": p.text, "since": since.isoformat(timespec="minutes")}
                self._event("start", p.text, now)
                self._notify(cfg, f"⚠ {p.text}", "warning", "high")
        for key in [k for k in self.active if k not in current]:
            text_ = self.active.pop(key)["text"]
            self._event("over", text_, now)
            self._notify(cfg, t("alert.behoben", text=text_), "white_check_mark", "default")

    def notice(self, cfg: AlertConfig, text_: str, tags: str = "zap") -> None:
        """A one-off note (no start/end), e.g. the backup heater ran: history, error log and push."""
        now = self._clock()
        self.history.appendleft({"kind": "notice", "text": text_, "time": now.isoformat(timespec="minutes")})
        log.warning(t("alert.log_notice", text=text_))
        self._notify(cfg, text_, tags, "default")

    def _event(self, kind: str, text_: str, now: datetime) -> None:
        self.history.appendleft({"kind": kind, "text": text_, "time": now.isoformat(timespec="minutes")})
        if kind == "start":
            log.warning(t("alert.log_notice", text=text_))
        else:
            log.info(t("alert.behoben_s"), text_, extra={"event": True})

    def _notify(self, cfg: AlertConfig, message: str, tags: str, priority: str) -> None:
        if not cfg.push_url:
            return

        def run():
            try:
                self._push(cfg.push_url, message, tags=tags, priority=priority)
                self.push_error = None
            except Exception as exc:                      # a failed push must not stop anything
                self.push_error = str(exc)
                log.warning("push: %s", exc)

        if self._background:
            threading.Thread(target=run, name="push", daemon=True).start()
        else:
            run()

    def to_json(self, cfg: AlertConfig) -> dict:
        return {"active": list(self.active.values()), "history": list(self.history),
                "push": bool(cfg.push_url), "push_error": self.push_error}
