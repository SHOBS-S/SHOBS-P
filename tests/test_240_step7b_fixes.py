"""2.4.0 step 7b regression fixes (John, 21 Nov 2026):
1. the promotion gates' linear limit is the linear ADU box (SHOBS-FLUX's number) even when it sits above the raw
   saturation setting; the saturation level is only the fallback when no linear ADU is set;
2. a disabled orange (Accent) button looks disabled, and every button shows the press (darker, sunken)."""
import os
import sys
import tempfile
import unittest

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
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        os.environ.pop("SHOBS_DATA_HOME", None)
        cls.tmp.cleanup()

    def test_flux_linear_above_saturation_is_used(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("60931")
        self.assertEqual(self.app._gate_settings()["linear_limit"], 60931.0)

    def test_linear_with_commas(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("40,000")
        self.assertEqual(self.app._gate_settings()["linear_limit"], 40000.0)

    def test_saturation_is_the_fallback(self):
        self.app.sat_limit.set("60000.0")
        self.app.linear_limit.set("")
        self.assertEqual(self.app._gate_settings()["linear_limit"], 60000.0)

    def test_accent_disabled_and_pressed_look(self):
        style = self.ca.ttk.Style(self.app)
        bg = style.map("Accent.TButton", "background")
        states = {tuple(s[:-1]) if len(s) > 2 else (s[0],): s[-1] for s in bg}
        keys = [" ".join(map(str, k)) for k in states]
        self.assertTrue(any("disabled" in k for k in keys), bg)
        self.assertTrue(any("pressed" in k for k in keys), bg)
        # disabled is listed before pressed and active, so it wins
        flat = [" ".join(map(str, s[:-1])) for s in bg]
        self.assertLess(flat.index(next(k for k in flat if "disabled" in k)),
                        flat.index(next(k for k in flat if "active" in k)))
        b = self.ca.ttk.Button(self.app, text="x", style="Accent.TButton")
        b.state(["disabled"])
        self.assertNotEqual(style.lookup("Accent.TButton", "background", ["disabled"]),
                            style.lookup("Accent.TButton", "background"))
        b.destroy()

    def test_every_button_style_sinks_when_pressed(self):
        style = self.ca.ttk.Style(self.app)
        for name in ("TButton", "Accent.TButton", "Step.TButton", "Compact.TButton"):
            self.assertEqual(str(style.lookup(name, "relief", ["pressed"])), "sunken", name)


if __name__ == "__main__":
    unittest.main()
