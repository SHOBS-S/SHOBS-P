#!/usr/bin/env bash
# SHOBS-P on Linux and macOS: finds a Python that can run SHOBS-P, sets up SHOBS-P's own private environment, then
# does one job. The Linux and Mac twin of setup_python.ps1 (2.2.19, #23 and #33). Called by:
#   run_linux.sh              (run)       run_linux.sh --desktop  (desktop: menu icon, Linux only)
#   run_selftest.sh           (selftest)
#   "Start SHOBS-P.command"   (run, Mac)  "Run self-test.command" (selftest, Mac)
# Usage: bash shobs_setup.sh run|selftest|desktop
#
# The environment lives in ~/.local/share/SHOBS-P/python-env on Linux (or $XDG_DATA_HOME/SHOBS-P/python-env) and in
# ~/Library/Application Support/SHOBS-P/python-env on a Mac; python.txt next to it records the Python that worked,
# so the next start reuses it. Shared by every SHOBS-P version; delete the SHOBS-P folder there to start afresh.
#
# For testing (like -UsePython / -ProfileName in setup_python.ps1):
#   SHOBS_USE_PYTHON=/path/to/python3  tries only that Python;
#   SHOBS_PROFILE=SHOBS-P-test         keeps the test apart from the real setup (its own folder; no menu icon);
#   SHOBS_TEST_PLATFORM=Darwin|Linux and SHOBS_TEST_ROOT=/some/dir  try the other platform's search on fake folders.
#
# Kept to bash 3.2 (the bash every Mac has) and to commands that work the same on macOS and Linux.

ACTION=${1:-run}
case "$ACTION" in
    run|selftest|desktop) ;;
    *) echo "Usage: bash shobs_setup.sh run|selftest|desktop"; exit 1 ;;
esac

APP_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
CHECK="$APP_DIR/shobs_check_python.py"
REQUIREMENTS="$APP_DIR/requirements.txt"
ROOT=${SHOBS_TEST_ROOT:-}
PROFILE=${SHOBS_PROFILE:-SHOBS-P}
case "${SHOBS_TEST_PLATFORM:-$(uname -s)}" in
    Darwin) MAC=1 ;;
    *) MAC=0 ;;
esac

if [ "$ACTION" = desktop ]; then
    if [ "$MAC" = 1 ]; then
        echo 'The menu icon is for Linux. On a Mac, double-click "Start SHOBS-P.command" (you can drag it to the Dock).'
        exit 6
    fi
    if [ "$PROFILE" != SHOBS-P ]; then
        echo 'The menu icon is only made for the normal setup (without SHOBS_PROFILE), so a test cannot replace it.'
        exit 6
    fi
fi

# A leftover PYTHONHOME/PYTHONPATH from another install stops every Python from starting.
unset PYTHONHOME PYTHONPATH
export PYTHONIOENCODING=utf-8

# SHOBS-P's own place in the user's folder.
if [ "$MAC" = 1 ]; then
    DATA_DIR="$HOME/Library/Application Support/$PROFILE"
else
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$PROFILE"
fi
ENV_DIR="$DATA_DIR/python-env"
ENV_PY="$ENV_DIR/bin/python3"
RECORD="$DATA_DIR/python.txt"
if ! mkdir -p "$DATA_DIR"; then
    echo "SHOBS-P could not make its folder $DATA_DIR"
    exit 5
fi

# Newest first. A Python newer than this list is still found as plain "python3".
VERSIONS="3.15 3.14 3.13 3.12 3.11"
PY_NAMES="python3.15 python3.14 python3.13 python3.12 python3.11 python3 python"

