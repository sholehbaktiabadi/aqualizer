"""Built-in equalizer presets and user-created ones.

A preset is just ten gain values in dB, one per band in :data:`const.BANDS`.
Built-in presets are embedded in the code; user presets live as JSON files under
``~/.config/aqualizer/presets/``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .const import GAIN_MAX_DB, GAIN_MIN_DB, N_BANDS, user_presets_dir


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    gains: tuple[float, ...]
    builtin: bool = False
    #: Only set for user presets; used when deleting.
    path: Path | None = field(default=None, compare=False)


def _p(pid: str, name: str, *gains: float) -> Preset:
    assert len(gains) == N_BANDS, f"{pid}: expected {N_BANDS} bands, got {len(gains)}"
    return Preset(pid, name, tuple(float(g) for g in gains), builtin=True)


#                        31   63  125  250  500   1k   2k   4k   8k  16k
BUILTIN_PRESETS: tuple[Preset, ...] = (
    _p("standard",   "Standard",     0,   0,   0,   0,   0,   0,   0,   0,   0,   0),
    _p("bass",       "Bass",         8,   7,   5,   2,   0,  -1,  -1,   0,   1,   2),
    _p("bass-light", "Light Bass",   6,   5,   3,   1,   0,   0,   0,   0,   0,   0),
    _p("acoustic",   "Acoustic",     4,   3,   1,   0,   1,   1,   2,   3,   3,   2),
    _p("vocal",      "Vocal",       -2,  -1,   0,   2,   4,   4,   3,   1,   0,  -1),
    _p("rock",       "Rock",         5,   4,   2,  -1,  -2,   0,   2,   4,   4,   4),
    _p("pop",        "Pop",         -1,   0,   2,   4,   4,   2,   0,  -1,  -1,  -1),
    _p("jazz",       "Jazz",         4,   3,   1,   2,  -1,  -1,   0,   1,   2,   3),
    _p("classical",  "Classical",    5,   4,   3,   2,  -1,  -1,   0,   2,   3,   4),
    _p("night",      "Night",        2,   1,   0,   2,   4,   4,   3,   1,  -2,  -4),
    _p("loudness",   "Loudness",     6,   5,   3,   0,  -1,  -1,   0,   2,   4,   6),
)

FLAT: tuple[float, ...] = (0.0,) * N_BANDS

#: Pseudo-id for "sliders moved by hand, matching no preset".
CUSTOM_ID = "custom"

#: The preset selected on a fresh install.
DEFAULT_ID = "standard"


def clamp_gains(gains) -> tuple[float, ...]:
    """Force any sequence of gains into exactly N_BANDS values within safe limits."""
    out = []
    for i in range(N_BANDS):
        try:
            value = float(gains[i])
        except (IndexError, TypeError, ValueError):
            value = 0.0
        out.append(min(GAIN_MAX_DB, max(GAIN_MIN_DB, value)))
    return tuple(out)


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "preset"


def load_user_presets() -> list[Preset]:
    directory = user_presets_dir()
    if not directory.is_dir():
        return []
    presets = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            presets.append(
                Preset(
                    id=str(data.get("id") or path.stem),
                    name=str(data.get("name") or path.stem),
                    gains=clamp_gains(data.get("gains", FLAT)),
                    builtin=False,
                    path=path,
                )
            )
        except (OSError, ValueError, TypeError):
            # A broken file must not take the application down; skip it.
            continue
    return presets


def all_presets() -> list[Preset]:
    return [*BUILTIN_PRESETS, *load_user_presets()]


def find(preset_id: str) -> Preset | None:
    wanted = preset_id.strip().lower()
    for preset in all_presets():
        if preset.id.lower() == wanted or preset.name.lower() == wanted:
            return preset
    return None


def save_user_preset(name: str, gains) -> Preset:
    directory = user_presets_dir()
    directory.mkdir(parents=True, exist_ok=True)
    preset_id = slugify(name)
    # Never let a user preset shadow a built-in one of the same name.
    if any(b.id == preset_id for b in BUILTIN_PRESETS):
        preset_id = f"{preset_id}-user"
    path = directory / f"{preset_id}.json"
    preset = Preset(preset_id, name.strip() or preset_id, clamp_gains(gains), path=path)
    path.write_text(
        json.dumps(
            {"id": preset.id, "name": preset.name, "gains": list(preset.gains)},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return preset


def delete_user_preset(preset: Preset) -> bool:
    if preset.builtin or preset.path is None:
        return False
    try:
        preset.path.unlink()
    except OSError:
        return False
    return True
