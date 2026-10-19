"""SHOBS-P: checks that this Python can run SHOBS-P. Used by setup_python.ps1; it changes nothing.

Prints SHOBSPY=<full path of this python.exe> and exits 0 when this Python will do; otherwise prints one line saying
why not and exits non-zero. With --packages it also checks that the packages in requirements.txt import.
Written without f-strings so that an old Python 2 or 3.5 still reports its version instead of a syntax error.
"""
import sys

PACKAGES = ("numpy", "scipy", "matplotlib", "astropy", "photutils", "ccdproc")


def main():
    version = sys.version.split()[0]
    if sys.version_info < (3, 11):
        print("Python %s is too old (SHOBS-P needs 3.11 or newer)" % version)
        return 3
    try:
        import ssl  # noqa: F401  (pip and the catalog lookups need it)
    except Exception as exc:  # e.g. an Anaconda Python that only works inside its own prompt
        print("Python %s: its SSL module does not load (%s)" % (version, exc))
        return 4
    try:
        import tkinter

        tkinter.Tcl()
    except Exception as exc:
        print("Python %s: Tkinter, the window toolkit, does not work (%s)" % (version, exc))
        return 5
    if "--packages" in sys.argv:
        missing = []
        for name in PACKAGES:
            try:
                __import__(name)
            except Exception as exc:
                missing.append("%s (%s)" % (name, exc))
        if missing:
            print("Packages that do not load: " + "; ".join(missing))
            return 6
    print("SHOBSVER=%s" % version)
    print("SHOBSPY=%s" % sys.executable)
    return 0


if __name__ == "__main__":
    sys.exit(main())