# --- Testing one Python -------------------------------------------------------------------------------------------
# test_python <exe> [packages] [quiet]: runs the checker with that Python. On success sets TP_PATH (its full path)
# and returns 0; otherwise says why (unless quiet) and returns non-zero.
# TK_FAILS / BREW_TK collect the Pythons whose Tkinter is missing, for the advice at the end.
TK_FAILS=""
BREW_TK=""
test_python() {
    local exe="$1" want_packages="$2" quiet="$3" out code line last
    TP_PATH=""
    if [ "$want_packages" = packages ]; then
        out=$("$exe" "$CHECK" --tk86 --packages 2>&1)
    else
        out=$("$exe" "$CHECK" --tk86 2>&1)
    fi
    code=$?
    while IFS= read -r line; do
        case "$line" in
            SHOBSPY=*) TP_PATH=${line#SHOBSPY=} ;;
        esac
        [ -n "$line" ] && last=$line
    done <<EOF
$out
EOF
    if [ "$code" -eq 0 ] && [ -n "$TP_PATH" ] && [ -x "$TP_PATH" ]; then
        return 0
    fi
    TP_PATH=""
    if [ "$code" -eq 5 ]; then
        TK_FAILS="$TK_FAILS
    $exe"
        case "$exe" in
            */homebrew/*|/usr/local/bin/*|/usr/local/opt/*|*/Cellar/*)
                if [ "$MAC" = 1 ] && [ -z "$BREW_TK" ]; then
                    BREW_TK=$(printf '%s\n' "$last" | sed -E -n 's/^Python ([0-9]+\.[0-9]+).*/\1/p')
                fi ;;
        esac
    fi
    if [ "$quiet" != quiet ]; then
        [ -z "$last" ] && last="it did not start (exit code $code)"
        echo "    not usable: $last"
    fi
    return 1
}

# --- Finding a Python ---------------------------------------------------------------------------------------------
# real_path <file>: the file behind any symlinks (macOS has no "readlink -f"), so /bin/python3 and /usr/bin/python3,
# or a python3.13 link and its python.org original, are tried once.
real_path() {
    local f="$1" t n=0 d
    while [ -L "$f" ] && [ "$n" -lt 20 ]; do
        t=$(readlink "$f") || break
        case "$t" in
            /*) f=$t ;;
            *) f="$(dirname "$f")/$t" ;;
        esac
        n=$((n + 1))
    done
    d=$(cd "$(dirname "$f")" 2>/dev/null && pwd -P) || d=$(dirname "$f")
    printf '%s/%s' "$d" "$(basename "$f")"
}

CANDIDATES=()
CANDIDATE_REALS=()
add_candidate() {
    local exe="$1" c real
    [ -n "$exe" ] && [ -x "$exe" ] && [ ! -d "$exe" ] || return 0
    # Apple's /usr/bin/python3 is 3.9 (too old) and, without the developer tools, opens an install dialog.
    if [ "$MAC" = 1 ] && [ "$exe" = /usr/bin/python3 ]; then return 0; fi
    # SHOBS-P's own environment (e.g. when it is activated) is never the base for a new one.
    case "$exe" in "$ENV_DIR"/*) return 0 ;; esac
    real=$(real_path "$exe")
    for c in "${CANDIDATE_REALS[@]}"; do
        [ "$c" = "$real" ] && return 0
    done
    CANDIDATES+=("$exe")
    CANDIDATE_REALS+=("$real")
}

# Every match on the PATH for these names, in this order.
add_path_names() {
    local name dir old_ifs
    for name in "$@"; do
        old_ifs=$IFS
        IFS=:
        set -f  # a PATH entry is a folder name, never a pattern
        for dir in $PATH; do
            IFS=$old_ifs
            [ -n "$dir" ] && add_candidate "$dir/$name"
            IFS=:
        done
        set +f
        IFS=$old_ifs
    done
}

add_conda_pythons() {
    local conda dir line
    [ -n "$CONDA_PREFIX" ] && add_candidate "$CONDA_PREFIX/bin/python3"
    # conda on the PATH: its base folder is two levels up from bin/conda or condabin/conda.
    conda=$(command -v conda 2>/dev/null)
    case "$conda" in
        /*) add_candidate "$(dirname "$(dirname "$conda")")/bin/python3" ;;
    esac
    # Every conda environment (base included) is listed in ~/.conda/environments.txt; only those with Tkinter.
    if [ -f "$HOME/.conda/environments.txt" ]; then
        while IFS= read -r line; do
            [ -n "$line" ] || continue
            for dir in "$line"/lib/python3.*/tkinter; do
                if [ -d "$dir" ]; then add_candidate "$line/bin/python3"; break; fi
            done
        done < "$HOME/.conda/environments.txt"
    fi
    for dir in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/mambaforge" \
               "$ROOT/opt/miniconda3" "$ROOT/opt/anaconda3" "$ROOT/opt/conda" "$ROOT/opt/miniforge3"; do
        add_candidate "$dir/bin/python3"
    done
}

