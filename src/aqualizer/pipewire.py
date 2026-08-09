"""A thin layer over PipeWire's command line tools.

PipeWire ships no official Python bindings, so everything goes through
``pw-dump`` / ``pw-cli`` / ``pw-metadata``, the same way other PipeWire tooling
does. All three come from the ``pipewire-bin`` package.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass

from .const import NODE_NAME, NODE_NAME_OUT, pipewire_conf_dir, pipewire_conf_path
from .graph import MODE_SINK, MODE_SMART, PREAMP_KEY, gain_key

FILTER_CHAIN_UNIT = "filter-chain.service"

#: Nodes with these media classes are the inner side of a loopback (a Bluetooth
#: internal sink, for instance) and are never picked by the user directly.
INTERNAL_CLASSES = ("Audio/Sink/Internal",)


class PipeWireError(RuntimeError):
    pass


@dataclass(frozen=True)
class Sink:
    id: int
    name: str
    description: str
    #: True when this node is itself a WirePlumber smart filter. In practice that
    #: means a Bluetooth sink, which bluez.lua creates as a loopback carrying
    #: filter.smart. Aqualizer cannot insert itself ahead of one without breaking
    #: that device's volume control, so such devices require "sink" mode.
    smart_filter: bool = False


def _run(args: list[str], *, check: bool = True, timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise PipeWireError(f"{args[0]} not found — install the pipewire-bin package") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipeWireError(f"{args[0]} did not respond") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise PipeWireError(f"{' '.join(args)} failed: {detail}")
    return proc.stdout


def available() -> bool:
    return all(shutil.which(tool) for tool in ("pw-dump", "pw-cli", "pw-metadata"))


# --------------------------------------------------------------------------- #
# Reading graph state
# --------------------------------------------------------------------------- #


def dump() -> list[dict]:
    try:
        data = json.loads(_run(["pw-dump"]) or "[]")
    except ValueError as exc:
        raise PipeWireError("could not parse pw-dump output") from exc
    return data if isinstance(data, list) else []


def _node_props(objs: list[dict]):
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        yield obj.get("id"), props


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def find_nodes(objs: list[dict] | None = None) -> tuple[int | None, int | None]:
    """Return (id of the Aqualizer sink node, id of its output stream node)."""
    objs = dump() if objs is None else objs
    capture = playback = None
    for node_id, props in _node_props(objs):
        name = props.get("node.name")
        if name == NODE_NAME:
            capture = node_id
        elif name == NODE_NAME_OUT:
            playback = node_id
    return capture, playback


def wait_for_nodes(timeout: float = 10.0) -> tuple[int, int]:
    """Wait for the chain to appear after PipeWire has been restarted."""
    deadline = time.monotonic() + timeout
    delay = 0.15
    while True:
        capture, playback = find_nodes()
        if capture is not None and playback is not None:
            return capture, playback
        if time.monotonic() >= deadline:
            raise PipeWireError(
                f"node '{NODE_NAME}' did not appear within {timeout:.0f} seconds — "
                f"check `systemctl --user status {FILTER_CHAIN_UNIT}`"
            )
        time.sleep(delay)
        delay = min(delay * 1.5, 1.0)


def node_state(node_id: int | None, objs: list[dict] | None = None) -> str | None:
    """Node state: suspended / idle / running / error.

    The difference matters. A ``suspended`` node has no DSP graph instantiated,
    and ``pw-cli set-param Props`` against it is accepted and then discarded
    without any error being reported.
    """
    if node_id is None:
        return None
    objs = dump() if objs is None else objs
    for obj in objs:
        if obj.get("id") == node_id and obj.get("type") == "PipeWire:Interface:Node":
            return (obj.get("info") or {}).get("state")
    return None


def list_sinks(objs: list[dict] | None = None) -> list[Sink]:
    """Output devices the user can choose, excluding Aqualizer's own nodes."""
    objs = dump() if objs is None else objs
    sinks = []
    for node_id, props in _node_props(objs):
        media_class = props.get("media.class", "")
        name = props.get("node.name", "")
        if media_class != "Audio/Sink" or media_class in INTERNAL_CLASSES:
            continue
        if name in (NODE_NAME, NODE_NAME_OUT):
            continue
        sinks.append(
            Sink(
                id=node_id,
                name=name,
                description=props.get("node.description") or name,
                smart_filter=bool(props.get("node.link-group"))
                and _as_bool(props.get("filter.smart", False)),
            )
        )
    sinks.sort(key=lambda s: s.description.lower())
    return sinks


def default_sink_name(objs: list[dict] | None = None) -> str | None:
    objs = dump() if objs is None else objs
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Metadata":
            continue
        if (obj.get("props") or {}).get("metadata.name") != "default":
            continue
        for entry in obj.get("metadata") or []:
            if entry.get("key") == "default.audio.sink":
                value = entry.get("value")
                if isinstance(value, dict):
                    return value.get("name")
                if isinstance(value, str):
                    try:
                        return json.loads(value).get("name")
                    except ValueError:
                        return value
    return None


def find_sink(name: str | None, objs: list[dict] | None = None) -> Sink | None:
    if not name:
        return None
    for sink in list_sinks(objs):
        if sink.name == name:
            return sink
    return None


def recommended_mode(sink: Sink | None) -> str:
    """The insertion mode that suits a given target device.

    A sink that is itself a smart filter — Bluetooth being the real-world case —
    always wins WirePlumber's target selection
    (``filter-utils.lua``: ``get_filter_from_target``), so Aqualizer's filter
    would never be traversed. Devices like that use the virtual sink mode.
    """
    if sink is not None and sink.smart_filter:
        return MODE_SINK
    return MODE_SMART


