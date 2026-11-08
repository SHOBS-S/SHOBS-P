#!/bin/bash
# Makes a SHOBS-P app on a Mac (2.3.2, item 15): the twin of "Create shortcuts.bat" on Windows. Double-click this
# file in Finder (the first time macOS may refuse it: right-click, Open, Open; on macOS 15 and newer System Settings >
# Privacy & Security > Open Anyway). It builds SHOBS-P.app, with the SHOBS-P logo, in
# /Applications when you may write there, else in Applications in your home folder, and puts SHOBS-P on your
# Desktop (drag it to the Dock to keep it there). The app starts SHOBS-P from this folder: run this again if you
# move or rename the folder, or after unzipping a new version somewhere else.
#
# The app opens "Start SHOBS-P.command" in Terminal every time (macOS lets Terminal, not this homemade app, read
# folders in Downloads, Desktop and Documents). The first start (no SHOBS-P setup yet) and the first start of each
# new version or folder leave the Terminal window up, so the setup's progress shows; other starts put it in the Dock
# once SHOBS-P is ready, and it comes back up with the message if SHOBS-P stops with an error.
#
# For testing: SHOBS_APP_DEST=/some/dir builds the app there (no Desktop link unless SHOBS_DESKTOP_DIR is also set).
# Kept to bash 3.2 and to tools every Mac has (sips and iconutil make the icon; without them the app has none).

cd "$(dirname "$0")" || exit 1
APP_DIR=$(pwd -P) || exit 1

finish() {
    echo ''
    if [ "$1" -ne 0 ]; then
        echo 'The SHOBS-P app was not made. Copy the message above.'
    fi
    if [ -z "${SHOBS_APP_DEST:-}" ]; then
        read -r -p 'Press Return to close this window. ' _
    fi
    exit "$1"
}

if [ ! -f "$APP_DIR/shobs_setup.sh" ] || [ ! -f "$APP_DIR/clearv_app.py" ]; then
    echo 'This file must stay in the SHOBS-P folder (beside "Start SHOBS-P.command").'
    finish 1
fi
case "$APP_DIR" in
    *"'"*|*'"'*|*\\*)
        echo "The SHOBS-P folder's path has a ' or \" or \\ in it, which the app cannot use. Rename or move the folder and run this again."
        finish 1 ;;
esac

# The file you just allowed is this one; SHOBS-P's other start files from the same zip still carry the Mac's
# quarantine mark, and the app opens "Start SHOBS-P.command" at its first start (review M1). Named files only.
if command -v xattr >/dev/null 2>&1; then
    for f in "Start SHOBS-P.command" "Run self-test.command" "Create shortcut.command" shobs_setup.sh run_linux.sh \
             run_selftest.sh; do
        [ -f "$APP_DIR/$f" ] && xattr -d com.apple.quarantine "$APP_DIR/$f" 2>/dev/null
    done
fi

VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"$/\1/p' "$APP_DIR/clearv_app.py" | head -n 1)
[ -n "$VERSION" ] || VERSION=0

# Where the app goes: /Applications if it may be written, else ~/Applications.
if [ -n "${SHOBS_APP_DEST:-}" ]; then
    DEST=$SHOBS_APP_DEST
elif [ -w /Applications ]; then
    DEST=/Applications
else
    DEST="$HOME/Applications"
fi
if ! mkdir -p "$DEST"; then
    echo "Could not make the folder $DEST"
    finish 2
fi
APP="$DEST/SHOBS-P.app"
if [ -e "$APP" ] && [ ! -f "$APP/Contents/MacOS/SHOBS-P" ]; then
    echo "$APP exists and is not a SHOBS-P app made by this file: it was left alone."
    finish 3
fi
# A new copy each time (the old one may point at a folder that has moved). Its files are written over, never
# deleted, so this works where removing files is not allowed.
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" || finish 2

# The icon: shobs_p.png in every size macOS asks for, then iconutil makes the .icns.
ICON_KEY=''
if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1 && [ -f "$APP_DIR/shobs_p.png" ]; then
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/shobs_icon.XXXXXX") || WORK=''
    if [ -n "$WORK" ]; then
        SET="$WORK/shobs_p.iconset"
        mkdir -p "$SET"
        ok=1
        for size in 16 32 128 256 512; do
            double=$((size * 2))
            sips -z "$size" "$size" "$APP_DIR/shobs_p.png" --out "$SET/icon_${size}x${size}.png" >/dev/null 2>&1 || ok=0
            sips -z "$double" "$double" "$APP_DIR/shobs_p.png" --out "$SET/icon_${size}x${size}@2x.png" \
                >/dev/null 2>&1 || ok=0
        done
        if [ "$ok" = 1 ] && iconutil -c icns "$SET" -o "$APP/Contents/Resources/shobs_p.icns" >/dev/null 2>&1; then
            ICON_KEY='    <key>CFBundleIconFile</key>
    <string>shobs_p</string>'
        else
            echo 'Note: the icon could not be made; the app works, with the plain app icon.'
        fi
        rm -rf "$WORK"
    fi
