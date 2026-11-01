"""2.2.20 (#33): Linux and Mac launchers (shobs_setup.sh) and the release zip.

Logic tests only: no packages are downloaded. The full first-start install was tested by hand on Linux (fresh HOME,
python 3.15 + 3.13 + 3.11) and with the Mac branch simulated (SHOBS_TEST_PLATFORM / a fake uname).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(HERE, "shobs_setup.sh")
SCRIPTS = ["shobs_setup.sh", "run_linux.sh", "run_selftest.sh", "Start SHOBS-P.command", "Run self-test.command"]
BASH = shutil.which("bash")


def setup_function(name):
    """The text of one shell function from shobs_setup.sh (from 'name() {' to the closing '}' at the line start)."""
    with open(SETUP, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), text, re.S | re.M)
    if not m:
        raise AssertionError("function %s not found in shobs_setup.sh" % name)
    return m.group(0)


@unittest.skipUnless(BASH, "needs bash")
class LauncherTests(unittest.TestCase):
    def test_scripts_parse(self):
        for name in SCRIPTS:
            r = subprocess.run([BASH, "-n", os.path.join(HERE, name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (name, r.stderr))

    def test_scripts_have_unix_line_endings(self):
        for name in SCRIPTS:
            with open(os.path.join(HERE, name), "rb") as fh:
                self.assertNotIn(b"\r\n", fh.read(), name)

    def test_python_order_313_312_311_then_newer(self):
        found = ["3.15.2", "3.11.9", "3.14.0", "3.13.1", "3.12.7", "3.16.0a1"]
        script = setup_function("python_rank") + "".join(
            'echo "$(python_rank %s) %s"\n' % (v, v) for v in found)
        out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, check=True).stdout
        ranked = [line.split()[1] for line in sorted(out.splitlines(), key=lambda s: int(s.split()[0]))]
        self.assertEqual(ranked, ["3.13.1", "3.12.7", "3.11.9", "3.14.0", "3.15.2", "3.16.0a1"])

    def test_only_ready_made_packages(self):
        with open(SETUP, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("--only-binary=:all:", text)
        # The old message that blamed the internet for every pip failure is gone.
        self.assertNotIn("failed (see the lines above). Check the internet connection", text)

    def test_no_python_message_on_a_mac(self):
        home = tempfile.mkdtemp()
        try:
            env = dict(os.environ, HOME=home, SHOBS_TEST_PLATFORM="Darwin",
                       SHOBS_USE_PYTHON=os.path.join(home, "no-such-python3"))
            r = subprocess.run([BASH, SETUP, "selftest"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("✖ SHOBS-P could not find a Python it can use", r.stdout)
            self.assertIn("Install Python 3.13 from https://www.python.org/downloads/macos/", r.stdout)
            self.assertIn("double-click again", r.stdout)
            # The Mac place, and no lock left behind.
            data = os.path.join(home, "Library", "Application Support", "SHOBS-P")
            self.assertTrue(os.path.isdir(data))
            self.assertFalse(os.path.exists(os.path.join(data, "setup.lock")))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_usable_python_with_tk_is_found(self):
        """With SHOBS_USE_PYTHON set to this (Tk) Python, the search reports it usable by version."""
        try:
            import tkinter  # noqa: F401
        except Exception:
            self.skipTest("this Python has no Tkinter")
        script = (
            'MAC=0; ENV_DIR=/nonexistent/SHOBS-P/python-env; CHECK="$1"; SHOBS_USE_PYTHON="$2"\n'
            + setup_function("test_python") + setup_function("find_usable_pythons")
            + 'find_usable_pythons && echo "FIRST=${USABLE[0]} VER=${USABLE_VER[0]}"\n')
        r = subprocess.run([BASH, "-c", script, "x", os.path.join(HERE, "shobs_check_python.py"), sys.executable],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("usable: Python %d.%d" % sys.version_info[:2], r.stdout)
        self.assertIn("VER=%d.%d" % sys.version_info[:2], r.stdout)


@unittest.skipUnless(shutil.which("git") and os.path.exists(os.path.join(HERE, ".git")), "needs a git checkout")
class ReleaseZipTests(unittest.TestCase):
    def test_zip_keeps_launchers_executable_and_leaves_out_tests(self):
        out = os.path.join(tempfile.mkdtemp(), "r.zip")
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "tools", "make_release_zip.py"), out, "--src", HERE],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 1 and "is not tracked by git" in r.stdout:
                self.skipTest("an app file is not committed yet: " + r.stdout.strip().splitlines()[-1])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with zipfile.ZipFile(out) as zf:
                modes = {i.filename.split("/", 1)[1]: (i.external_attr >> 16) & 0o777 for i in zf.infolist()}
            for name in SCRIPTS:
                self.assertEqual(modes.get(name), 0o755, name)
            self.assertEqual(modes.get("clearv_app.py"), 0o644)
            self.assertFalse([n for n in modes if n.startswith(("tests/", "tools/", "docs/"))])
        finally:
            shutil.rmtree(os.path.dirname(out), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