add_pyenv_pythons() {
    local root="${PYENV_ROOT:-$HOME/.pyenv}" name names dir
    [ -d "$root/versions" ] || return 0
    # Newest first (sort by the numbers, not as text, so 3.13 comes before 3.9).
    names=$(for dir in "$root"/versions/3.*; do [ -d "$dir" ] && basename "$dir"; done | sort -t. -k1,1nr -k2,2nr -k3,3nr)
    # shellcheck disable=SC2086  # one name per word (pyenv version names have no spaces)
    for name in $names; do
        add_candidate "$root/versions/$name/bin/python3"
    done
}

find_base_python() {
    local exe v
    BASE=""
    if [ -n "$SHOBS_USE_PYTHON" ]; then
        echo "  Trying only $SHOBS_USE_PYTHON (SHOBS_USE_PYTHON)..."
        if test_python "$SHOBS_USE_PYTHON"; then
            echo "    usable: $TP_PATH"
            BASE=$TP_PATH
            return 0
        fi
        return 1
    fi
    if [ "$MAC" = 1 ]; then
        # 1. python.org (it brings its own Tk 8.6), 2. Homebrew (needs python-tk), 3. conda, 4. pyenv, 5. PATH.
        for v in $VERSIONS; do
            add_candidate "$ROOT/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
            add_candidate "$HOME/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
        done
        for v in $VERSIONS; do
            add_candidate "$ROOT/opt/homebrew/bin/python$v"
            add_candidate "$ROOT/usr/local/bin/python$v"
        done
        add_candidate "$ROOT/opt/homebrew/bin/python3"
        add_candidate "$ROOT/usr/local/bin/python3"
        add_conda_pythons
        add_pyenv_pythons
        # shellcheck disable=SC2086
        add_path_names $PY_NAMES
    else
        # 1. the PATH (newest version name first; the distribution's Python is usually here), 2. conda, 3. pyenv.
        # shellcheck disable=SC2086
        add_path_names $PY_NAMES
        add_conda_pythons
        add_pyenv_pythons
    fi
    for exe in "${CANDIDATES[@]}"; do
        echo "  Trying $exe..."
        test_python "$exe" || continue
        case "$TP_PATH" in
            "$ENV_DIR"/*) echo "    skipped (SHOBS-P's own environment)"; continue ;;
        esac
        echo "    usable: $TP_PATH"
        BASE=$TP_PATH
        return 0
    done
    return 1
}

tk_advice() {
    local id=""
    if [ "$MAC" = 1 ]; then
        if [ -n "$BREW_TK" ]; then
            echo "A Homebrew Python was found without Tkinter. To use it: brew install python-tk@$BREW_TK"
            echo 'Or, simpler:'
        fi
        return 0
    fi
    [ -n "$TK_FAILS" ] || return 0
    echo 'These Pythons are missing Tkinter, the window toolkit:'"$TK_FAILS"
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        id=$( (. /etc/os-release; echo "$ID $ID_LIKE") 2>/dev/null)
    fi
    echo 'Install it with your system'"'"'s package manager, then run this again:'
    case " $id " in
        *" debian "*|*" ubuntu "*) echo '    sudo apt install python3-tk     (for a python3.X from elsewhere: python3.X-tk)' ;;
        *" fedora "*|*" rhel "*|*" centos "*) echo '    sudo dnf install python3-tkinter' ;;
        *" arch "*) echo '    sudo pacman -S tk' ;;
        *" suse "*|*" opensuse "*) echo '    sudo zypper install python3-tk' ;;
        *)
            echo '    Debian, Ubuntu, Mint:   sudo apt install python3-tk'
            echo '    Fedora, RHEL:           sudo dnf install python3-tkinter'
            echo '    Arch, Manjaro:          sudo pacman -S tk'
            echo '    openSUSE:               sudo zypper install python3-tk' ;;
    esac
}

stop_no_python() {
    echo ''
    echo 'SHOBS-P could not find a Python it can use (each one tried is listed above, with the reason).'
    echo 'It needs Python 3.11 or newer with Tkinter (the window toolkit).'
    tk_advice
    if [ "$MAC" = 1 ]; then
        echo 'Install Python from https://www.python.org/downloads/macos/ (it includes Tk), then double-click again.'
    elif [ -z "$TK_FAILS" ]; then
        echo 'Install Python 3.11 or newer with your package manager (or from https://www.python.org/downloads/),'
        echo 'with its Tkinter package, then run this again.'
    fi
    exit 2
}

remove_env_dir() {
    # Only ever SHOBS-P's own folder.
    case "$ENV_DIR" in
        */"$PROFILE"/python-env) ;;
        *) return 1 ;;
    esac
    [ -e "$ENV_DIR" ] || return 0
    rm -rf "$ENV_DIR" 2>/dev/null
    [ ! -e "$ENV_DIR" ]
}

