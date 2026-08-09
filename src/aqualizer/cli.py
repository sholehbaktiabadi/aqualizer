"""Aqualizer's command line interface.

Running ``aqualizer`` with no arguments opens the GTK window. The subcommands
below give the same access without a GUI, and are what the systemd unit uses to
reapply the saved preset whenever a session starts.
"""

from __future__ import annotations

import argparse
import sys

from . import pipewire as pw
from . import presets as presets_mod
from .const import APP_NAME, BANDS, GAIN_MAX_DB, N_BANDS, VERSION
from .engine import Engine, apply_saved_state
from .graph import MODE_SINK
from .presets import CUSTOM_ID


def _err(message: str) -> int:
    print(f"aqualizer: {message}", file=sys.stderr)
    return 1


def _bar(db: float, width: int = 21) -> str:
    """A small horizontal bar with 0 dB at the centre."""
    half = width // 2
    filled = int(round(abs(db) / GAIN_MAX_DB * half))
    if db >= 0:
        return " " * half + "│" + "█" * filled + " " * (half - filled)
    return " " * (half - filled) + "█" * filled + "│" + " " * half


def _print_bands(gains) -> None:
    for (_, label, _), gain in zip(BANDS, gains):
        print(f"  {label:>6}  {_bar(gain)}  {gain:+5.1f} dB")


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #


def cmd_status(_args) -> int:
    engine = Engine()
    st = engine.status()
    print(f"{APP_NAME} {VERSION}")
    print(f"  Config     : {'installed' if st.installed else 'not installed'}")
    print(f"  Chain      : {'running' if st.running else 'not running'}")
    print(f"  State      : {'on' if st.enabled else 'off (bypassed)'}")
    print(f"  Preset     : {st.preset}")
    print(f"  Preamp     : {st.preamp_db:+.1f} dB"
          f"{' (automatic)' if engine.state.auto_preamp else ''}")
    print(f"  Mode       : {st.mode}"
          f"{' — virtual sink, required for Bluetooth' if st.mode == MODE_SINK else ''}")
    print(f"  Output     : {st.device or '(unknown)'}"
          f"{'' if st.target else ' — following the default'}")
    if st.running:
        print(f"  Filtering  : {'yes' if st.intercepting else 'no stream passing through yet'}")
    print()
    _print_bands(st.gains)
    return 0


def cmd_list(_args) -> int:
    active = Engine().state.preset
    for preset in presets_mod.all_presets():
        mark = "*" if preset.id == active else " "
        kind = "built-in" if preset.builtin else "yours"
        print(f" {mark} {preset.id:<14} {preset.name:<14} ({kind})")
    if active == CUSTOM_ID:
        print(f" * {CUSTOM_ID:<14} Custom         (sliders moved by hand)")
    return 0


def cmd_set(args) -> int:
    try:
        preset = Engine().select_preset(args.preset)
    except ValueError as exc:
        return _err(str(exc))
    print(f"Preset '{preset.name}' applied.")
    return 0


def cmd_bands(args) -> int:
    if len(args.gain) != N_BANDS:
        return _err(f"expected exactly {N_BANDS} dB values, got {len(args.gain)}")
    engine = Engine()
    engine.set_gains(args.gain)
    _print_bands(engine.state.gains)
    return 0


def cmd_on(_args) -> int:
    Engine().set_enabled(True)
    print("Equalizer turned on.")
    return 0


def cmd_off(_args) -> int:
    engine = Engine()
    engine.set_enabled(False)
    if engine.state.mode == MODE_SINK:
        print("Equalizer turned off (response flattened; audio still passes through "
              "the Aqualizer sink).")
    else:
        print("Equalizer turned off (detached from the audio path).")
    return 0


