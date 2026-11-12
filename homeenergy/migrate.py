"""One-time import of the JSON files used before the SQLite store (2026-11-09).

After the import the old files are moved, unchanged, to backup/legacy-json/ – kept as a
backup, out of the way of the live data (nothing reads them any more).
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .store import Store, to_ts

log = logging.getLogger(__name__)

DECISION_FILES = {"dhw": "decisions.jsonl", "heating": "heating_decisions.jsonl"}
LEGACY_FILES = ("history.jsonl", "finance.json", *DECISION_FILES.values())
ARCHIVE = Path("backup") / "legacy-json"


def _jsonl(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def import_legacy(store: Store, data_dir: Path) -> dict:
    """Import history.jsonl, finance.json and the decision logs once (marker in `meta`)."""
    if store.get_meta("legacy_imported"):
        archive_legacy(data_dir)
        return {}
    done = {}
    rows = []
    for s in _jsonl(data_dir / "history.jsonl"):
        try:
            rows.append((to_ts(s["t"]), s))
        except (KeyError, ValueError, TypeError):
            continue
    done["samples"] = store.add_samples(rows)

    try:
        fin = json.loads((data_dir / "finance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fin = {}
    days = {d["day"]: d for d in fin.get("days", []) if isinstance(d, dict) and "day" in d}
    if days:
        store.save_days(days)
    if fin.get("last_raw"):
        store.set_meta("last_raw", fin["last_raw"])
    done["days"] = len(days)

    for kind, name in DECISION_FILES.items():
        entries = list(_jsonl(data_dir / name))
        store.add_decisions(kind, entries)
        done[f"decisions_{kind}"] = len(entries)

    store.set_meta("legacy_imported", done)
    log.info("imported legacy files into SQLite: %s", done)
    archive_legacy(data_dir)
    return done


def archive_legacy(data_dir: Path) -> list[str]:
    """Move the imported JSON files (and finance.json.bak-* copies) to backup/legacy-json/."""
    names = [n for n in LEGACY_FILES if (data_dir / n).is_file()]
    names += sorted(p.name for p in data_dir.glob("finance.json.bak-*") if p.is_file())
    if not names:
        return []
    target = data_dir / ARCHIVE
    target.mkdir(parents=True, exist_ok=True)
    for n in names:
        if (target / n).exists():                       # never overwrite an archived copy
            continue
        shutil.move(str(data_dir / n), str(target / n))
    log.info("moved the imported JSON files to %s: %s", target, ", ".join(names))
    return names
