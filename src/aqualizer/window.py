"""Aqualizer's main window (GTK4 + libadwaita)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import pipewire as pw  # noqa: E402
from . import presets as presets_mod  # noqa: E402
from . import state as state_mod  # noqa: E402
from .const import (  # noqa: E402
    APP_ID,
    APP_NAME,
    BANDS,
    GAIN_MAX_DB,
    GAIN_MIN_DB,
    PREAMP_MAX_DB,
    PREAMP_MIN_DB,
    VERSION,
)
from .engine import Engine  # noqa: E402
from .graph import MODE_SINK  # noqa: E402
from .presets import DEFAULT_ID, Preset  # noqa: E402
from .worker import Worker  # noqa: E402

AUTO_LABEL = "Automatic"

CSS = b"""
.eq-strip { min-height: 240px; padding: 6px 0; }
"""

#: How much smaller the band and preamp readouts are than body text.
#:
#: Kept close to 1.0 on purpose. Shrunk much further — libadwaita's .caption
#: class, at 0.82, is already past the line — thin-stroked fonts start losing
#: the top strokes of 1, 2, 3 and 5 to hinting, so "31 Hz" renders as "51 Hz"
#: and "+1.2" as "+⊥.2". Letters and round digits survive, which makes the
#: damage easy to miss. 0.92 still reads as secondary text and stays legible
#: with a light-weight UI font.
SMALL_SCALE = 0.92


def _small_label(text: str, *, tabular: bool = False, **props) -> Gtk.Label:
    """A label a little smaller than body text, sized through Pango.

    Pango attributes rather than a CSS ``font-size``: CSS-scaled labels came out
    noticeably worse, clipping glyph tops outright at sizes where Pango-scaled
    ones were still intact.

    ``tabular`` asks for fixed-width figures so readouts do not jitter sideways
    as their values change.
    """
    label = Gtk.Label(label=text, **props)
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_scale_new(SMALL_SCALE))
    if tabular:
        attrs.insert(Pango.attr_font_features_new("tnum=1"))
    label.set_attributes(attrs)
    label.add_css_class("dim-label")
    return label


class AqualizerWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(application=application, title=APP_NAME)
        self.set_default_size(600, 800)

        self.engine = Engine()
        self._updating = False
        self._sink_names: list[str | None] = [None]
        self._preset_buttons: dict[str, Gtk.ToggleButton] = {}

        self._install_css()
        self._build_ui()
        self._load_from_state()

        self.worker = Worker(
            self.engine, self._on_worker_error, self._on_poll, self._on_reload
        )
        self.worker.start()
        self.worker.request_apply()
        self.connect("close-request", self._on_close)

    # ----------------------------------------------------------------- #
    # Building the interface
    # ----------------------------------------------------------------- #

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self) -> None:
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        view.add_top_bar(self._build_header())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(self._build_output_group())
        box.append(self._build_preset_group())
        box.append(self._build_eq_group())
        box.append(self._build_preamp_group())

        clamp = Adw.Clamp(maximum_size=620, child=box)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        scroller.set_vexpand(True)
        view.set_content(scroller)

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        self.master_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.master_switch.set_tooltip_text("Turn the equalizer on or bypass it")
        self.master_switch.connect("notify::active", self._on_master_toggled)
        header.pack_end(self.master_switch)

        menu = Gio.Menu()
        menu.append("Save as preset…", "win.save-preset")
        menu.append("Reset to Standard", "win.reset")
        section = Gio.Menu()
        section.append("Turn off Aqualizer…", "win.uninstall")
        section.append(f"About {APP_NAME}", "win.about")
        menu.append_section(None, section)

        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        button.set_tooltip_text("Main menu")
        header.pack_end(button)

        for name, handler in (
            ("save-preset", self._on_save_preset),
            ("reset", self._on_reset),
            ("uninstall", self._on_uninstall),
            ("about", self._on_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        return header

    def _build_output_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Output")
        self.device_row = Adw.ComboRow(title="Device", model=Gtk.StringList())
        self.device_row.connect("notify::selected", self._on_device_changed)
        group.add(self.device_row)
        self.output_group = group
        return group

    def _build_preset_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Presets")
        self.preset_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=3,
            min_children_per_line=2,
            homogeneous=True,
            row_spacing=6,
            column_spacing=6,
        )
        group.add(self.preset_flow)
        return group

    def _build_eq_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Equalizer")

        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=2)
        strip.add_css_class("eq-strip")
        self.band_scales: list[Gtk.Scale] = []
        self.band_values: list[Gtk.Label] = []

        for index, (_freq, label, _kind) in enumerate(BANDS):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            value = _small_label("0.0", tabular=True)
            column.append(value)

            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.VERTICAL, GAIN_MIN_DB, GAIN_MAX_DB, 0.5
            )
            # GTK puts the smallest value at the top of a vertical scale; inverting
            # it makes dragging upwards mean boost, like a real equalizer.
            scale.set_inverted(True)
            scale.set_draw_value(False)
            # Without this, GTK fills the trough from the lower bound up to the
            # handle, as if −12 dB were the zero point. These values are bipolar,
            # so the reference is the 0 dB mark in the middle, not a fill colour.
            scale.set_has_origin(False)
            scale.set_vexpand(True)
            scale.add_mark(0.0, Gtk.PositionType.LEFT, None)
            scale.connect("value-changed", self._on_band_changed, index)
            column.append(scale)

            column.append(_small_label(label))

            strip.append(column)
            self.band_scales.append(scale)
            self.band_values.append(value)

        group.add(strip)
        return group

    def _build_preamp_group(self) -> Adw.PreferencesGroup:
        # Kept separate from the band strip: Adw.PreferencesGroup always places
        # plain widgets below its list of rows, so combining the two would put
        # them on screen in the wrong order.
        group = Adw.PreferencesGroup(title="Preamp")

        self.auto_preamp_row = Adw.SwitchRow(
            title="Automatic",
            subtitle="Lower the level just enough to keep boosts from clipping",
        )
        self.auto_preamp_row.connect("notify::active", self._on_auto_preamp_toggled)
        group.add(self.auto_preamp_row)

        self.preamp_row = Adw.ActionRow(title="Level")
        self.preamp_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, PREAMP_MIN_DB, PREAMP_MAX_DB, 0.5
        )
        self.preamp_scale.set_size_request(220, -1)
        self.preamp_scale.set_draw_value(False)
        self.preamp_scale.set_has_origin(False)
        self.preamp_scale.set_valign(Gtk.Align.CENTER)
        self.preamp_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, None)
        self.preamp_scale.connect("value-changed", self._on_preamp_changed)
        self.preamp_value = _small_label(
            "0.0 dB", tabular=True, valign=Gtk.Align.CENTER
        )
        self.preamp_row.add_suffix(self.preamp_scale)
        self.preamp_row.add_suffix(self.preamp_value)
        group.add(self.preamp_row)

        return group

    # ----------------------------------------------------------------- #
    # Copying state into the interface
    # ----------------------------------------------------------------- #

    def _load_from_state(self) -> None:
        self._updating = True
        try:
            state = self.engine.state
            self.master_switch.set_active(state.enabled)
            self.auto_preamp_row.set_active(state.auto_preamp)
            for scale, gain in zip(self.band_scales, state.gains):
                scale.set_value(gain)
            self._refresh_band_labels()
            self._refresh_preamp()
            self._rebuild_presets()
            self._refresh_devices()
        finally:
            self._updating = False

    def _refresh_band_labels(self) -> None:
        for label, gain in zip(self.band_values, self.engine.state.gains):
            label.set_label(f"{gain:+.1f}")

    def _refresh_preamp(self) -> None:
        auto = self.engine.state.auto_preamp
        self.preamp_row.set_sensitive(not auto)
        value = self.engine.preamp_db()
        if abs(self.preamp_scale.get_value() - value) > 1e-6:
            self.preamp_scale.set_value(value)
        self.preamp_value.set_label(f"{value:+.1f} dB")

    def _rebuild_presets(self) -> None:
        while (child := self.preset_flow.get_first_child()) is not None:
            self.preset_flow.remove(child)
        self._preset_buttons.clear()

        for preset in presets_mod.all_presets():
            button = Gtk.ToggleButton(label=preset.name)
            button.connect("toggled", self._on_preset_toggled, preset)
            if not preset.builtin:
                button.set_tooltip_text("Your preset — right-click to delete")
                gesture = Gtk.GestureClick(button=3)
                gesture.connect("pressed", self._on_preset_secondary, preset)
                button.add_controller(gesture)
            self._preset_buttons[preset.id] = button
            self.preset_flow.append(button)

        self._refresh_preset_selection()

    def _refresh_preset_selection(self) -> None:
        active = self.engine.state.preset
        for preset_id, button in self._preset_buttons.items():
            button.set_active(preset_id == active)

    def _refresh_devices(self) -> None:
        try:
            objs = pw.dump()
            sinks = pw.list_sinks(objs)
        except pw.PipeWireError as exc:
            self._on_worker_error(exc)
            return

        names: list[str | None] = [None] + [s.name for s in sinks]
        labels = [AUTO_LABEL] + [s.description for s in sinks]
        if names == self._sink_names and self.device_row.get_model().get_n_items():
            self._refresh_device_subtitle(objs)
            return

        self._sink_names = names
        model = Gtk.StringList()
        for label in labels:
            model.append(label)

        was_updating = self._updating
        self._updating = True
        try:
            self.device_row.set_model(model)
            target = self.engine.state.target
            self.device_row.set_selected(names.index(target) if target in names else 0)
        finally:
            self._updating = was_updating
        self._refresh_device_subtitle(objs)

    def _refresh_device_subtitle(self, objs: list[dict] | None = None) -> None:
        state = self.engine.state
        device = self.engine._effective_device(objs if objs is not None else pw.dump())
        parts = [device.description] if device else ["no device yet"]
        if state.mode == MODE_SINK:
            parts.append("through a virtual sink — required for Bluetooth")
        else:
            parts.append("inserted automatically")
        self.device_row.set_subtitle(" · ".join(parts))

    # ----------------------------------------------------------------- #
    # Responding to user actions
    # ----------------------------------------------------------------- #

    def _touch(self) -> None:
        self.worker.request_apply()

    def _on_master_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._updating:
            return
        self.engine.state.enabled = switch.get_active()
        self._touch()

    def _on_band_changed(self, scale: Gtk.Scale, index: int) -> None:
        if self._updating:
            return
        self.engine.state.gains[index] = scale.get_value()
        self.engine.state.preset = Engine._match_preset(self.engine.state.gains)
        self.band_values[index].set_label(f"{scale.get_value():+.1f}")
        self._refresh_preamp()
        self._updating = True
        try:
            self._refresh_preset_selection()
        finally:
            self._updating = False
        self._touch()

    def _on_preset_toggled(self, button: Gtk.ToggleButton, preset: Preset) -> None:
        if self._updating:
            return
        if not button.get_active():
            # Pressing the active preset must not switch it off; one preset is
            # always selected.
            self._updating = True
            try:
                button.set_active(True)
            finally:
                self._updating = False
            return

        self.engine.state.preset = preset.id
        self.engine.state.gains = list(preset.gains)
        self._updating = True
        try:
            for scale, gain in zip(self.band_scales, preset.gains):
                scale.set_value(gain)
            self._refresh_preset_selection()
        finally:
            self._updating = False
        self._refresh_band_labels()
        self._refresh_preamp()
        self._touch()

    def _on_preset_secondary(self, _gesture, _n, _x, _y, preset: Preset) -> None:
        dialog = Adw.AlertDialog(
            heading="Delete preset?",
            body=f"The preset '{preset.name}' will be deleted permanently.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_delete_response, preset)
        dialog.present(self)

    def _on_delete_response(self, _dialog, response: str, preset: Preset) -> None:
        if response != "delete":
            return
        if presets_mod.delete_user_preset(preset):
            if self.engine.state.preset == preset.id:
                self.engine.state.preset = presets_mod.CUSTOM_ID
            self._rebuild_presets()
            self._toast(f"Preset '{preset.name}' deleted.")
        else:
            self._toast(f"Could not delete preset '{preset.name}'.")

    def _on_device_changed(self, row: Adw.ComboRow, _param) -> None:
        if self._updating:
            return
        index = row.get_selected()
        if 0 <= index < len(self._sink_names):
            self.engine.state.target = self._sink_names[index]
            self._touch()

    def _on_auto_preamp_toggled(self, row: Adw.SwitchRow, _param) -> None:
        if self._updating:
            return
        self.engine.state.auto_preamp = row.get_active()
        if not row.get_active():
            self.engine.state.preamp_db = self.preamp_scale.get_value()
        self._refresh_preamp()
        self._touch()

    def _on_preamp_changed(self, scale: Gtk.Scale) -> None:
        if self._updating or self.engine.state.auto_preamp:
            return
        self.engine.state.preamp_db = scale.get_value()
        self.preamp_value.set_label(f"{scale.get_value():+.1f} dB")
        self._touch()

    # ----------------------------------------------------------------- #
    # Menu actions
    # ----------------------------------------------------------------- #

    def _on_save_preset(self, _action, _param) -> None:
        entry = Gtk.Entry(placeholder_text="Preset name", activates_default=True)
        dialog = Adw.AlertDialog(
            heading="Save preset", body="The current band settings will be saved."
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.connect("response", self._on_save_response, entry)
        dialog.present(self)

    def _on_save_response(self, _dialog, response: str, entry: Gtk.Entry) -> None:
        name = entry.get_text().strip()
        if response != "save" or not name:
            return
        preset = presets_mod.save_user_preset(name, self.engine.state.gains)
        self.engine.state.preset = preset.id
        state_mod.save(self.engine.state)
        self._rebuild_presets()
        self._toast(f"Preset '{preset.name}' saved.")

    def _on_reset(self, _action, _param) -> None:
        if button := self._preset_buttons.get(DEFAULT_ID):
            button.set_active(True)

    def _on_uninstall(self, _action, _param) -> None:
        dialog = Adw.AlertDialog(
            heading="Turn off Aqualizer?",
            body=(
                "The filter will be detached from the audio path and the PipeWire "
                "configuration restored to how it was. Your presets are kept."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Turn off")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_uninstall_response)
        dialog.present(self)

    def _on_uninstall_response(self, _dialog, response: str) -> None:
        if response != "remove":
            return
        try:
            self.worker.stop()
            self.engine.uninstall()
        except pw.PipeWireError as exc:
            self._on_worker_error(exc)
            return
        self.close()

    def _on_about(self, _action, _param) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="sholehbaktiabadi",
            comments=(
                "A ten-band equalizer for PipeWire audio output, with ready-made "
                "presets that insert themselves into whichever device is in use."
            ),
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self)

    # ----------------------------------------------------------------- #
    # Feedback and lifecycle
    # ----------------------------------------------------------------- #

    def _toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=3))

    def _on_worker_error(self, exc: Exception) -> bool:
        self._toast(str(exc))
        return GLib.SOURCE_REMOVE

    def _on_poll(self) -> bool:
        if not self._updating:
            self._refresh_devices()
        return GLib.SOURCE_REMOVE

    def _on_reload(self) -> bool:
        """Another process changed the saved state; show it instead of fighting it."""
        self._load_from_state()
        return GLib.SOURCE_REMOVE

    def _on_close(self, _window) -> bool:
        self.worker.stop()
        state_mod.save(self.engine.state)
        return False