# --- 0. One setup at a time: a second double-click during the first install must not remove the environment -----
LOCK="$DATA_DIR/setup.lock"
release_lock() { rmdir "$LOCK" 2>/dev/null; }
if ! mkdir "$LOCK" 2>/dev/null; then
    # A lock older than 60 minutes is left over from a setup that was cut off (window closed, computer restarted).
    if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
        rmdir "$LOCK" 2>/dev/null
    fi
    if ! mkdir "$LOCK" 2>/dev/null; then
        echo 'SHOBS-P is being set up in another window (or was, less than an hour ago).'
        echo 'Wait for that window to finish, then start SHOBS-P again.'
        echo "(If no other window is open, delete the folder $LOCK and try again.)"
        exit 7
    fi
fi
trap release_lock EXIT

# A fingerprint of what the environment must hold (requirements.txt, + certifi on a Mac), kept as python.txt's
# second line: when it matches and the packages load, a start skips pip altogether.
fingerprint() {
    "$1" -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read() + sys.argv[2].encode()).hexdigest())" \
        "$REQUIREMENTS" "mac=$MAC" 2>/dev/null
}

# --- 1. The Python SHOBS-P used last time, if it still works ------------------------------------------------------
PY=""
LAST_PRINT=""
if [ -z "$SHOBS_USE_PYTHON" ] && [ -f "$RECORD" ]; then
    last=$(head -n 1 "$RECORD")
    LAST_PRINT=$(sed -n 2p "$RECORD")
    if [ -n "$last" ] && [ -x "$last" ] && test_python "$last" "" quiet; then
        PY=$TP_PATH
    elif [ -n "$last" ]; then
        echo "The Python SHOBS-P used before ($last) no longer works; looking for another one."
    fi
fi
IS_NEW=0
[ -z "$PY" ] && IS_NEW=1
PIP_USER=0

# --- 2. Otherwise find one, and give SHOBS-P its own private environment made from it --------------------------------
if [ -z "$PY" ]; then
    echo 'Looking for a Python that can run SHOBS-P...'
    find_base_python || stop_no_python
    echo ''
    echo "Setting up SHOBS-P's own Python environment in $ENV_DIR"
    echo '(made from that Python, which itself is left untouched). This takes a moment...'
    if ! remove_env_dir; then
        echo "The old SHOBS-P environment in $ENV_DIR could not be removed"
        echo '(a file in it may be in use or not yours). Close any SHOBS-P window, then try again;'
        echo 'if that does not help, delete that folder yourself.'
        exit 5
    fi
    venv_out=$("$BASE" -m venv "$ENV_DIR" 2>&1)
    venv_code=$?
    [ -n "$venv_out" ] && printf '%s\n' "$venv_out"
    if [ "$venv_code" -eq 0 ] && [ -x "$ENV_PY" ] && test_python "$ENV_PY"; then
        PY=$TP_PATH
    fi
    if [ -z "$PY" ]; then
        remove_env_dir
        # A Python whose packages belong to the system (Debian, Ubuntu, Fedora...) must not get pip's packages.
        if "$BASE" -c "import os, sys, sysconfig; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_path('stdlib'), 'EXTERNALLY-MANAGED')) else 1)" 2>/dev/null; then
            echo ''
            echo "SHOBS-P could not make its own environment with $BASE"
            echo "and that Python's packages are managed by the system, so SHOBS-P will not install into it."
            case "$venv_out" in
                *ensurepip*|*python3*-venv*)
                    echo 'Install the venv package, then run this again:'
                    echo '    Debian, Ubuntu, Mint:   sudo apt install python3-venv     (for python3.X: python3.X-venv)' ;;
            esac
            exit 5
        fi
        PY=$BASE
        # Anaconda's own environment is managed by conda: keep pip's packages in the user's folder instead.
        prefix=$(dirname "$(dirname "$BASE")")
        [ -d "$prefix/conda-meta" ] && PIP_USER=1
        echo '  A separate environment does not work with this Python, so SHOBS-P will use it directly:'
        echo "  $BASE"
        if [ "$PIP_USER" = 1 ]; then
            echo "  (its packages go to your user folder, not into Anaconda's own environment)"
        else
            echo '  (its packages are installed into that Python with pip)'
        fi
    fi
