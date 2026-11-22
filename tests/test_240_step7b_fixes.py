"""2.4.0 step 7b regression fixes (John, 21 Nov 2026): a disabled orange (Accent) button looks disabled, and every
button shows the press (darker, sunken). (The promotion-gate tests of this file were removed in step 7d.)"""
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
        for name in self.styles():
            self.assertLess(lum(style.lookup(name, "background", ["pressed", "active"])),
                            lum(style.lookup(name, "background", ["active"])) - 10, name)

    def test_button_state_map_order(self):
        m = self.ca.button_state_map("#c47a2c", "#a86622", "#ffffff", disabled_bg="#d9cbbd", disabled_fg="#6e5e4e")
        self.assertEqual([s[0] for s in m["background"]], ["disabled", "pressed", "active"])
        self.assertEqual(self.ca.mix_color("#c47a2c", "#000000", 0.35), m["background"][1][1])


if __name__ == "__main__":
    unittest.main()
