"""Error log: every warning and error the service logs, kept in the database (store.events).

A logging handler, so nothing has to call it: device read failures, failed writes to the
heat pump, forecast and push problems, crashes in a request all land here. Besides those,
records logged with `extra=EVENT` are kept too (INFO level) - used for the good news that
belongs next to an error: a device answering again, an alert that is over, a restart.
Repeats of the same message within 10 minutes count up on one row (store.add_event).
"""
from __future__ import annotations

import logging

from .store import Store

EVENT = {"event": True}                 # log.info(..., extra=EVENT) -> also kept in the log
LEVELS = {logging.INFO: "info", logging.WARNING: "warning", logging.ERROR: "error", logging.CRITICAL: "error"}


class StoreHandler(logging.Handler):
    def __init__(self, store: Store):
        super().__init__(level=logging.INFO)
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING and not getattr(record, "event", False):
            return
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            source = record.name.removeprefix("homeenergy.")
            self.store.add_event(record.created, LEVELS.get(record.levelno, "warning"), source, message)
        except Exception:                # the log must never break what is being logged
            pass


def install(store: Store) -> StoreHandler:
    root = logging.getLogger()
    handler = StoreHandler(store)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return handler


def uninstall(handler: StoreHandler) -> None:
    logging.getLogger().removeHandler(handler)
