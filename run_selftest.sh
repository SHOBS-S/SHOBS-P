#!/usr/bin/env bash
# SHOBS-P self-test on Linux (2.2.19, #23): checks this copy of SHOBS-P with the same Python run_linux.sh uses
# (Astropy, photutils, Tkinter). It changes no data. The log goes to selftest_log.txt here and to
# ~/Downloads/SHOBS-P_selftest_log.txt (when that folder exists). On a Mac, double-click "Run self-test.command" instead.
here=$(cd "$(dirname "$0")" && pwd -P) || exit 1
exec bash "$here/run_linux.sh" --selftest
