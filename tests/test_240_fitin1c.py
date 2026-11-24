"""2.4.0 fit-in F1c (#109, from the iMac at 150 %): main pages scroll when the window is shorter than they need, plots
keep a minimum height, the Input page's hints go under their entries when the room beside them is too narrow, and
the side bar widens with the Text size and scrolls."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import page_scroll  # noqa: E402
from tkinter import ttk  # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


class TestPure(unittest.TestCase):
    def test_body_height(self):
        self.assertEqual(page_scroll.body_height(800, 500), 800)    # fits: the page fills the window
        self.assertEqual(page_scroll.body_height(600, 1400), 1400)  # too short: as tall as the page needs
        self.assertEqual(page_scroll.body_height(0, 0), 1)

    def test_needs_scroll(self):
        self.assertFalse(page_scroll.needs_scroll(800, 800))
        self.assertFalse(page_scroll.needs_scroll(800, 801))        # a pixel of rounding is not a scroll
        self.assertTrue(page_scroll.needs_scroll(800, 900))

    def test_scroll_target(self):
        f = page_scroll.scroll_target
        self.assertIsNone(f(0, 500, 400, 600, 20))            # the page fits: nothing to do
        self.assertIsNone(f(100, 500, 2000, 300, 20))         # already in view
        self.assertEqual(f(0, 500, 2000, 900, 20), 900 + 20 + 8 - 500)   # below: scroll it up to the bottom edge
        self.assertEqual(f(800, 500, 2000, 300, 20), 300 - 8)            # above: to the top edge
        self.assertEqual(f(0, 500, 2000, 1990, 30), 1500)                # never past the end

    def test_wheel_units(self):
        f = page_scroll.wheel_units
        self.assertEqual(f(120, "win32"), -1)
        self.assertEqual(f(-240, "win32"), 2)
        self.assertEqual(f(30, "win32"), -1)        # a fine-grained wheel still moves one step
        self.assertEqual(f(3, "darwin"), -1)
        self.assertEqual(f(-1, "darwin"), 1)
        self.assertEqual(f(0, "win32"), 0)
        self.assertEqual(f("x", "win32"), 0)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestPageScroll(unittest.TestCase):
    def setUp(self):
        self.root = _tk.Tk()
        self.root.geometry("400x300+0+0")

    def tearDown(self):
        self.root.destroy()

    def settle(self):
        for _ in range(3):
            self.root.update()
            self.root.update_idletasks()

    def test_fits_then_scrolls(self):
        ps = page_scroll.PageScroll(self.root)
        ps.pack(fill="both", expand=True)
        filler = _tk.Frame(ps.body, height=100, width=100)
        filler.pack(fill="both", expand=True)
        self.settle()
        ps.refit()
        self.assertFalse(ps.scrollable())
        self.assertFalse(ps.vbar.winfo_manager())           # no scroll bar while the page fits
        self.assertGreaterEqual(ps.body.winfo_height(), ps.canvas.winfo_height() - 1)   # the page fills the view
        filler.configure(height=900)
        self.settle()
        ps.refit()
        self.settle()
        self.assertTrue(ps.scrollable())
        self.assertEqual(ps.body.winfo_height(), ps.wanted_height())
        self.assertTrue(ps.vbar.winfo_manager())

    def test_wheel_scrolls_page_and_spares_plots(self):
        ps = page_scroll.PageScroll(self.root)
        ps.pack(fill="both", expand=True)
        lab = _tk.Label(ps.body, text="row")
        lab.pack()
        plot = _tk.Canvas(ps.body, width=100, height=1200)
        plot.pack()
        self.settle()
        ps.refit()
        self.settle()
        ps._tag_children()
        self.assertIn(ps._tag, lab.bindtags())
        self.assertNotIn(ps._tag, plot.bindtags())        # a plot or image keeps the wheel (zoom)
        before = ps.canvas.yview()[0]
        ev = type("E", (), {"delta": -120 if sys.platform != "darwin" else -1, "widget": lab})()
        self.assertEqual(ps._wheel(ev), "break")
        self.assertGreater(ps.canvas.yview()[0], before)

    def test_wheel_never_changes_a_drop_down(self):
        """Review M1: the wheel over Binning/Mount/Time zone scrolls the page (or does nothing), never the value."""
        ps = page_scroll.PageScroll(self.root)
        ps.pack(fill="both", expand=True)
        var = _tk.StringVar(value="1")
        box = ttk.Combobox(ps.body, textvariable=var, values=("1", "2", "4"), state="readonly")
        box.pack()
        self.settle()
        ps.refit()
        ps._tag_children()
        self.assertIn(ps._tag, box.bindtags())
        ev = type("E", (), {"delta": -120, "widget": box})()
        self.assertEqual(ps._wheel(ev), "break")              # page fits: no scroll, and the value is kept
        tall = _tk.Frame(ps.body, height=1500)
        tall.pack()
        self.settle()
        ps.refit()
        self.settle()
        before = ps.canvas.yview()[0]
        self.assertEqual(ps._wheel(ev), "break")
        self.assertGreater(ps.canvas.yview()[0], before)      # page scrolls instead
        self.assertEqual(var.get(), "1")

    def test_focus_scrolls_into_view(self):
        ps = page_scroll.PageScroll(self.root)
        ps.pack(fill="both", expand=True)
        _tk.Frame(ps.body, height=1200).pack()
        entry = ttk.Entry(ps.body)
        entry.pack()
        self.settle()
        ps.refit()
        self.settle()
        ps._tag_children()
        ev = type("E", (), {"widget": entry})()
        ps._show_focus(ev)
        self.settle()
        top = ps.canvas.canvasy(0)
        y = entry.winfo_rooty() - ps.body.winfo_rooty()
        self.assertGreaterEqual(y, top)
        self.assertLessEqual(y + entry.winfo_height(), top + ps.view_height())

    def test_wheel_does_nothing_when_page_fits(self):
        ps = page_scroll.PageScroll(self.root)
        ps.pack(fill="both", expand=True)
        _tk.Label(ps.body, text="row").pack()
        self.settle()
        ps.refit()
        ev = type("E", (), {"delta": -120, "widget": ps.body})()
        self.assertIsNone(ps._wheel(ev))

    def test_min_plot_height_comes_back(self):
        c = _tk.Canvas(self.root)
        c.pack()
        want = page_scroll.min_plot_height(c, 300, 1.5, width_px=400)
        self.assertEqual(want, 450)
        self.assertEqual(int(c.cget("height")), 450)
        self.assertEqual(int(c.cget("width")), 400)
        c.configure(width=1080, height=976)               # what matplotlib does on a new pixel ratio
        c.pack_forget()
        c.pack()                                          # shown again: <Map>
        self.settle()
        self.assertEqual(int(c.cget("height")), 450)
        self.assertEqual(int(c.cget("width")), 400)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestApp(unittest.TestCase):
    """The real window at 150 %, in a small window."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.TemporaryDirectory()
        sf = os.path.join(cls.tmp.name, "s.json")
        with open(sf, "w") as fh:
            json.dump({"text_size": 150}, fh)
        cls.p = mock.patch.object(ca, "SETTINGS_FILE", sf)
        cls.p.start()
        r = _tk.Tk()   # Tk keeps "tk scaling" per display for the whole process: put it back afterwards
        cls.scaling = float(r.tk.call("tk", "scaling"))
        r.destroy()
        cls.app = ca.App()
        cls.app.geometry("1280x800+0+0")
        for _ in range(4):
            cls.app.update()
            cls.app.after(200)
            cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.p.stop()
        cls.tmp.cleanup()
        r = _tk.Tk()
        r.tk.call("tk", "scaling", cls.scaling)
        r.destroy()
        try:   # the Mac font path's factor is module state: leave it as found
            import text_scale
            text_scale.FACTOR = 1.0
        except Exception:
            pass

    def boxes(self, w, out):
        for k in w.winfo_children():
            if hasattr(k, "_hints_under"):
                out.append(k)
            self.boxes(k, out)
        return out

    def test_every_main_page_scrolls(self):
        for page in list(self.app.page_frames) + [self.app.discovery_page, self.app.credits_page,
                                                  self.app.transit_page.frame]:
            self.assertIsInstance(page, page_scroll.PageScroll)

    def test_input_page_reaches_optics(self):
        app = self.app
        app.show_step(0)
        app.update()
        host = app.page_frames[0]
        host.refit()
        app.update()
        self.assertTrue(host.scrollable())                # the iMac case: the page is taller than the window
        names = [b.cget("text") for b in self.boxes(host, [])]
        self.assertIn("Site", names)
        self.assertIn("Optics", names)
        host.canvas.yview_moveto(1.0)
        app.update()
        self.assertGreater(host.canvas.yview()[0], 0.0)   # and the bottom (Optics, Mount) can be scrolled to

    def test_hints_go_under_when_narrow(self):
        app = self.app
        app.show_step(0)
        app.update()
        site = [b for b in self.boxes(app.page_frames[0], []) if b.cget("text") == "Site"][0]
        self.assertTrue(site._hints_under())
        under = [k for k in site.winfo_children() if isinstance(k, self.ca.ttk.Label)
                 and str(k.cget("text")) == "west is negative"][0]
        info = under.grid_info()
        self.assertEqual(int(info["column"]), 0)
        self.assertGreater(int(info["columnspan"]), 1)

    def test_plot_min_heights(self):
        s = self.ca.content_scale(self.app)
        self.assertGreaterEqual(s, 1.4)
        w = self.app.out_canvas.get_tk_widget()
        self.assertEqual(w._shobs_min_height, int(round(self.ca.PLOT_MIN_PX["output"] * s)))
        tw = self.app.transit_page.canvas.get_tk_widget()
        self.assertEqual(tw._shobs_min_height, int(round(self.ca.PLOT_MIN_PX["transit"] * s)))

    def test_transit_table_does_not_ratchet(self):
        """Review M2: in a short window the table gives up rows (down to 3) for the plot's minimum."""
        app = self.app
        app.set_mode("transits")
        app.show_step(self.ca.STEP_OUTPUT)
        for _ in range(4):
            app.update()
            app.after(150)
            app.update()
        tp = app.transit_page
        tp._fit_strip()
        app.update()
        self.assertEqual(int(tp.table.cget("height")), 3)

    def test_side_bar(self):
        self.assertEqual(self.ca.side_bar_width(150), 345)
        self.assertEqual(self.ca.side_bar_width(100), 230)
        self.assertEqual(self.ca.side_bar_width(90), 207)
        self.assertEqual(self.ca.side_bar_width("x"), 230)
        self.assertIsInstance(self.app.side_host, page_scroll.PageScroll)
        self.assertIs(self.app.side, self.app.side_host.body)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestHintsBesideAt100(unittest.TestCase):
    def test_wide_window_keeps_hints_beside(self):
        import clearv_app as ca
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ca, "SETTINGS_FILE", os.path.join(tmp, "s.json")):
            app = ca.App()
            try:
                app.geometry("1600x1000+0+0")
                for _ in range(4):
                    app.update()
                    app.after(200)
                    app.update()
                found = []

                def walk(w):
                    for k in w.winfo_children():
                        if hasattr(k, "_hints_under"):
                            found.append(k)
                        walk(k)
                walk(app.page_frames[0])
                self.assertTrue(found)
                for box in found:
                    self.assertFalse(box._hints_under(), box.cget("text"))
            finally:
                app.destroy()


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestTransitAt100(unittest.TestCase):
    def test_table_keeps_rows_when_page_fits(self):
        import clearv_app as ca
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ca, "SETTINGS_FILE", os.path.join(tmp, "s.json")):
            app = ca.App()
            try:
                app.geometry("1600x1060+0+0")
                app.set_mode("transits")
                app.show_step(ca.STEP_OUTPUT)
                for _ in range(5):
                    app.update()
                    app.after(150)
                    app.update()
                tp = app.transit_page
                tp._fit_strip()
                app.update()
                rows = int(tp.table.cget("height"))
                self.assertGreaterEqual(rows, 3)
                # no ratchet: the page's want never exceeds the view because of the table
                tp.frame.refit()
                app.update()
                if tp.frame.scrollable():
                    self.assertEqual(rows, 3)
            finally:
                app.destroy()


class TestContentScale(unittest.TestCase):
    def test_mac_factor_not_counted_twice(self):
        import clearv_app as ca
        import text_scale
        w = mock.Mock()
        with mock.patch.object(text_scale, "FACTOR", 1.5), mock.patch.object(ca, "ui_scale", return_value=1.125):
            self.assertEqual(ca.content_scale(w), 1.5)
        with mock.patch.object(text_scale, "FACTOR", 1.0), mock.patch.object(ca, "ui_scale", return_value=1.35):
            self.assertEqual(ca.content_scale(w), 1.35)


if __name__ == "__main__":
    unittest.main()
