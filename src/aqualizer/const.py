"""Shared constants: application identity, band definitions, and file locations."""

from __future__ import annotations

import os
from pathlib import Path

APP_ID = "io.github.sholehbaktiabadi.Aqualizer"
APP_NAME = "Aqualizer"
VERSION = "0.1.0"

# PipeWire node names. These are used to find the filter chain again through
# pw-dump, so they must match exactly what gets written to the config file.
NODE_NAME = "aqualizer_eq"
NODE_NAME_OUT = "aqualizer_eq_out"
LINK_GROUP = "aqualizer"
SMART_NAME = "aqualizer"

# Name of the preamp node in the graph; its control is addressed as "preamp:Mult".
PREAMP_NODE = "preamp"

#: One-octave ISO bands. (frequency in Hz, display label, PipeWire builtin filter)
#: The first band is a low shelf and the last a high shelf so that the extremes of
#: the spectrum lift as a whole, rather than as a narrow peak the way peaking
#: filters would.
BANDS: tuple[tuple[float, str, str], ...] = (
    (31.5, "31 Hz", "bq_lowshelf"),
    (63.0, "63 Hz", "bq_peaking"),
    (125.0, "125 Hz", "bq_peaking"),
    (250.0, "250 Hz", "bq_peaking"),
    (500.0, "500 Hz", "bq_peaking"),
    (1000.0, "1 kHz", "bq_peaking"),
    (2000.0, "2 kHz", "bq_peaking"),
    (4000.0, "4 kHz", "bq_peaking"),
    (8000.0, "8 kHz", "bq_peaking"),
    (16000.0, "16 kHz", "bq_highshelf"),
)

N_BANDS = len(BANDS)

#: Q for the peaking bands. 1.41 is roughly one octave wide, so the ten bands meet
#: smoothly without gaps or excessive overlap.
BAND_Q = 1.41

#: Q for the shelving bands at either end. Gentler than the peaking bands so the
#: edges of the spectrum lift evenly instead of bulging at one point.
SHELF_Q = 0.7

#: Per-band gain limits, used by both the UI and CLI validation.
GAIN_MIN_DB = -12.0
GAIN_MAX_DB = 12.0

#: Limits for the manual preamp.
PREAMP_MIN_DB = -20.0
PREAMP_MAX_DB = 6.0


def _xdg(env: str, default: str) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else Path.home() / default


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def pipewire_conf_dir() -> Path:
    """Directory of config fragments read by filter-chain.service."""
    return config_home() / "pipewire" / "filter-chain.conf.d"


def pipewire_conf_path() -> Path:
    return pipewire_conf_dir() / "99-aqualizer.conf"


def app_config_dir() -> Path:
    return config_home() / "aqualizer"


def state_path() -> Path:
    return app_config_dir() / "state.json"


def user_presets_dir() -> Path:
    return app_config_dir() / "presets"
