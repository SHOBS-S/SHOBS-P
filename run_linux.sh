#!/usr/bin/env bash
# Starts SHOBS-P on Linux (2.2.19, #23). Run it from a terminal:   ./run_linux.sh
# The first time it finds a Python 3.11+ with Tkinter, gives SHOBS-P its own environment in
# ~/.local/share/SHOBS-P/python-env and installs the packages there (a few minutes); later starts reuse it.
#   ./run_linux.sh --desktop    adds SHOBS-P to your applications menu (and Desktop), with its icon. No sudo needed.
#   ./run_linux.sh --selftest   runs the self-test (same as ./run_selftest.sh).
# See shobs_setup.sh.
here=$(cd "$(dirname "$0")" && pwd -P) || exit 1
case "$1" in
    "") action=run ;;
    --desktop) action=desktop ;;
    --selftest) action=selftest ;;
    *) echo "Usage: ./run_linux.sh [--desktop | --selftest]"; exit 1 ;;
esac
bash "$here/shobs_setup.sh" "$action"
code=$?
echo ''
if [ "$code" -ne 0 ]; then
    case "$action" in
        run) echo 'SHOBS-P did not start, or stopped with an error. Copy the message above.' ;;
        desktop) echo 'The menu icon could not be made. Copy the message above.' ;;
        selftest) echo 'The self-test did not pass or could not run. Copy the message above.'
                  echo 'If the test ran, its log is selftest_log.txt in this folder.' ;;
    esac
    exit "$code"
fi
case "$action" in
    run) echo 'App closed.' ;;
    desktop) echo 'Done. Start SHOBS-P from your applications menu (search for SHOBS-P).'
             echo 'If you move this folder, run ./run_linux.sh --desktop again.' ;;
    selftest)
        if [ -d "$HOME/Downloads" ]; then
            echo 'Done. The log is selftest_log.txt in this folder (a copy is in your Downloads folder).'
        else
            echo 'Done. The log is selftest_log.txt in this folder.'
        fi ;;
esac
