#!/bin/sh
# Build the .deb without debhelper.
#
# This project's standard route is `dpkg-buildpackage -us -uc -b`, which uses
# debian/rules and debhelper. This script produces a package with the same
# contents for machines that do not have debhelper and dh-python installed —
# useful when installing build packages needs root access you do not yet have.
#
# Usage: tools/build-deb.sh [output directory]

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUTDIR=${1:-$ROOT}
PKG=aqualizer
VERSION=$(sed -n '1s/.*(\(.*\)).*/\1/p' "$ROOT/debian/changelog")
ARCH=all
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

echo "Building $PKG $VERSION"

install -d "$STAGE/DEBIAN"
install -d "$STAGE/usr/bin"
install -d "$STAGE/usr/lib/python3/dist-packages/$PKG"
install -d "$STAGE/usr/lib/systemd/user"
install -d "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
install -d "$STAGE/usr/share/metainfo"
install -d "$STAGE/usr/share/man/man1"
install -d "$STAGE/usr/share/doc/$PKG"

install -m 644 "$ROOT/src/$PKG"/*.py "$STAGE/usr/lib/python3/dist-packages/$PKG/"

cat > "$STAGE/usr/bin/$PKG" <<'LAUNCHER'
#!/usr/bin/python3
from aqualizer.cli import main

raise SystemExit(main())
LAUNCHER
chmod 755 "$STAGE/usr/bin/$PKG"

APP_ID=io.github.sholehbaktiabadi.Aqualizer
install -m 644 "$ROOT/data/$APP_ID.desktop"      "$STAGE/usr/share/applications/"
install -m 644 "$ROOT/data/$APP_ID.svg"          "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "$ROOT/data/$APP_ID.metainfo.xml" "$STAGE/usr/share/metainfo/"
install -m 644 "$ROOT/data/aqualizer.service"    "$STAGE/usr/lib/systemd/user/"
gzip -9nc "$ROOT/data/aqualizer.1" > "$STAGE/usr/share/man/man1/$PKG.1.gz"
chmod 644 "$STAGE/usr/share/man/man1/$PKG.1.gz"

install -m 644 "$ROOT/debian/copyright" "$STAGE/usr/share/doc/$PKG/copyright"
gzip -9nc "$ROOT/debian/changelog" > "$STAGE/usr/share/doc/$PKG/changelog.Debian.gz"
chmod 644 "$STAGE/usr/share/doc/$PKG/changelog.Debian.gz"
install -m 644 "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG/README.md"

SIZE=$(du -ks "$STAGE" | cut -f1)

# The Depends field mirrors the binary stanza in debian/control, with the
# ${python3:Depends} that dh-python would normally expand replaced by an explicit
# python3 dependency.
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, pipewire (>= 1.0), pipewire-bin, wireplumber, init-system-helpers (>= 1.52)
Recommends: pipewire-pulse
Installed-Size: $SIZE
Maintainer: sholehbaktiabadi <sholehbaktiabadi@gmail.com>
Homepage: https://github.com/sholehbaktiabadi/aqualizer
Description: audio output equalizer for PipeWire
 Aqualizer inserts a ten-band equalizer into PipeWire's audio path, with
 ready-made presets such as Bass, Vocal, Acoustic and Night. Every band can also
 be set by hand and saved as a preset of your own.
 .
 The filter follows whichever device is in use, including when moving between
 speakers and a Bluetooth headset. Switching presets takes effect instantly
 without interrupting music that is already playing, because gain values are sent
 to the running graph rather than reloading the chain.
 .
 Signal processing uses PipeWire's built-in biquad filters, so no extra DSP
 library such as LADSPA or LV2 is needed. Ships as both a GTK4 application and a
 command line tool.
CONTROL

cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    # Safe to enable for every user: `aqualizer --apply-only` exits immediately
    # without doing anything when the user has never used Aqualizer or has
    # turned it off.
    if command -v deb-systemd-helper >/dev/null 2>&1; then
        deb-systemd-helper --user enable aqualizer.service >/dev/null 2>&1 || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
    fi
fi

exit 0
POSTINST
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e

if [ "$1" = "remove" ] && command -v deb-systemd-helper >/dev/null 2>&1; then
    deb-systemd-helper --user disable aqualizer.service >/dev/null 2>&1 || true
fi

exit 0
PRERM
chmod 755 "$STAGE/DEBIAN/prerm"

cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e

if [ "$1" = "purge" ] && command -v deb-systemd-helper >/dev/null 2>&1; then
    deb-systemd-helper --user purge aqualizer.service >/dev/null 2>&1 || true
fi

if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
fi

exit 0
POSTRM
chmod 755 "$STAGE/DEBIAN/postrm"

find "$STAGE" -type d -exec chmod 755 {} +
DEB="$OUTDIR/${PKG}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
echo "Done: $DEB"
dpkg-deb --info "$DEB" | sed -n '2,8p'