def cmd_devices(_args) -> int:
    engine = Engine()
    objs = pw.dump()
    default = pw.default_sink_name(objs)
    pinned = engine.state.target
    if pinned is None:
        print("  * (automatic)     following the default output")
    for sink in pw.list_sinks(objs):
        mark = "*" if sink.name == pinned else " "
        notes = []
        if sink.name == default:
            notes.append("default")
        if sink.smart_filter:
            notes.append("needs sink mode")
        suffix = f"  [{', '.join(notes)}]" if notes else ""
        print(f" {mark} {sink.description}{suffix}")
        print(f"     {sink.name}")
    return 0


def cmd_device(args) -> int:
    engine = Engine()
    if args.name.lower() in ("auto", "automatic", "default"):
        engine.set_target(None)
        print("Output now follows the default device.")
        return 0
    if pw.find_sink(args.name) is None:
        return _err(f"no device named '{args.name}' — see `aqualizer devices`")
    engine.set_target(args.name)
    print(f"Output pinned to {args.name} ({engine.state.mode} mode).")
    return 0


def cmd_save(args) -> int:
    preset = Engine().save_preset(args.name)
    print(f"Preset '{preset.name}' saved as {preset.id}.")
    return 0


def cmd_apply(_args) -> int:
    Engine().apply()
    return 0


def cmd_apply_only(_args) -> int:
    if not apply_saved_state():
        print("Aqualizer is not active for this user; nothing applied.")
    return 0


def cmd_install(_args) -> int:
    engine = Engine()
    engine.apply()
    st = engine.status()
    print(f"Aqualizer installed in {st.mode} mode, output: {st.device or '(automatic)'}.")
    return 0


def cmd_uninstall(_args) -> int:
    Engine().uninstall()
    print("Aqualizer removed from the audio path. PipeWire configuration restored.")
    return 0


def cmd_gui(_args) -> int:
    try:
        from .application import run
    except ImportError as exc:
        return _err(
            f"GUI components unavailable ({exc}). "
            "Install python3-gi, gir1.2-gtk-4.0 and gir1.2-adw-1, "
            "or use a subcommand such as `aqualizer set bass`."
        )
    return run([])


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aqualizer",
        description=f"{APP_NAME} — audio output equalizer for PipeWire.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    # Used by the systemd unit at login; offered as an option rather than a
    # subcommand so the line reads clearly in the unit file.
    parser.add_argument(
        "--apply-only",
        action="store_true",
        help="reapply the saved preset and exit (no GUI)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="show the current state").set_defaults(func=cmd_status)
    sub.add_parser("list", help="list presets").set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="apply a preset")
    p_set.add_argument("preset")
    p_set.set_defaults(func=cmd_set)

    p_bands = sub.add_parser("bands", help=f"set all {N_BANDS} bands by hand (dB)")
    p_bands.add_argument("gain", nargs="*", type=float)
    p_bands.set_defaults(func=cmd_bands)

    sub.add_parser("on", help="turn the equalizer on").set_defaults(func=cmd_on)
    sub.add_parser("off", help="turn the equalizer off").set_defaults(func=cmd_off)

    sub.add_parser("devices", help="list output devices").set_defaults(func=cmd_devices)

    p_device = sub.add_parser("device", help="pin the output to a specific device")
    p_device.add_argument("name", help="the device's node.name, or 'auto'")
    p_device.set_defaults(func=cmd_device)

    p_save = sub.add_parser("save", help="save the current band settings as a preset")
    p_save.add_argument("name")
    p_save.set_defaults(func=cmd_save)

    sub.add_parser("apply", help="reapply the saved state").set_defaults(func=cmd_apply)
    sub.add_parser("install", help="install the chain into PipeWire").set_defaults(
        func=cmd_install
    )
    sub.add_parser(
        "uninstall", help="remove the chain and restore audio as it was"
    ).set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not pw.available():
        return _err("pw-dump/pw-cli/pw-metadata not found — install the pipewire-bin package")

    if args.apply_only:
        handler = cmd_apply_only
    elif getattr(args, "func", None) is not None:
        handler = args.func
    else:
        handler = cmd_gui

    try:
        return handler(args)
    except pw.PipeWireError as exc:
        return _err(str(exc))
    except KeyboardInterrupt:
        return 130
