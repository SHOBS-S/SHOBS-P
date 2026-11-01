#!/bin/bash
# Starts SHOBS-P on a Mac (2.2.19, #33): double-click this file in Finder.
# The first time, macOS may refuse to open a file from a downloaded zip: right-click (or Control-click) it, choose
# Open, then Open again (macOS 15 and newer: System Settings > Privacy & Security > Open Anyway). The first start
# finds a Python with Tk (python.org's 3.13 is best; 2.2.20 (#33): 3.13, 3.12, 3.11 are tried before newer ones),
# gives SHOBS-P its own environment in ~/Library/Application Support/SHOBS-P/python-env and installs the packages
# there, ready-made only (a few minutes); later starts reuse it. Keep this Terminal window open while SHOBS-P runs.
# See shobs_setup.sh.
cd "$(dirname "$0")" || exit 1
bash ./shobs_setup.sh run
code=$?
echo ''
if [ "$code" -ne 0 ]; then
    echo 'SHOBS-P did not start, or stopped with an error. Copy the message above.'
    read -r -p 'Press Return to close this window. ' _
    exit "$code"
fi
echo 'App closed. You can close this window.'
