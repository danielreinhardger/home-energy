"""Every text reaches every language, and no user-visible German is left in the code.

The catalogs are homeenergy/lang/<code>.json (see homeenergy/i18n.py). These checks fail when a key
is added to one catalog only, a placeholder differs, a key used in the code is missing, a page
key isn't sent to the page, or a German literal slips back into the code.
"""
import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from homeenergy import config as config_mod
from homeenergy import i18n, views

from fakes import use_language
from test_runtime import make_service

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{(\w*)\}|%[.\d]*[sdf]")
# texts that are the same word in English and German (names, symbols, loanwords)
SAME_IN_ALL = {"COP {cop}", "Filter", "Info", "Init", "KOSTAL PLENTICORE plus (PV)", "PV", "Standby", "Status", "Stop",
               "String 1: {dc1} · String 2: {dc2}", "Update", "Wallbox", "{soc} %"}
GERMAN = re.compile(
    r"[äöüÄÖÜß]|\b(und|oder|nicht|der|die|das|mit|bei|nur|heute|morgen|Uhr|wird|ist|kein|keine|Wärme|Warmwasser|"
    r"Speicher|Haus|Einspeisung|Bezug|Batterie|Regelung|Prognose|Raum|Strom|Netz|Tage|Ziel|Soll|Komfort|Vorrat|"
    r"Sonne|ja|nein|frei|Jetzt|Heute|Morgen|Speichern|Lädt|Fehler|Warnung|Leistung|Verdichter|Vorlauf|Datenblatt)\b")


