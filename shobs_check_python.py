"""SHOBS-P: checks that this Python can run SHOBS-P. Used by setup_python.ps1; it changes nothing.

Prints SHOBSPY=<full path of this python.exe> and exits 0 when this Python will do; otherwise prints one line saying
why not and exits non-zero. With --packages it also checks that the packages in requirements.txt import.
"""
import sys

PACKAGES = ("numpy", "scipy", "matplotlib", "astropy", "photutils", "ccdproc")


def main() -> int:
    version = sys.version.split()[0]
    if sys.version_info < (3, 11):
        print(f"Python {version} is too old (SHOBS-P needs 3.11 or newer)")
        return 3
    try:
        import ssl  # noqa: F401  (pip and the catalog lookups need it)
    except Exception as exc:  # e.g. an Anaconda Python started outside its own prompt
        print(f"Python {version}: its SSL module does not load ({exc})")
        return 4
    try:
        import tkinter

        tkinter.Tcl()
    except Exception as exc:
        print(f"Python {version}: Tkinter, the window toolkit, does not work ({exc})")
        return 5
    if "--packages" in sys.argv:
        missing = []
        for name in PACKAGES:
            try:
                __import__(name)
            except Exception as exc:
                missing.append(f"{name} ({exc})")
        if missing:
            print("Packages that do not import: " + "; ".join(missing))
            return 6
    print(f"SHOBSPY={sys.executable}")
    print(f"SHOBSVER={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
