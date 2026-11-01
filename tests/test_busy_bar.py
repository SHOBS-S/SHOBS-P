"""2.2.20: busy_bar.BusyBar (copied from SHOBS-SCHED 1.2.0)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tkinter as tk
import busy_bar


class TestBusyBar(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.bar = busy_bar.BusyBar(self.root)
        self.bar.pack()

    def tearDown(self):
        self.root.destroy()

    def test_words_and_end(self):
        t1 = self.bar.begin("Looking up A…")
        t2 = self.bar.begin("Saving…")
        self.assertEqual(self.bar.text, "Saving…")
        self.bar.words(t1, "Looking up B…")
        self.assertEqual(self.bar.text, "Saving…")
        self.bar.end(t2)
        self.assertEqual(self.bar.text, "Looking up B…")
        self.bar.end(t1)
        self.bar.end(t1)
        self.assertEqual(self.bar.text, "")
        self.assertFalse(self.bar.busy)

    def test_short_job_never_shows(self):
        t = self.bar.begin("quick")
        self.root.update()
        self.bar.end(t)
        self.root.after(400, self.root.quit)
        self.root.mainloop()
        self.assertFalse(self.bar.shown)


if __name__ == "__main__":
    unittest.main()
