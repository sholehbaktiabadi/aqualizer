"""GTK entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from .const import APP_ID  # noqa: E402
from .window import AqualizerWindow  # noqa: E402


class AqualizerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.set_accels_for_action("window.close", ["<Control>w", "<Control>q"])

    def do_activate(self) -> None:
        window = self.props.active_window or AqualizerWindow(self)
        window.present()


def run(argv: list[str] | None = None) -> int:
    return AqualizerApplication().run(argv if argv is not None else sys.argv[1:])
