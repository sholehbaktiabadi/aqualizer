"""Application state that survives between sessions.

Gain values live inside the running filter-chain process and are lost every time
PipeWire restarts. This file is their source of truth, and
``aqualizer --apply-only`` (run by the systemd user unit at login) is what sends
them back to PipeWire.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .const import state_path
from .presets import DEFAULT_ID, FLAT, clamp_gains


@dataclass
class State:
    #: False means the equalizer is bypassed (audio passes through unprocessed).
    enabled: bool = True
    #: Id of the active preset, or presets.CUSTOM_ID when sliders were moved by hand.
    preset: str = DEFAULT_ID
    gains: list[float] = field(default_factory=lambda: list(FLAT))
    #: Automatic preamp works out just enough attenuation to avoid clipping.
    auto_preamp: bool = True
    #: Manual preamp in dB, used only when auto_preamp is False.
    preamp_db: float = 0.0
    #: node.name of the pinned output device, or None to follow the default sink.
    target: str | None = None
    #: Bookkeeping rather than a user choice: the last real device recognised.
    #: In sink mode Aqualizer itself becomes the default output, so this is the
    #: only record of which device it took over.
    device: str | None = None
    #: "smart" (WirePlumber inserts it automatically) or "sink" (classic virtual sink).
    mode: str = "smart"
    #: False after the user chooses "Turn off Aqualizer". The login-time apply
    #: honours this, so the filter never quietly reinstalls itself.
    installed: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"


def load() -> State:
    path = state_path()
    if not path.is_file():
        return State()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return State()
    if not isinstance(data, dict):
        return State()

    default = State()
    target = data.get("target", default.target)
    device = data.get("device", default.device)
    mode = data.get("mode", default.mode)
    return State(
        enabled=bool(data.get("enabled", default.enabled)),
        preset=str(data.get("preset", default.preset)),
        gains=list(clamp_gains(data.get("gains", FLAT))),
        auto_preamp=bool(data.get("auto_preamp", default.auto_preamp)),
        preamp_db=float(data.get("preamp_db", default.preamp_db)),
        target=str(target) if target else None,
        device=str(device) if device else None,
        mode=mode if mode in ("smart", "sink") else default.mode,
        installed=bool(data.get("installed", default.installed)),
    )


def exists() -> bool:
    """True when this user has used Aqualizer before."""
    return state_path().is_file()


def mtime() -> float | None:
    """Modification time of the state file, or None when it does not exist.

    Used to notice changes made by another process — running ``aqualizer set``
    in a terminal while the window is open, for instance.
    """
    try:
        return state_path().stat().st_mtime
    except OSError:
        return None


def save(state: State) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write through a temporary file so a process dying mid-write cannot leave
    # half-written state behind.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(state.to_json(), encoding="utf-8")
    tmp.replace(path)
