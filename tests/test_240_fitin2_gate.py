"""2.4.0 fit-in F2 (#79 item 6): the grey-out helper."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gate  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk
    _root = tk.Tk()
    _root.withdraw()
except Exception:   # no display
    _root = None


@unittest.skipIf(_root is None, "no display")
class GateTest(unittest.TestCase):
    def test_reason_greys_and_clears(self):
        busy = {"on": False}
        g = gate.Gates(busy=lambda: busy["on"])
        need = {"why": "Scan the field first."}
        b = ttk.Button(_root, text="Save")
        g.add(b, lambda: need["why"])
        self.assertTrue(b.instate(["disabled"]))
        self.assertEqual(g.reason(b), "Scan the field first.")
        need["why"] = ""
        g.refresh()
        self.assertFalse(b.instate(["disabled"]))
        busy["on"] = True
        g.refresh()
        self.assertTrue(b.instate(["disabled"]))
        self.assertEqual(g.reason(b), gate.BUSY_REASON)

    def test_busy_false_and_tk_button_and_broken_check(self):
        logged = []
        g = gate.Gates(busy=lambda: True, log=logged.append)
        b = ttk.Button(_root, text="x")
        g.add(b, lambda: "", busy=False)
        self.assertFalse(b.instate(["disabled"]))
        t = tk.Button(_root, text="y")
        g.add(t, lambda: "no", busy=False)
        self.assertEqual(str(t.cget("state")), "disabled")
        bad = ttk.Button(_root, text="z")
        g.add(bad, lambda: 1 / 0, busy=False)
        self.assertFalse(bad.instate(["disabled"]))
        g.refresh()
        self.assertEqual(len(logged), 1)

    def test_destroyed_widget_dropped(self):
        g = gate.Gates()
        b = ttk.Button(_root, text="x")
        g.add(b, lambda: "no")
        b.destroy()
        g.refresh()
        self.assertFalse(g.is_gated(b))


if __name__ == "__main__":
    unittest.main()
