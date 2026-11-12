"""Settings are defined once (settings.py): every field is a form setting or explicitly hidden."""
import unittest
from dataclasses import fields

from homeenergy import config
from homeenergy.settings import undeclared


class SettingsTests(unittest.TestCase):
    def test_every_field_is_declared(self):
        for section, cls in config.UI_SECTIONS.items():
            self.assertEqual(undeclared(cls), [], f"{section}: give these fields num()/flag()/clock() or hidden()")

    def test_defaults_lie_within_their_range(self):
        for section, cls in config.UI_SECTIONS.items():
            obj = cls()
            for name, meta in config.UI_FIELDS[section].items():
                value = getattr(obj, name)
                if meta["type"] == "number" and value is not None:
                    self.assertLessEqual(meta.get("min", value), value, f"{section}.{name}")
                    self.assertGreaterEqual(meta.get("max", value), value, f"{section}.{name}")
                self.assertTrue(meta["label"], f"{section}.{name} needs a label")

    def test_forms_follow_the_dataclasses(self):
        # the forms show exactly the declared settings, in field order
        for section, cls in config.UI_SECTIONS.items():
            shown = [f.name for f in fields(cls) if f.metadata.get("ui")]
            self.assertEqual(list(config.UI_FIELDS[section]), shown)

    def test_update_checks_the_range(self):
        cfg = config.AppConfig()
        with self.assertRaises(ValueError):
            config.update_section(cfg, "dhw", {"comfort_temp": 80})
        new = config.update_section(cfg, "heating", {"step_k": 0.1})
        self.assertEqual(new.heating.step_k, 0.1)


if __name__ == "__main__":
    unittest.main()