else
    echo 'Note: sips or iconutil is missing, so the app gets the plain app icon.'
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>SHOBS-P</string>
    <key>CFBundleDisplayName</key>
    <string>SHOBS-P</string>
    <key>CFBundleIdentifier</key>
    <string>org.shilohhillobservatory.shobs-p</string>
    <key>CFBundleExecutable</key>
    <string>SHOBS-P</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
$ICON_KEY
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# The app's own program: starts SHOBS-P from this folder. (LSUIElement: the launcher has no Dock icon of its own;
# SHOBS-P's window has one while it runs.)
cat > "$APP/Contents/MacOS/SHOBS-P" <<LAUNCH
#!/bin/bash
# Made by "Create shortcut.command" (SHOBS-P $VERSION). Starts SHOBS-P from the folder below.
APP_DIR='$APP_DIR'
# An app started from Finder gets a short PATH: add where python.org and Homebrew put Python.
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH"
SUPPORT="\$HOME/Library/Application Support/SHOBS-P"
alert() {
    /usr/bin/osascript -e "display alert \"SHOBS-P\" message \"\$1\"" >/dev/null 2>&1
}
if [ ! -f "\$APP_DIR/shobs_setup.sh" ]; then
    alert "SHOBS-P is no longer in \$APP_DIR. Open the SHOBS-P folder where it is now and double-click Create shortcut.command again."
    exit 1
fi
# The Python the setup recorded (its own environment, or a Python used directly).
PY=\$(head -n 1 "\$SUPPORT/python.txt" 2>/dev/null)
SEEN="\$APP_DIR|$VERSION"
if [ -z "\$PY" ] || [ ! -x "\$PY" ] || [ "\$(cat "\$SUPPORT/app_seen.txt" 2>/dev/null)" != "\$SEEN" ]; then
    # First start, or the first of this version or folder: the setup may install packages for a few minutes;
    # Terminal shows its progress.
    mkdir -p "\$SUPPORT" && printf '%s\n' "\$SEEN" > "\$SUPPORT/app_seen.txt"
    rm -f "\$SUPPORT/app_minimize"   # this start keeps its Terminal window up
    /usr/bin/open -a Terminal "\$APP_DIR/Start SHOBS-P.command"
    exit 0
fi
# 2.3.2 (run-through): every start goes through Terminal. macOS lets this unsigned app read nothing in Downloads,
# Desktop or Documents (bash: "Operation not permitted", and no question asked), while Terminal may; so the app only
# asks Terminal to run "Start SHOBS-P.command", and leaves a note that the window may go to the Dock once SHOBS-P
# is ready (shobs_setup.sh: it comes back up if SHOBS-P stops with an error).
date +%s > "\$SUPPORT/app_minimize" 2>/dev/null
/usr/bin/open -a Terminal "\$APP_DIR/Start SHOBS-P.command" || {
    alert "SHOBS-P could not open Terminal. Double-click Start SHOBS-P.command in \$APP_DIR instead."
    exit 1
}
exit 0
LAUNCH
chmod 755 "$APP/Contents/MacOS/SHOBS-P" || finish 2
# Tell Finder the app changed (a new icon shows at once).
touch "$APP" 2>/dev/null
command -v xattr >/dev/null 2>&1 && xattr -dr com.apple.quarantine "$APP" 2>/dev/null

# The Desktop: a link to the app (no Finder permission question, as an alias made by a script would ask).
DESK=${SHOBS_DESKTOP_DIR:-}
if [ -z "$DESK" ] && [ -z "${SHOBS_APP_DEST:-}" ]; then
    DESK="$HOME/Desktop"
fi
if [ -n "$DESK" ] && [ -d "$DESK" ]; then
    LINK="$DESK/SHOBS-P"
    if [ -L "$LINK" ] || [ ! -e "$LINK" ]; then
        ln -sfn "$APP" "$LINK" && echo "On your Desktop: SHOBS-P (drag it to the Dock to keep it there)."
    else
        echo "$LINK exists and is not a link to the app: it was left alone."
    fi
fi

echo "Made $APP (SHOBS-P $VERSION), starting SHOBS-P from:"
echo "  $APP_DIR"
echo 'Open it from Launchpad, Applications or the Desktop. Run this again if you move the SHOBS-P folder.'
finish 0
