"""Application logic: the bridge between saved state and the PipeWire graph.

Shared by the CLI and the GUI so the two can never drift apart in behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import pipewire as pw
from . import presets as presets_mod
from . import state as state_mod
from .const import NODE_NAME
from .graph import MODE_SINK, MODE_SMART, render
from .presets import CUSTOM_ID, FLAT, clamp_gains
from .state import State


@dataclass
class Status:
    installed: bool
    running: bool
    mode: str
    enabled: bool
    preset: str
    gains: tuple[float, ...]
    preamp_db: float
    target: str | None
    device: str | None
    intercepting: bool


class Engine:
    """Single-use per operation; nothing is cached between calls."""

    def __init__(self, state: State | None = None):
        self.state = state if state is not None else state_mod.load()

    # ----------------------------------------------------------------- #
    # Device and mode selection
    # ----------------------------------------------------------------- #

    def _effective_device(self, objs: list[dict]) -> pw.Sink | None:
        """The hardware device that should be receiving audio.

        In order: the user's explicit choice, then the default output, then the
        last device recognised. That final step is what lets sink mode recover —
        there the default output is Aqualizer itself, so the real device is only
        recorded in state.
        """
        pinned = pw.find_sink(self.state.target, objs)
        if pinned is not None:
            return pinned

        default_name = pw.default_sink_name(objs)
        if default_name and default_name != NODE_NAME:
            found = pw.find_sink(default_name, objs)
            if found is not None:
                return found

        return pw.find_sink(self.state.device, objs)

    def _remember(self, device: pw.Sink | None) -> None:
        if device is not None and self.state.device != device.name:
            self.state.device = device.name
            state_mod.save(self.state)

    def _desired_mode(self, device: pw.Sink | None) -> str:
        return pw.recommended_mode(device)

    # ----------------------------------------------------------------- #
    # Applying values
    # ----------------------------------------------------------------- #

    def preamp_db(self) -> float:
        if self.state.auto_preamp:
            return pw.auto_preamp_db(self.state.gains)
        return self.state.preamp_db

    def _effective_values(self) -> tuple[list[float], float]:
        """The values actually sent to the DSP, with bypass taken into account."""
        if not self.state.enabled:
            # A biquad at 0 dB gain has unity coefficients, so a flattened chain
            # really is transparent — not merely close to it.
            return list(FLAT), 0.0
        return list(self.state.gains), self.preamp_db()

    def apply(self, *, allow_restart: bool = True) -> None:
        """Send the whole state to PipeWire: routing, bypass, and gain values.

        Gains are written in two places because each applies at a different time:
        the configuration file is what a loading chain starts from, and
        ``pw-cli set-param`` is what reaches a chain already running. The chain is
        only reloaded when its structure changes or the node has never been active
        — and in that last case there is no audio that could be interrupted.
        """
        if not self.state.installed:
            self.state.installed = True
            state_mod.save(self.state)

        objs = pw.dump()
        device = self._effective_device(objs)
        self._remember(device)
        mode = self._desired_mode(device)
        gains, preamp = self._effective_values()

        # Sink mode needs to know its destination from the configuration file
        # onwards, so that audio does not stall while a new chain loads.
        target = device.name if (mode == MODE_SINK and device) else None
        changed = pw.write_config(render(mode, target, gains, pw.db_to_linear(preamp)))

        capture, playback = pw.find_nodes(objs)
        structure_changed = mode != self.state.mode
        # A node that has never been active discards Props changes without any
        # error, so reloading the chain is the only way new values can land.
        never_ran = pw.node_state(capture, objs) in (None, "suspended")

        if capture is None or playback is None or structure_changed or (changed and never_ran):
            if not allow_restart:
                raise pw.PipeWireError("the chain needs reloading but reloading is not allowed")
            pw.restart_chain()
            capture, playback = pw.wait_for_nodes()
            objs = pw.dump()
        elif changed:
            pw.apply_gains(capture, gains, preamp)

        if mode != self.state.mode:
            self.state.mode = mode
            state_mod.save(self.state)

        self._route(mode, capture, playback, device, objs)

    def _route(
        self,
        mode: str,
        capture: int,
        playback: int,
        device: pw.Sink | None,
        objs: list[dict],
    ) -> None:
        if mode == MODE_SMART:
            # Smart mode must not leave Aqualizer as the default output — that is
            # a leftover from sink mode, and would loop audio back into a filter
            # that is meant to be transparent.
            if device is not None and pw.default_sink_name(objs) == NODE_NAME:
                pw.set_default_sink(device.name)
            # No pinned device means the filter follows wherever the default
            # output moves, which is the most comfortable behaviour, so it is the
            # default.
            pw.set_smart_target(capture, self.state.target, objs)
            pw.set_smart_disabled(capture, not self.state.enabled, objs)
        elif device is not None:
            pw.set_stream_target(playback, device.name, objs)
            if pw.default_sink_name(objs) != NODE_NAME:
                pw.set_default_sink(NODE_NAME)

    # ----------------------------------------------------------------- #
    # State changes
    # ----------------------------------------------------------------- #

    def _commit(self) -> None:
        state_mod.save(self.state)
        self.apply()

    def select_preset(self, preset_id: str) -> presets_mod.Preset:
        preset = presets_mod.find(preset_id)
        if preset is None:
            available = ", ".join(p.id for p in presets_mod.all_presets())
            raise ValueError(f"no such preset '{preset_id}'. Available: {available}")
        self.state.preset = preset.id
        self.state.gains = list(preset.gains)
        self._commit()
        return preset

    def set_gains(self, gains) -> None:
        self.state.gains = list(clamp_gains(gains))
        self.state.preset = self._match_preset(self.state.gains)
        self._commit()

    @staticmethod
    def _match_preset(gains) -> str:
        for preset in presets_mod.all_presets():
            if all(abs(a - b) < 1e-6 for a, b in zip(preset.gains, gains)):
                return preset.id
        return CUSTOM_ID

    def set_enabled(self, enabled: bool) -> None:
        self.state.enabled = bool(enabled)
        self._commit()

    def set_target(self, sink_name: str | None) -> None:
        self.state.target = sink_name or None
        self._commit()

    def save_preset(self, name: str) -> presets_mod.Preset:
        """Save the current band settings as a user preset, then select it."""
        preset = presets_mod.save_user_preset(name, self.state.gains)
        self.state.preset = preset.id
        self._commit()
        return preset

    def set_preamp(self, *, auto: bool, manual_db: float | None = None) -> None:
        self.state.auto_preamp = bool(auto)
        if manual_db is not None:
            self.state.preamp_db = float(manual_db)
        self._commit()

    # ----------------------------------------------------------------- #
    # Reporting and removal
    # ----------------------------------------------------------------- #

    def status(self) -> Status:
        objs = pw.dump()
        capture, _ = pw.find_nodes(objs)
        device = self._effective_device(objs)
        return Status(
            installed=pw.config_installed(),
            running=capture is not None,
            mode=self.state.mode,
            enabled=self.state.enabled,
            preset=self.state.preset,
            gains=tuple(self.state.gains),
            preamp_db=self.preamp_db(),
            target=self.state.target,
            device=device.description if device else None,
            intercepting=pw.is_intercepting(objs) if capture is not None else False,
        )

    def uninstall(self) -> None:
        """Take Aqualizer out of the audio path and restore the previous output."""
        objs = pw.dump()
        device = self._effective_device(objs)
        removed = pw.remove_config()

        # In sink mode Aqualizer is the default output. Restore the real device
        # first, before its node disappears along with the chain.
        if device is not None and pw.default_sink_name(objs) == NODE_NAME:
            pw.set_default_sink(device.name)

        self.state.installed = False
        state_mod.save(self.state)

        if removed:
            pw.restart_chain()


def apply_saved_state() -> bool:
    """Entry point for ``aqualizer --apply-only`` at login.

    The package installs the systemd unit for every user on the system, so this
    has to stay silent for anyone who has never opened Aqualizer or who has turned
    it off — installing a filter unasked is the last thing an automatically
    running unit should do.
    """
    if not state_mod.exists():
        return False
    engine = Engine()
    if not engine.state.installed:
        return False
    engine.apply()
    return True
