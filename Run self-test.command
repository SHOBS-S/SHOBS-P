#!/bin/bash
# SHOBS-P self-test on a Mac (2.2.19, #33): double-click this file in Finder (the first time: right-click, Open).
# Checks this copy of SHOBS-P with the same Python "Start SHOBS-P.command" uses (Astropy, photutils, Tk).
# It changes no data. The log goes to selftest_log.txt here and to ~/Downloads/SHOBS-P_selftest_log.txt (when that
# folder exists).
cd "$(dirname "$0")" || exit 1
bash ./shobs_setup.sh selftest
code=$?
echo ''
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
read -r -p 'Press Return to close this window. ' _
exit "$code"
