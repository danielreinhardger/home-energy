"""Texts: one catalog per language, one lookup.

Every text the pages, the decision logs, alerts and pushes show lives in the catalogs
homeenergy/lang/<code>.json – flat {"key": "text"} objects. Code holds only keys and values:

    t("dhw.reason.wait_sun", kwh=fnum(free_wh / 1000))

Placeholders are named, `{kwh}`, so a translation can reorder them; numbers are formatted
for the language by `fnum()` before they go in. The language is one setting per
installation (config section "ui"); `set_language()` switches it, and everything built
afterwards (decisions, explanations, labels, alerts) uses it. Texts already stored – the
decision logs, the event log – keep the language they were written in.

Adding a language: copy en.json to <code>.json, translate every value, add the code to
LANGUAGES. tests/test_i18n.py checks that every catalog has exactly the same keys and
placeholders as the source catalog and that no user-visible German is left in the code.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

LANG_DIR = Path(__file__).with_name("lang")
# code -> its own name (shown in the language setting) and the locale for numbers and dates
LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "locale": "en-GB", "decimal": ".", "group": ","},
    "de": {"name": "Deutsch", "locale": "de-DE", "decimal": ",", "group": "."},
}
SOURCE = "de"            # the catalog the texts were written in; the others are checked against it
DEFAULT = "en"           # a new installation's language

_packs: dict[str, dict[str, str]] = {}
_lang = SOURCE           # until a config sets the installation's language (Service, CLI)
_reported: set[tuple[str, str]] = set()


def pack(code: str) -> dict[str, str]:
    """The catalog of one language (read once)."""
    if code not in _packs:
        _packs[code] = json.loads((LANG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    return _packs[code]


def set_language(code: str) -> None:
    global _lang
    if code not in LANGUAGES:
        raise ValueError(f"unknown language: {code!r} ({', '.join(LANGUAGES)})")
    pack(code)                                          # fail now, not on the first text
    _lang = code


def language() -> str:
    return _lang


def locale() -> str:
    return LANGUAGES[_lang]["locale"]


def has(key: str) -> bool:
    return key in pack(SOURCE)


def t(key: str, /, **args) -> str:
    """The text for `key` in the current language, with its placeholders filled in.

    A key missing from the current catalog falls back to the source catalog and is logged
    once (the parity test keeps this from happening); a key no catalog has is a bug."""
    text = pack(_lang).get(key)
    if text is None:
        text = pack(SOURCE).get(key)
        if text is None:
            raise KeyError(f"no text for {key!r}")
        if (_lang, key) not in _reported:
            _reported.add((_lang, key))
            log.warning("text %r missing in %s.json – shown in %s", key, _lang, SOURCE)
    return text.format(**args) if args else text


def fnum(value: float | None, digits: int = 1) -> str:
    """A number as the pages show it in the current language (de 1.500,5 · en 1,500.5); "–" for none."""
    if value is None:
        return "–"
    lang = LANGUAGES[_lang]
    s = f"{value:,.{digits}f}"
    return s.replace(",", "\0").replace(".", lang["decimal"]).replace("\0", lang["group"])


def web_texts() -> dict[str, str]:
    """The texts the pages draw themselves (keys "web.*"), in the current language."""
    src, cur = pack(SOURCE), pack(_lang)
    return {k: cur.get(k, v) for k, v in src.items() if k.startswith("web.")}
