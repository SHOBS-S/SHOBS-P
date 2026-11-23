"""2.4.0 fit-in F1 (#109, Fable M1): Text size on a screen where `tk scaling` does not move point-sized fonts.

On Windows and Linux, multiplying `tk scaling` makes every point-sized font, distance and matplotlib's Tk canvas
follow. On a Mac (Aqua Tk) point-sized fonts are drawn at their point size whatever the scaling says, and matplotlib
reads the scaling only on Windows and Linux. apply() therefore MEASURES whether fonts grew; when they did not, the
fonts are scaled where they are given: every font tuple or size handed to Tk (widget options, ttk styles, Font
objects), Tk's named fonts, and matplotlib's figure dpi (text, lines and markers together).
"""
import tkinter
import tkinter.font as tkfont
import tkinter.ttk as ttk

FACTOR = 1.0          # the explicit font factor in force (1.0 = none: tk scaling does the work)
_installed = False


def scaled_font(value, factor: float | None = None):
    """A font spec with its size scaled: ("Segoe UI", 9, "bold") -> ("Segoe UI", 12, "bold") at 1.33. A named font
    ("TkDefaultFont"), a Font object or anything else is returned as it is (named fonts are scaled once, at start)."""
    f = FACTOR if factor is None else factor
    if f == 1.0 or not isinstance(value, (tuple, list)) or len(value) < 2:
        return value
    size = value[1]
    try:
        n = int(size)
    except (TypeError, ValueError):
        return value
    if n == 0:
        return value
    new = max(1, int(round(abs(n) * f))) * (1 if n > 0 else -1)
    out = list(value)
    out[1] = new
    return tuple(out) if isinstance(value, tuple) else out


def _scale_kw(kw: dict) -> dict:
    if kw and "font" in kw:
        kw = dict(kw)
        kw["font"] = scaled_font(kw["font"])
    return kw


def _install():
    """The Mac path only. Covers what SHOBS-P uses: font TUPLES in widget options (create, configure, canvas items,
    tags), ttk Style.configure, and Font(font=tuple) / Font(size=N). Not covered (unused by SHOBS-P today; re-review
    N2): string specs ("Helvetica 10"), Style.map(font=...), Font.configure(size=...), and Font.copy() (scales twice)."""
    global _installed
    if _installed:
        return
    _installed = True
    orig_options = tkinter.Misc._options

    def _options(self, cnf, kw=None):
        if isinstance(cnf, dict):
            cnf = _scale_kw(cnf)
        return orig_options(self, cnf, _scale_kw(kw) if isinstance(kw, dict) else kw)
    tkinter.Misc._options = _options

    orig_fmt = ttk._format_optdict

    def _format_optdict(optdict, script=False, ignore=None):
        return orig_fmt(_scale_kw(optdict) if isinstance(optdict, dict) else optdict, script, ignore)
    ttk._format_optdict = _format_optdict

    orig_font_init = tkfont.Font.__init__

    def font_init(self, root=None, font=None, name=None, exists=False, **options):
        if font is not None and not exists:
            font = scaled_font(font)
        if "size" in options and not exists:
            try:
                n = int(options["size"])
                if n:
                    options["size"] = max(1, int(round(abs(n) * FACTOR))) * (1 if n > 0 else -1)
            except (TypeError, ValueError):
                pass
        orig_font_init(self, root, font, name, exists, **options)
    tkfont.Font.__init__ = font_init


def _linespace(widget, size=12) -> int:
    f = tkfont.Font(root=widget, family="Helvetica", size=size)
    try:
        return int(f.metrics("linespace"))
    finally:
        try:
            f.tk.call("font", "delete", f.name)
        except Exception:
            pass


def apply(widget, pct: int) -> str:
    """Scale all text and layout by pct %. Returns how: "" (100 %), "tk scaling", or "fonts" (the Mac path)."""
    global FACTOR
    if pct == 100:
        return ""
    factor = pct / 100.0
    try:
        before = _linespace(widget)
        widget.tk.call("tk", "scaling", float(widget.tk.call("tk", "scaling")) * factor)
    except Exception:
        return ""
    try:
        after = _linespace(widget)
    except Exception:
        return "tk scaling"   # re-review N3: the scaling is in place; whether fonts followed cannot be told
    if before > 0 and abs(after / before - 1.0) >= 0.5 * abs(factor - 1.0):
        return "tk scaling"   # point fonts followed (Windows, Linux)
    # Fonts did not follow the scaling (Aqua): scale them where they are given.
    FACTOR = factor
    _install()
    try:
        for name in tkfont.names(widget):
            f = tkfont.nametofont(name, root=widget)
            n = int(f.cget("size"))
            if n:
                f.configure(size=max(1, int(round(abs(n) * factor))) * (1 if n > 0 else -1))
    except Exception:
        pass
    _scale_figures(factor)
    return "fonts"


def _scale_figures(factor: float):
    """Every matplotlib Figure gets factor x its dpi (SHOBS-P passes dpi=100 itself), so its text, lines and
    markers grow together; the Tk canvas still fills the panel it is packed in."""
    try:
        import matplotlib
        import matplotlib.figure as mfig
        matplotlib.rcParams["figure.dpi"] = float(matplotlib.rcParams["figure.dpi"]) * factor
        orig = mfig.Figure.__init__
        if getattr(orig, "_shobs_scaled", False):
            return

        def init(self, *a, **k):
            if "dpi" in k and k["dpi"] is not None:
                k["dpi"] = float(k["dpi"]) * factor
            orig(self, *a, **k)
        init._shobs_scaled = True
        mfig.Figure.__init__ = init
    except Exception:
        pass