# --------------------------------------------------------------------------- #
# Writing graph state at runtime
# --------------------------------------------------------------------------- #


def db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def auto_preamp_db(gains) -> float:
    """Just enough attenuation to keep the largest band boost from clipping."""
    return -max(0.0, max(gains, default=0.0))


def apply_gains(node_id: int, gains, preamp_db: float) -> None:
    """Send every band value and the preamp in a single command.

    Controls are shared across channels, so one write sets both left and right.
    """
    params = [f'"{PREAMP_KEY}" {db_to_linear(preamp_db):.6f}']
    params += [f'"{gain_key(i)}" {float(g):.4f}' for i, g in enumerate(gains)]
    _run(["pw-cli", "set-param", str(node_id), "Props", f"{{ params = [ {' '.join(params)} ] }}"])


def read_gains(node_id: int) -> dict[str, float]:
    """Read control values back from the running graph (used for verification)."""
    out = _run(["pw-cli", "enum-params", str(node_id), "Props"])
    values: dict[str, float] = {}
    pending: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('String "'):
            pending = line[8:].rstrip('"')
        elif pending and line.startswith("Float "):
            try:
                values[pending] = float(line.split(None, 1)[1])
            except ValueError:
                pass
            pending = None
    return values


_MISSING = object()


def metadata_value(objs: list[dict], name: str, subject: int, key: str):
    """The metadata value currently in effect, or ``_MISSING`` if unset."""
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Metadata":
            continue
        if (obj.get("props") or {}).get("metadata.name") != name:
            continue
        for entry in obj.get("metadata") or []:
            if entry.get("subject") == subject and entry.get("key") == key:
                return entry.get("value")
    return _MISSING


def _metadata_set(
    node_id: int,
    key: str,
    value: str | None,
    name: str = "default",
    objs: list[dict] | None = None,
) -> None:
    """Write metadata, skipping the write when the value is already correct.

    Skipping redundant writes is not just an optimisation: every metadata change
    makes WirePlumber rescan the whole graph, and the application's periodic check
    would otherwise trigger that every few seconds.
    """
    if objs is not None:
        current = metadata_value(objs, name, node_id, key)
        wanted = json.loads(value) if value is not None else _MISSING
        if current == wanted:
            return

    if value is None:
        _run(["pw-metadata", "-n", name, "-d", str(node_id), key], check=False)
    else:
        _run(["pw-metadata", "-n", name, str(node_id), key, value, "Spa:String:JSON"])


def set_smart_disabled(node_id: int, disabled: bool, objs: list[dict] | None = None) -> None:
    """Detach the filter from the audio path, or put it back (smart mode only)."""
    _metadata_set(
        node_id, "filter.smart.disabled", "true" if disabled else "false", "filters", objs
    )


def set_smart_target(node_id: int, sink_name: str | None, objs: list[dict] | None = None) -> None:
    """Pin the filter to one device, or None to follow the default output."""
    value = json.dumps({"node.name": sink_name}) if sink_name else None
    _metadata_set(node_id, "filter.smart.target", value, "filters", objs)


def set_stream_target(node_id: int, sink_name: str | None, objs: list[dict] | None = None) -> None:
    """Point the output stream at a specific device (sink mode only)."""
    value = json.dumps(sink_name) if sink_name else None
    _metadata_set(node_id, "target.object", value, "default", objs)


def set_default_sink(name: str) -> None:
    """Make a sink the default output.

    What gets written is ``default.configured.audio.sink`` — the user-choice key
    that ``wpctl set-default`` also uses. Writing ``default.audio.sink`` alone does
    not stick: WirePlumber immediately recomputes that value from the configured
    key and overwrites whatever we wrote.
    """
    _run(
        [
            "pw-metadata",
            "-n",
            "default",
            "0",
            "default.configured.audio.sink",
            json.dumps({"name": name}),
            "Spa:String:JSON",
        ]
    )


def is_intercepting(objs: list[dict] | None = None) -> bool:
    """True when at least one application stream really passes through Aqualizer.

    Used to verify that the chosen mode actually works, rather than assuming so
    from the configuration.
    """
    objs = dump() if objs is None else objs
    capture_id, _ = find_nodes(objs)
    if capture_id is None:
        return False
    ports = {
        obj["id"]
        for obj in objs
        if obj.get("type") == "PipeWire:Interface:Port"
        and ((obj.get("info") or {}).get("props") or {}).get("node.id") == capture_id
    }
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        if ((obj.get("info") or {}).get("props") or {}).get("link.input.port") in ports:
            return True
    return False


# --------------------------------------------------------------------------- #
# Configuration file and chain lifecycle
# --------------------------------------------------------------------------- #


def config_installed() -> bool:
    return pipewire_conf_path().is_file()


def write_config(text: str) -> bool:
    """Write the configuration; return True when the contents actually changed."""
    path = pipewire_conf_path()
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    pipewire_conf_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def remove_config() -> bool:
    path = pipewire_conf_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def restart_chain() -> None:
    proc = subprocess.run(
        ["systemctl", "--user", "restart", FILTER_CHAIN_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PipeWireError(
            f"could not reload {FILTER_CHAIN_UNIT}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )


__all__ = [
    "MODE_SINK",
    "MODE_SMART",
    "PipeWireError",
    "Sink",
    "apply_gains",
    "auto_preamp_db",
    "available",
    "config_installed",
    "db_to_linear",
    "default_sink_name",
    "dump",
    "find_nodes",
    "find_sink",
    "is_intercepting",
    "list_sinks",
    "metadata_value",
    "node_state",
    "read_gains",
    "recommended_mode",
    "remove_config",
    "restart_chain",
    "set_default_sink",
    "set_smart_disabled",
    "set_smart_target",
    "set_stream_target",
    "wait_for_nodes",
    "write_config",
]
