"""HTTP server: static dashboard + small JSON API (stdlib only).

GET  /api/state                     live values, energy summary, controller, forecast (every 10 s:
                                    only what changes; labels and texts come from /api/meta)
GET  /api/meta                      what rarely changes: value labels/units, the rule texts; its
                                    `version` is in every state, so the page reloads it only on change
GET  /api/decisions[?limit=30]      both decision logs, newest first (PV-Regelung tab)
GET  /api/history[?since=<iso|unix>][&format=columns]   one sample per minute, last 48 h
                                    (or only newer than `since`); columns: one list per value
GET  /api/history?from=<iso>[&to=<iso>][&points=800]   any range, thinned (minute or 15-min data)
GET  /api/perf                      device read times, control time, API sizes, data files
GET  /api/settings                  device settings + dashboard config with field metadata
GET  /api/simulation                strategy vs. fixed targets on model days (live settings)
GET  /api/finance                   daily and monthly money ledger
GET  /api/log[?level=error,warning][&limit=300]   error log, newest first (90 days kept)
POST /api/settings/<device>/<key>   {"value": 46.0}  write one device setting
POST /api/config/<section>          {"field": value, ...}  update one config section (dhw, heating, alerts, …)
GET  /api/forecast/outlook          the next 3 days: weather, temperatures, rain, wind, PV per day
POST /api/forecast/refresh          fetch the forecast now
POST /api/bath                      {"time": "17:30", "temp": 55}  request hot water for a bath
POST /api/bath/cancel               cancel the bath request
POST /api/alerts/test               send a test message to the push address
"""
from __future__ import annotations

import gzip
import json
import logging
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import views
from .service import Service

log = logging.getLogger(__name__)
MAX_BODY = 64 * 1024
GZIP_MIN = 1024          # compress JSON larger than this when the browser accepts it (≈ 10× smaller)


def make_handler(service: Service, static_dir: Path):
    static_root = static_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "home-energy/0.1"

        def log_message(self, fmt, *args):  # route access logs through logging
            log.debug("%s %s", self.address_string(), fmt % args)

        # -- helpers ------------------------------------------------------
        def _json(self, data, status=HTTPStatus.OK):
            started = time.monotonic()
            raw = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
            body, zipped = raw, False
            if len(raw) >= GZIP_MIN and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                body, zipped = gzip.compress(raw, compresslevel=5), True
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if zipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/") and self.command == "GET":
                service.record_api(path, len(raw), len(body), (time.monotonic() - started) * 1000)

        def _error(self, status, message):
            self._json({"error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("request too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
            return data

        def _static(self, rel: str):
            path = (static_root / (rel or "index.html")).resolve()
            if static_root not in path.parents and path != static_root or not path.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            body = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        # -- routes ---------------------------------------------------------
        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/api/state":
                return self._json(views.snapshot(service))
            if path == "/api/history":
                q = {k: v[0] for k, v in parse_qs(query).items()}
                try:
                    cols = q.get("format") == "columns"
                    if "from" in q:
                        return self._json(views.history_range(service, q["from"], q.get("to"), int(q.get("points", 800)), cols))
                    return self._json(views.history(service, q.get("since"), cols))
                except ValueError as exc:
                    return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            if path == "/api/forecast/outlook":
                return self._json(service.forecaster.outlook())
            if path == "/api/meta":
                return self._json(views.meta(service))
            if path == "/api/decisions":
                q = {k: v[0] for k, v in parse_qs(query).items()}
                return self._json(views.decisions(service, min(200, int(q.get("limit", 30)))))
            if path == "/api/perf":
                return self._json(views.perf(service))
            if path == "/api/sensor/room":            # sensors that can only call a URL (Shelly webhook)
                q = {k: v[0] for k, v in parse_qs(query).items()}
                try:
                    return self._json({"ok": True, **service.room_reading(q.get("t") or q.get("tC"),
                                                                          q.get("h") or q.get("rh"))})
                except ValueError as exc:
                    return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            if path == "/api/settings":
                return self._json(views.settings(service))
            if path == "/api/simulation":
                return self._json(service.simulation())
            if path == "/api/finance":
                return self._json(views.finance(service))
            if path == "/api/log":
                q = {k: v[0] for k, v in parse_qs(query).items()}
                levels = tuple(x for x in q.get("level", "").split(",") if x) or None
                return self._json(service.store.events(min(1000, int(q.get("limit", 300))), levels))
            if path.startswith("/api/"):
                return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return self._static(path.lstrip("/"))

        def do_POST(self):
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            try:
                body = self._body()
                if parts[:2] == ["api", "settings"] and len(parts) == 4:
                    if "value" not in body:
                        raise ValueError("'value' fehlt")
                    value = service.write_setting(parts[2], parts[3], float(body["value"]))
                    return self._json({"ok": True, "value": value})
                if parts[:2] == ["api", "config"] and len(parts) == 3:
                    return self._json({"ok": True, "values": service.update_config(parts[2], body)})
                if parts == ["api", "bath"]:
                    return self._json({"ok": True, "request": service.request_bath(body.get("time"), body.get("temp"))})
                if parts == ["api", "bath", "cancel"]:
                    service.cancel_bath()
                    return self._json({"ok": True})
                if parts == ["api", "sensor", "room"]:
                    return self._json({"ok": True, **service.room_reading(body.get("t", body.get("tC")),
                                                                          body.get("h", body.get("rh")))})
                if parts == ["api", "alerts", "test"]:
                    service.test_push()
                    return self._json({"ok": True})
                if parts == ["api", "forecast", "refresh"]:
                    service.refresh_forecast()
                    return self._json({"ok": True, "error": service.forecast_error})
                return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, f"unbekannt: {exc}")
            except PermissionError as exc:
                return self._error(HTTPStatus.CONFLICT, str(exc))
            except (ValueError, TypeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # device errors
                log.exception("POST %s", self.path)
                return self._error(HTTPStatus.BAD_GATEWAY, str(exc))

    return Handler


def serve(service: Service, host: str, port: int, static_dir: Path) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(service, static_dir))
    httpd.daemon_threads = True
    return httpd
