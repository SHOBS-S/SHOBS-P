#!/bin/bash
# SHOBS-P self-test on a Mac (2.2.19, #33): double-click this file in Finder (the first time: right-click, Open).
# Checks this copy of SHOBS-P with the same Python "Start SHOBS-P.command" uses (Astropy, photutils, Tk).
# It changes no data. The log goes to selftest_log.txt here and to Downloads/SHOBS-P_selftest_log.txt.
cd "$(dirname "$0")" || exit 1
bash ./shobs_setup.sh selftest
code=$?
echo ''
if [ "$code" -ne 0 ]; then
    echo 'The self-test did not pass or could not run. Copy the message above.'
    echo 'If the test ran, its log is selftest_log.txt in this folder.'
else
    echo 'Done. The log is selftest_log.txt in this folder (a copy is in your Downloads folder).'
fi
read -r -p 'Press Return to close this window. ' _
exit "$code"
