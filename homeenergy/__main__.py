"""Command line: `python -m homeenergy serve | read <device> | set isg <key> <value>`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config as config_mod
from .device import ModbusDevice
from .devices import SPECS

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="homeenergy")
    ap.add_argument("--config", type=Path, default=ROOT / "config.json")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="run the dashboard server")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    rp = sub.add_parser("read", help="print all values of one device as JSON")
    rp.add_argument("device", choices=sorted(SPECS))
    wp = sub.add_parser("set", help="write one device setting")
    wp.add_argument("device", choices=sorted(SPECS))
    wp.add_argument("key")
    wp.add_argument("value", type=float)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config_mod.load(args.config)

    if args.cmd == "serve":
        from .service import Service
        from .web import serve
        service = Service(args.config, args.data)
        host, port = args.host or cfg.web_host, args.port or cfg.web_port
        httpd = serve(service, host, port, ROOT / "web")
        service.start()
        logging.info("dashboard on http://%s:%d/", host, port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            service.stop()
        return 0

    dev = ModbusDevice(SPECS[args.device], getattr(cfg.devices, args.device))
    try:
        if args.cmd == "read":
            out = {"values": dev.read_values(), "settings": dev.read_settings() if dev.spec.settings else {}}
            sys.stdout.reconfigure(encoding="utf-8")
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"{dev.setting(args.key).label}: {dev.write_setting(args.key, args.value)}")
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