def python_literals(path: Path):
    """(line, text) of every string literal that isn't a docstring, a catalog key or a log format."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        p = parent.get(node)
        if isinstance(p, ast.Expr):
            continue                                                         # docstring
        while isinstance(p, (ast.JoinedStr, ast.FormattedValue)):
            p = parent.get(p)
        if isinstance(p, ast.Call) and getattr(p.func, "id", getattr(p.func, "attr", "")) in ("t", "has"):
            continue                                                         # a key
        yield node.lineno, node.value


def js_literals(src: str):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(?m)^\s*//.*$|(?<=[;,{}()\s])//[^\n\"'`]*$", "", src)
    for m in re.finditer(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', src):
        if src[max(0, m.start() - 2):m.start()] in ("t(", "("[:0]):
            continue
        yield src.count("\n", 0, m.start()) + 1, m.group(0)[1:-1]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.source = i18n.pack(i18n.SOURCE)

    def test_every_language_has_every_text(self):
        self.assertGreater(len(self.source), 500)                            # not vacuously green
        for code in i18n.LANGUAGES:
            pack = i18n.pack(code)
            self.assertEqual(sorted(set(self.source) - set(pack)), [], f"{code}.json misses these keys")
            self.assertEqual(sorted(set(pack) - set(self.source)), [], f"{code}.json has extra keys")
            for key, text in pack.items():
                self.assertTrue(text.strip(), f"{code}: {key} is empty")
                self.assertEqual(sorted(PLACEHOLDER.findall(text)), sorted(PLACEHOLDER.findall(self.source[key])),
                                 f"{code}: {key} has other placeholders than {i18n.SOURCE}")

    def test_translations_differ_from_the_source(self):
        for code in i18n.LANGUAGES:
            if code == i18n.SOURCE:
                continue
            same = sorted(k for k, v in i18n.pack(code).items() if v == self.source[k] and v not in SAME_IN_ALL)
            self.assertEqual(same, [], f"{code}: untranslated (or add a true loanword to SAME_IN_ALL)")

    def test_catalogs_are_utf8_json_objects(self):
        for code in i18n.LANGUAGES:
            data = json.loads((i18n.LANG_DIR / f"{code}.json").read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)


class CodeTests(unittest.TestCase):
    def setUp(self):
        self.keys = set(i18n.pack(i18n.SOURCE))

    def test_keys_in_the_code_exist(self):
        missing = []
        for path in list((ROOT / "homeenergy").rglob("*.py")) + list((ROOT / "web").glob("*.js")):
            if "__pycache__" in path.parts or path.stem == "i18n":         # the lookup itself (doc examples)
                continue
            src = path.read_text(encoding="utf-8")
            for key in re.findall(r"""(?<![\w.])(?:i18n\.)?t\(\s*["']([\w.]+)["']""", src):
                if key not in self.keys:
                    missing.append(f"{path.name}: {key}")
            for prefix in re.findall(r"""(?<![\w.])(?:i18n\.)?t\(\s*f?["`]([\w.]+)\.(?:\{|\$\{)""", src):
                if not any(k.startswith(prefix + ".") for k in self.keys):
                    missing.append(f"{path.name}: {prefix}.*")
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        for key in re.findall(r'data-i18n(?:-aria-label)?="([^"]+)"', html):
            if key not in self.keys:
                missing.append(f"index.html: {key}")
        self.assertEqual(missing, [])

    def test_the_page_only_uses_page_keys(self):
        # only "web.*" texts travel to the page (i18n.web_texts)
        for path in list((ROOT / "web").glob("*.js")) + [ROOT / "web/index.html"]:
            src = path.read_text(encoding="utf-8")
            keys = re.findall(r"""(?<![\w.])t\(\s*["'`]([\w.]+)""", src) + re.findall(r'data-i18n(?:-aria-label)?="([^"]+)"', src)
            self.assertEqual([k for k in keys if not k.startswith("web.")], [], path.name)

    def test_no_german_left_in_python(self):
        found = []
        for path in (ROOT / "homeenergy").rglob("*.py"):
            if "__pycache__" in path.parts or path.name == "i18n.py":
                continue
            for line, text in python_literals(path):
                if text.isupper():
                    continue                                                 # vendor page labels (ISG HTML)
                if GERMAN.search(text):
                    found.append(f"{path.relative_to(ROOT)}:{line} {text[:60]!r}")
        # openWB device names (user data) may end in these words; they are matched, not shown
        found = [f for f in found if not re.search(r"flow\.py:\d+ ' (Zähler|Wechselrichter|Speicher|Batterie)'", f)]
        self.assertEqual(found, [])

    def test_no_german_left_on_the_page(self):
        found = []
        for path in (ROOT / "web").glob("*.js"):
            for line, text in js_literals(path.read_text(encoding="utf-8")):
                if GERMAN.search(text):
                    found.append(f"{path.name}:{line} {text[:60]!r}")
        html = re.sub(r"<!--[\s\S]*?-->", "", (ROOT / "web/index.html").read_text(encoding="utf-8"))
        for text in re.findall(r">([^<>]+)<", html):
            if GERMAN.search(text):
                found.append(f"index.html {text.strip()[:60]!r}")
        self.assertEqual(found, [])


class EnglishTests(unittest.TestCase):
    """The whole read model in English: no missing text, no German."""

    def test_pages_render_in_english(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d, use_language("de"):
            tmp = Path(d)
            (tmp / "config.json").write_text(json.dumps({"ui": {"language": "en"}}), encoding="utf-8")
            svc = make_service(tmp)
            try:
                self.assertEqual(i18n.language(), "en")
                svc.poll_once()
                out = json.dumps([views.meta(svc), views.settings(svc), views.snapshot(svc), svc.simulation()],
                                 ensure_ascii=False, default=str)
                self.assertNotRegex(out, r"[äöüÄÖÜß]")
                self.assertIn("Hot water", out)
                self.assertEqual(views.meta(svc)["locale"], "en-GB")
            finally:
                svc.stop()

    def test_language_setting_switches_everything(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d, use_language("de"):
            svc = make_service(Path(d))                                      # German config
            try:
                before = views.meta(svc)
                self.assertEqual(before["language"], "de")
                svc.update_config("ui", {"language": "en"})
                after = views.meta(svc)
                self.assertEqual((i18n.language(), after["language"]), ("en", "en"))
                self.assertNotEqual(before["version"], after["version"])     # the page reloads its texts
                self.assertEqual(after["texts"]["web.html.uebersicht"], "Overview")
                with self.assertRaises(ValueError):
                    svc.update_config("ui", {"language": "xx"})
            finally:
                svc.stop()

    def test_numbers_follow_the_language(self):
        with use_language("en"):
            self.assertEqual(i18n.fnum(1500.5), "1,500.5")
        with use_language("de"):
            self.assertEqual(i18n.fnum(1500.5), "1.500,5")
        self.assertEqual(config_mod.AppConfig().ui.language, "en")           # new installations: English


if __name__ == "__main__":
    unittest.main()
