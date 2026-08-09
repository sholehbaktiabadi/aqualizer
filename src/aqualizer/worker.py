"""Background thread for work that touches PipeWire.

Every ``pw-dump``/``pw-cli`` call spawns a process and costs tens of milliseconds.
On the main thread that is enough to make sliders stutter, so it all moves to a
single worker thread.

Apply requests coalesce on their own: dragging a slider marks the state dirty many
times over, but the worker only ever reads the latest state each pass.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import GLib

from . import state as state_mod
from .engine import Engine

#: How often devices are re-checked. Frequent enough that connecting or removing
#: a Bluetooth headset feels immediate, sparse enough not to spawn processes
#: needlessly.
POLL_SECONDS = 3.0


class Worker:
    def __init__(
        self,
        engine: Engine,
        on_error: Callable[[Exception], None],
        on_poll: Callable[[], None],
        on_reload: Callable[[], None],
    ):
        self._engine = engine
        self._on_error = on_error
        self._on_poll = on_poll
        self._on_reload = on_reload
        self._cond = threading.Condition()
        self._dirty = False
        self._stopped = False
        self._seen_mtime = state_mod.mtime()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def request_apply(self) -> None:
        """Ask for the current state to be sent to PipeWire, coalescing bursts."""
        with self._cond:
            self._dirty = True
            self._cond.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cond:
                if not self._dirty and not self._stopped:
                    self._cond.wait(timeout=POLL_SECONDS)
                if self._stopped:
                    return
                dirty, self._dirty = self._dirty, False

            try:
                if dirty:
                    state_mod.save(self._engine.state)
                    self._engine.apply()
                    self._seen_mtime = state_mod.mtime()
                elif self._adopt_external_changes():
                    GLib.idle_add(self._on_reload)
                else:
                    # Periodic check: the device may have changed on its own (a
                    # Bluetooth headset connecting, HDMI being unplugged).
                    # apply() already knows when to switch modes and when to do
                    # nothing at all.
                    self._engine.apply()
                    GLib.idle_add(self._on_poll)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user as a toast
                GLib.idle_add(self._on_error, exc)

    def _adopt_external_changes(self) -> bool:
        """Pick up state written by another process, rather than overwriting it.

        Without this the window would keep pushing its own stale copy of the
        state every few seconds, silently undoing anything done meanwhile with
        ``aqualizer set`` in a terminal.
        """
        current = state_mod.mtime()
        if current is None or current == self._seen_mtime:
            return False
        self._seen_mtime = current
        loaded = state_mod.load()
        self._engine.state.__dict__.update(loaded.__dict__)
        return True
