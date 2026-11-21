"""2.4.0 step 7b regression fixes (John, 21 Nov 2026):
1. the promotion gates' linear limit is the linear ADU box (SHOBS-FLUX's number) even when it sits above the raw
   saturation setting; the saturation level is only the fallback when no linear ADU is set;
2. a disabled orange (Accent) button looks disabled, and every button shows the press (darker, sunken)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestStep7bFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["SHOBS_DATA_HOME"] = cls.tmp.name
        ca.LOG_FILE = os.path.join(cls.tmp.name, "test.log")
        for nm in ("showinfo", "showwarning", "showerror"):
            setattr(ca.messagebox, nm, lambda *a, **k: None)
        for nm in ("askyesno", "askokcancel", "askyesnocancel"):
            setattr(ca.messagebox, nm, lambda *a, **k: False)
        cls.ca = ca
        # never the user's own ~/.shobs_p_settings.json (conftest does this under pytest; this covers a direct run)
        cls.patch = mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp.name, "s.json"))
        cls.patch.start()
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.patch.stop()
        os.environ.pop("SHOBS_DATA_HOME", None)
        cls.tmp.cleanup()

    def test_flux_linear_above_saturation_is_used(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("60931")
        self.assertEqual(self.app._gate_settings()["linear_limit"], 60931.0)

    def test_saturation_level_is_passed_too(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("60931")
        self.assertEqual(self.app._gate_settings()["sat_level"], 60000.0)

    def test_linear_with_commas(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("40,000")
        self.assertEqual(self.app._gate_settings()["linear_limit"], 40000.0)

    def test_saturation_is_the_fallback(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("")
        self.assertEqual(self.app._gate_settings()["linear_limit"], 60000.0)

    def test_odd_linear_text_falls_back_to_saturation(self):
        self.app.sat_limit.set("60000.0")
        for text in ("0", "nan", "60931 ADU", "-5"):
            self.app.linear_limit.set(text)
            self.assertEqual(self.app._gate_settings()["linear_limit"], 60000.0, text)

    def styles(self):
        return ["TButton", "Accent.TButton", "Compact.TButton", "Step.TButton"] + [
            f"Mode{k}.TButton" for k in self.ca.MODES]

    def test_disabled_buttons_look_disabled(self):
        style = self.ca.ttk.Style(self.app)
        for name in ("TButton", "Accent.TButton", "Compact.TButton"):
            self.assertNotEqual(str(style.lookup(name, "background", ["disabled"])),
                                str(style.lookup(name, "background")), name)
            self.assertNotEqual(str(style.lookup(name, "foreground", ["disabled"])),
                                str(style.lookup(name, "foreground")), name)
        self.assertEqual(str(style.lookup("Accent.TButton", "background", ["disabled", "active"])), "#d9cbbd")

    def test_every_button_darkens_when_pressed_in_every_mode(self):
        style = self.ca.ttk.Style(self.app)
        for mode in self.ca.MODES:
            self.app.set_mode(mode)
            self.app.update()
            for name in self.styles():
                pressed = str(style.lookup(name, "background", ["pressed", "active"]))
                self.assertNotEqual(pressed, str(style.lookup(name, "background", ["active"])), (mode, name))
                self.assertNotEqual(pressed, str(style.lookup(name, "background")), (mode, name))
                self.assertTrue(str(style.lookup(name, "lightcolor", ["pressed", "active"])), (mode, name))
        self.app.set_mode("variables")

    def test_pressed_is_darker(self):
        style = self.ca.ttk.Style(self.app)

        def lum(c):
            r, g, b = (int(str(c)[i:i + 2], 16) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
        for name in self.styles() + ["TMenubutton"]:
            self.assertLess(lum(style.lookup(name, "background", ["pressed", "active"])),
                            lum(style.lookup(name, "background", ["active"])) - 10, name)

    def test_old_gate_reports_are_outdated(self):
        self.app.gate_reports = {"S": {"version": 1}}
        self.assertTrue(self.app._gate_reports_outdated())
        self.app.gate_reports = {"S": {"version": self.ca.promotion.GATES_VERSION}}
        self.assertFalse(self.app._gate_reports_outdated())
        self.app.gate_reports = {}

    def test_registry_counts_a_rule_change_as_new_data(self):
        import registry
        doc = registry.empty()
        lin_ok = {"key": "linear", "label": "Linear limit", "set": "campaign", "value": "", "need": "", "pass": True,
                  "cleared_by": "ours", "why": ""}
        rep = {"star": "S", "period_days": 0.1, "nights": ["JD1", "JD2"], "n_points": 100, "candidate": True,
               "campaign": True, "gates": [lin_ok], "summary": "", "version": 1}
        rec, _ = registry.upsert_from_report(doc, rep, "S", ra=10.0, dec=20.0)
        self.assertEqual(rec["evidence"]["gates_version"], 1)
        registry.set_campaign(rec, True, by="John")
        # same nights and points, judged again under the new rule set: the saturation failure turns the campaign off
        lin_bad = dict(lin_ok, **{"pass": False, "cleared_by": None, "why": "saturation"})
        rec2, event = registry.upsert_from_report(doc, dict(rep, version=2, gates=[lin_bad]), "S", ra=10.0, dec=20.0)
        self.assertEqual(event, "campaign_fail")
        self.assertFalse(rec2["campaign"])
        self.assertEqual(rec2["evidence"]["gates_version"], 2)
        # and the same report again is not new data
        rec3, event3 = registry.upsert_from_report(doc, dict(rep, version=2, gates=[lin_bad]), "S", ra=10.0, dec=20.0)
        self.assertNotEqual(event3, "campaign_fail")

    def test_button_state_map_order(self):
        m = self.ca.button_state_map("#c47a2c", "#a86622", "#ffffff", disabled_bg="#d9cbbd", disabled_fg="#6e5e4e")
        self.assertEqual([s[0] for s in m["background"]], ["disabled", "pressed", "active"])
        self.assertEqual(self.ca.mix_color("#c47a2c", "#000000", 0.35), m["background"][1][1])


if __name__ == "__main__":
    unittest.main()
