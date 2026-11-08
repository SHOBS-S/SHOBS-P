#!/bin/bash
# Makes a SHOBS-P app on a Mac (2.3.2, item 15): the twin of "Create shortcuts.bat" on Windows. Double-click this
# file in Finder (the first time: right-click, Open, Open). It builds SHOBS-P.app, with the SHOBS-P logo, in
# /Applications when you may write there, else in Applications in your home folder, and puts SHOBS-P on your
# Desktop (drag it to the Dock to keep it there). The app starts SHOBS-P from this folder: run this again if you
# move or rename the folder, or after unzipping a new version somewhere else.
#
# The app: the first start (no SHOBS-P environment yet) opens "Start SHOBS-P.command" in Terminal, so the setup's
# progress shows. Later starts run without a Terminal window; their messages go to app_start_log.txt in
# ~/Library/Application Support/SHOBS-P, and if SHOBS-P stops with an error that log opens in TextEdit.
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
    *"'"*) echo "The SHOBS-P folder's path has a ' in it, which the app cannot use. Rename or move the folder and run this again."
           finish 1 ;;
esac

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
if [ ! -x "\$SUPPORT/python-env/bin/python3" ] || [ ! -f "\$SUPPORT/python.txt" ]; then
    # First start: the setup takes a few minutes; Terminal shows its progress.
    /usr/bin/open -a Terminal "\$APP_DIR/Start SHOBS-P.command"
    exit 0
fi
mkdir -p "\$SUPPORT"
LOG="\$SUPPORT/app_start_log.txt"
cd "\$APP_DIR" || exit 1
/bin/bash ./shobs_setup.sh run >"\$LOG" 2>&1
code=\$?
if [ "\$code" -ne 0 ]; then
    alert "SHOBS-P did not start, or stopped with an error. Its messages open in TextEdit (app_start_log.txt)."
    /usr/bin/open -e "\$LOG"
fi
exit "\$code"
LAUNCH
chmod 755 "$APP/Contents/MacOS/SHOBS-P" || finish 2
# Tell Finder the app changed (a new icon shows at once).
touch "$APP" 2>/dev/null

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