fi
echo "Using Python: $PY"
# Reusing an Anaconda Python directly (no environment of its own): its pip packages still go to the user's folder.
case "$PY" in
    "$ENV_DIR"/*) ;;
    *) [ -d "$(dirname "$(dirname "$PY")")/conda-meta" ] && PIP_USER=1 ;;
esac

# --- 3. The packages (skipped when they load and requirements.txt has not changed since the last setup) -----------
PRINT=$(fingerprint "$PY")
if [ "$IS_NEW" = 1 ] || [ -z "$PRINT" ] || [ "$PRINT" != "$LAST_PRINT" ] || ! test_python "$PY" packages quiet; then
    if ! "$PY" -m pip --version >/dev/null 2>&1; then
        echo ''
        echo "This Python has no pip, so SHOBS-P cannot install its packages: $PY"
        echo 'Its pip / venv package is missing. Debian, Ubuntu, Mint: sudo apt install python3-venv'
        echo '(for a python3.X: python3.X-venv). Or install Python from https://www.python.org/downloads/'
        echo 'and run this again.'
        exit 3
    fi
    set -- -m pip install --disable-pip-version-check
    if [ "$IS_NEW" = 1 ]; then
        echo 'Installing the Python packages SHOBS-P needs (this downloads them: a few minutes)...'
    else
        echo 'Updating the Python packages SHOBS-P needs...'
        set -- "$@" --quiet
    fi
    [ "$PIP_USER" = 1 ] && set -- "$@" --user
    set -- "$@" -r "$REQUIREMENTS"
    # A python.org Mac Python has no certificates of its own until "Install Certificates.command" is run; certifi
    # lets SHOBS-P's catalog lookups work without that step (used below).
    [ "$MAC" = 1 ] && set -- "$@" certifi
    if ! "$PY" "$@"; then
        echo 'Installing the packages failed (see the lines above). Check the internet connection and try again.'
        exit 3
    fi
    if ! test_python "$PY" packages; then
        echo 'The packages were installed but do not all load (see the line above).'
        exit 3
    fi
fi
printf '%s\n%s\n' "$PY" "$PRINT" > "$RECORD"

# 2.2.19 (#33): no certificates for this Python's SSL (python.org Mac) -> point it at certifi's, if certifi is there.
if [ -z "$SSL_CERT_FILE" ]; then
    cert=$("$PY" -c "
import os, ssl
p = ssl.get_default_verify_paths()
ok = (p.cafile and os.path.isfile(p.cafile)) or (p.capath and os.path.isdir(p.capath) and os.listdir(p.capath))
if not ok:
    import certifi
    print(certifi.where())" 2>/dev/null)
    if [ -n "$cert" ] && [ -f "$cert" ]; then
        export SSL_CERT_FILE="$cert"
    fi
fi

# Files from a downloaded zip carry the Mac's quarantine mark; once one launcher was allowed, clear it from SHOBS-P's
# own launchers only (named files, never the folder: it may be Downloads), and only at the first setup.
if [ "$MAC" = 1 ] && [ "$IS_NEW" = 1 ] && [ -z "$SHOBS_TEST_PLATFORM" ] && command -v xattr >/dev/null 2>&1; then
    for f in "$APP_DIR/Start SHOBS-P.command" "$APP_DIR/Run self-test.command" "$APP_DIR/shobs_setup.sh" \
             "$APP_DIR/run_linux.sh" "$APP_DIR/run_selftest.sh"; do
        [ -f "$f" ] && xattr -d com.apple.quarantine "$f" 2>/dev/null
    done
fi
release_lock
trap - EXIT

# --- 4. The job ---------------------------------------------------------------------------------------------------
cd "$APP_DIR" || exit 1

# Quotes one argument for the Exec line of a .desktop file (freedesktop.org Desktop Entry spec).
desktop_quote() {
    local s="$1"
    # shellcheck disable=SC2016  # the $ in the sed pattern is meant literally
    s=$(printf '%s' "$s" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/`/\\`/g' -e 's/\$/\\$/g' -e 's/%/%%/g')
    # The file's own escaping comes on top of the Exec quoting: each backslash is written twice.
    s=$(printf '%s' "$s" | sed -e 's/\\/\\\\/g')
    printf '"%s"' "$s"
}

# Tk draws through X: on Wayland that needs XWayland (which sets DISPLAY).
no_screen_hint() {
    [ "$MAC" = 0 ] && [ -z "$DISPLAY" ] || return 0
    if [ -n "$WAYLAND_DISPLAY" ]; then
        echo 'This is a Wayland session without XWayland (DISPLAY is not set), and Tk needs X to draw its windows.'
        echo 'Install XWayland (package xwayland), or log in with an "on Xorg" session, then try again.'
    else
        echo 'No screen found (DISPLAY is not set): start SHOBS-P from your desktop session.'
    fi
}

case "$ACTION" in
    run)
        no_screen_hint
        "$PY" "$APP_DIR/clearv_app.py"
        exit $?
        ;;
    selftest)
        echo 'Running the SHOBS-P self-test. It takes a minute or two (the transit fits are the slow part)...'
        if [ "$MAC" = 0 ] && [ -z "$DISPLAY" ]; then
            if command -v xvfb-run >/dev/null 2>&1; then
                echo '(no screen found, so the window test runs on a hidden screen: xvfb-run)'
                xvfb-run -a "$PY" "$APP_DIR/selftest.py"
                exit $?
            fi
            no_screen_hint
        fi
        "$PY" "$APP_DIR/selftest.py"
        exit $?
        ;;
    desktop)
        apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
        file="$apps/shobs-p.desktop"
        mkdir -p "$apps" || exit 4
        {
            echo '[Desktop Entry]'
            echo 'Type=Application'
            echo 'Version=1.0'
            echo 'Name=SHOBS-P'
            echo 'GenericName=Photometry'
            echo 'Comment=Shiloh Hill Observatory - Photometry (SHOBS-P)'
            # The menu entry starts Python directly, without the SSL_CERT_FILE set above: fine on Linux, whose
            # Pythons use the system's certificates (the certifi fallback is for python.org Macs).
            echo "Exec=$(desktop_quote "$PY") $(desktop_quote "$APP_DIR/clearv_app.py")"
            echo "Path=$(printf '%s' "$APP_DIR" | sed -e 's/\\/\\\\/g')"
            echo "Icon=$(printf '%s' "$APP_DIR/shobs_p.png" | sed -e 's/\\/\\\\/g')"
            echo 'Terminal=false'
            echo 'Categories=Science;Astronomy;Education;'
            echo 'StartupWMClass=clearv_app'
        } > "$file" || { echo "The menu entry $file could not be written."; exit 4; }
        chmod 755 "$file"
        echo "Created $file"
        command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1
        # A copy on the Desktop too, when there is one.
        desk=""
        command -v xdg-user-dir >/dev/null 2>&1 && desk=$(xdg-user-dir DESKTOP 2>/dev/null)
        [ -z "$desk" ] && desk="$HOME/Desktop"
        if [ -d "$desk" ] && [ "$desk" != "$HOME" ]; then
            if cp "$file" "$desk/shobs-p.desktop" && chmod 755 "$desk/shobs-p.desktop"; then
                command -v gio >/dev/null 2>&1 && gio set "$desk/shobs-p.desktop" metadata::trusted true >/dev/null 2>&1
                echo "Created $desk/shobs-p.desktop"
            fi
        fi
        exit 0
        ;;
esac
