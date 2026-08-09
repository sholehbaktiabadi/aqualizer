"""Generator for the PipeWire filter-chain configuration file.

The DSP graph is identical in both modes; only the way the filter is inserted
into the audio path differs:

``smart``
    The node is marked ``filter.smart``. WirePlumber inserts it on its own
    between applications and the output in use, and follows device changes. The
    real device stays visible in GNOME's sound settings. This is the better mode,
    but it cannot be used for every device — see ``pipewire.recommended_mode()``.

``sink``
    The node becomes an ordinary virtual sink that is made the default output,
    with its own output pinned to the real device. This works everywhere, at the
    cost of "Aqualizer" being the device shown in the sound settings.

The graph is deliberately written as mono, with no ``inputs``/``outputs`` block.
Per ``man 7 libpipewire-module-filter-chain``, the number of ``inputs``/``outputs``
entries *defines* the channel count — declaring them explicitly would lock the
chain to mono. When they are omitted, PipeWire duplicates the graph to match the
stream's channel count and the control values stay shared, so a single write to
``eq_band_1:Gain`` sets both left and right.
"""

from __future__ import annotations

from .const import (
    APP_NAME,
    BAND_Q,
    BANDS,
    LINK_GROUP,
    NODE_NAME,
    NODE_NAME_OUT,
    PREAMP_NODE,
    SHELF_Q,
    SMART_NAME,
    VERSION,
)

MODE_SMART = "smart"
MODE_SINK = "sink"


def band_node(index: int) -> str:
    """Name of the band node at `index` (zero-based) inside the graph."""
    return f"eq_band_{index + 1}"


def gain_key(index: int) -> str:
    return f"{band_node(index)}:Gain"


PREAMP_KEY = f"{PREAMP_NODE}:Mult"


def _q_for(label: str) -> float:
    return SHELF_Q if label.endswith("shelf") else BAND_Q


def _render_nodes(gains, preamp_linear: float) -> str:
    lines = [
        f'                    {{ type = builtin name = {PREAMP_NODE:<10}'
        f' label = {"linear":<12} control = {{ "Mult" = {preamp_linear:.6f} "Add" = 0.0 }} }}'
    ]
    for i, (freq, _label, kind) in enumerate(BANDS):
        name = band_node(i)
        lines.append(
            f"                    {{ type = builtin name = {name:<10}"
            f' label = {kind:<12} control = {{ "Freq" = {freq} "Q" = {_q_for(kind)}'
            f' "Gain" = {float(gains[i]):.4f} }} }}'
        )
    return "\n".join(lines)


def _render_links() -> str:
    chain = [PREAMP_NODE] + [band_node(i) for i in range(len(BANDS))]
    return "\n".join(
        f'                    {{ output = "{src}:Out" input = "{dst}:In" }}'
        for src, dst in zip(chain, chain[1:])
    )


def _block(pairs: list[tuple[str, str]], indent: str = " " * 16) -> str:
    width = max(len(key) for key, _ in pairs)
    return "\n".join(f"{indent}{key:<{width}} = {value}" for key, value in pairs)


def _render_props(mode: str, target: str | None) -> tuple[str, str]:
    # session.suspend-timeout-seconds = 0 stops WirePlumber from ever suspending
    # the node (see suspend-node.lua: a value of 0 returns immediately). This is
    # not merely a tuning knob: once a node is suspended its DSP graph is torn
    # down and `pw-cli set-param Props` is accepted but silently discarded, so
    # changing a preset while nothing is playing would have no effect at all.
    capture = [
        ("node.name", f'"{NODE_NAME}"'),
        ("node.description", f'"{APP_NAME}"'),
        ("media.class", "Audio/Sink"),
        ("node.link-group", f'"{LINK_GROUP}"'),
        ("session.suspend-timeout-seconds", "0"),
    ]
    playback = [
        ("node.name", f'"{NODE_NAME_OUT}"'),
        ("node.link-group", f'"{LINK_GROUP}"'),
        # node.passive keeps the chain from forcing the hardware to stay awake
        # even though the node itself is never suspended.
        ("node.passive", "true"),
        ("session.suspend-timeout-seconds", "0"),
    ]

    if mode == MODE_SMART:
        # filter.smart.target is deliberately left out: with no target the filter
        # follows whatever the default output is. When the user pins a specific
        # device, the application writes it at runtime through the "filters"
        # metadata, so the chain never has to be reloaded.
        capture += [("filter.smart", "true"), ("filter.smart.name", f'"{SMART_NAME}"')]
    else:
        # node.virtual = false so it shows up as an ordinary output device in
        # GNOME's sound settings rather than being hidden as an internal node.
        #
        # Deliberately no priority.session: raising it makes WirePlumber also pick
        # this node's monitor as the default *source*, displacing the user's
        # microphone. Aqualizer is made the default output explicitly through
        # metadata, so a high priority is not needed.
        capture += [("node.virtual", "false")]
        if target:
            playback.append(("target.object", f'"{target}"'))

    return _block(capture), _block(playback)


def render(
    mode: str = MODE_SMART,
    target: str | None = None,
    gains=None,
    preamp_linear: float = 1.0,
) -> str:
    """Produce the contents of ``99-aqualizer.conf``."""
    if mode not in (MODE_SMART, MODE_SINK):
        raise ValueError(f"unknown mode: {mode!r}")
    gains = [0.0] * len(BANDS) if gains is None else list(gains)
    capture, playback = _render_props(mode, target)
    return f"""# Generated by {APP_NAME} {VERSION} — do not edit by hand.
# This file is read by PipeWire's filter-chain.service when it starts.
# Insertion mode: {mode}
#
# The gain values below are always kept in sync with the active preset. While the
# chain is running, changes are sent through `pw-cli set-param <id> Props` so that
# audio is never interrupted; the values in this file are what a freshly loaded
# chain starts from.
context.modules = [
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "{APP_NAME}"
            media.name       = "{APP_NAME}"
            audio.channels   = 2
            audio.position   = [ FL FR ]
            filter.graph = {{
                nodes = [
{_render_nodes(gains, preamp_linear)}
                ]
                links = [
{_render_links()}
                ]
            }}
            capture.props = {{
{capture}
            }}
            playback.props = {{
{playback}
            }}
        }}
    }}
]
"""
