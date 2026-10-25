#!/bin/bash
# Starts SHOBS-P on a Mac (2.2.19, #33): double-click this file in Finder.
# The first time, macOS may refuse to open a file from a downloaded zip: right-click (or Control-click) it, choose
# Open, then Open again. The first start also finds a Python 3.11+ with Tk (python.org's is best), gives SHOBS-P its
# own environment in ~/Library/Application Support/SHOBS-P/python-env and installs the packages there (a few
# minutes); later starts reuse it. Keep this Terminal window open while SHOBS-P runs. See shobs_setup.sh.
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
