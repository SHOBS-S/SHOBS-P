#!/usr/bin/env bash
# Starts SHOBS-P on Linux (2.2.19, #23). Run it from a terminal:   ./run_linux.sh
# The first time it finds a Python 3.11+ with Tkinter (2.2.20 (#33): 3.13, 3.12, 3.11 first, then newer, until the
# packages install ready-made), gives SHOBS-P its own environment in
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
if [ "$action" = selftest ]; then
    cd "$here" || exit "$code"
    # 2.3.5 (#83): say how it went (selftest.py exits 0 passed, 10 checks failed, 11 crashed; 1-7 = setup failed).
    log_where='selftest_log.txt in this folder'
    [ -d "$HOME/Downloads" ] && log_where="$log_where (a copy is in your Downloads folder)"
    case "$code" in
        0) echo "✔ Self-test passed. The log is $log_where." ;;
        10) n=$(grep -m1 '^RESULT:' selftest_log.txt 2>/dev/null | grep -oE '[0-9]+' | head -n 1)
            echo "✖ Self-test: ${n:-some} check$([ "$n" = 1 ] && echo '' || echo 's') failed (the RESULT line above names them)."
            echo "  Send $log_where." ;;
        11) echo '✖ The self-test itself crashed (see CRASHED above).'
            echo "  Send $log_where." ;;
        8) ;;   # no screen: the ✖ line above says so
        *) echo '✖ The self-test could not run (setup failed, see the lines above).' ;;
    esac
    exit "$code"
fi
if [ "$code" -ne 0 ]; then
    [ "$code" = 8 ] && exit "$code"   # 2.3.5: no screen: the ✖ hint above is the last thing on the screen
    case "$action" in
        run) echo 'SHOBS-P did not start, or stopped with an error. Copy the message above.' ;;
        desktop) echo 'The menu icon could not be made. Copy the message above.' ;;
    esac
    exit "$code"
fi
case "$action" in
    run) echo 'App closed.' ;;
    desktop) echo 'Done. Start SHOBS-P from your applications menu (search for SHOBS-P).'
             echo 'If you move this folder, run ./run_linux.sh --desktop again.' ;;
esac
