# Aqualizer

A ten-band equalizer for PipeWire audio output, with ready-made presets (Bass,
Vocal, Acoustic, Night and more), per-band manual sliders, and presets of your
own. Ships as a GTK4/libadwaita application and a command line tool.

All signal processing uses PipeWire's **built-in** biquad filters
(`bq_lowshelf`, `bq_peaking`, `bq_highshelf`) — no LADSPA, LV2 or any other DSP
library to install.

## App Preview

<div align="center">
  <img
    width="432"
    height="638"
    alt="image"
    src="https://github.com/user-attachments/assets/59aff57b-a5de-465e-b6c9-85ac2fd1c365"
  />
</div>


## What sets it apart

- **Switch presets without interrupting music.** Gain values are sent to the
  running graph through `pw-cli set-param` rather than reloading the chain.
- **Follows your devices.** Moving between speakers and a Bluetooth headset
  needs no reconfiguration.
- **Automatic preamp.** When bass is boosted, the level drops by just enough to
  avoid clipping.
- **Removes itself cleanly.** One command restores your audio configuration
  exactly as it was.

## Installing

```bash
sudo apt install ./aqualizer_0.1.0_all.deb
```

Building from source:

```bash
sudo apt install debhelper dh-python pybuild-plugin-pyproject python3-all python3-setuptools
dpkg-buildpackage -us -uc -b
sudo apt install ../aqualizer_0.1.0_all.deb
```

If debhelper is not available on that machine, `tools/build-deb.sh` produces a
package with the same contents without needing any extra build packages:

```bash
./tools/build-deb.sh
sudo apt install ./aqualizer_0.1.0_all.deb
```

The systemd user unit is enabled at install time but only starts with your next
session. To activate it right away:

```bash
systemctl --user daemon-reload
systemctl --user enable --now aqualizer.service
```

## Using it

Open **Aqualizer** from your application menu, or from a terminal:

```bash
aqualizer status          # current state, with a bar chart of every band
aqualizer list            # list presets
aqualizer set bass        # apply a preset
aqualizer off             # bypass the equalizer
aqualizer devices         # list output devices
aqualizer device auto     # follow the default device
aqualizer bands 6 5 3 0 -1 -1 0 2 4 6
aqualizer save "Late Night"
aqualizer uninstall       # remove it from the audio path
```

## How it works

```
Applications (Chrome, Spotify, …)
        ↓
  aqualizer_eq  [Audio/Sink]
        ↓
  preamp → 31 Hz → 63 → 125 → 250 → 500 → 1k → 2k → 4k → 8k → 16 kHz
        ↓
  Real device (speakers / Bluetooth)
```

The chain is defined in `~/.config/pipewire/filter-chain.conf.d/99-aqualizer.conf`
and loaded by PipeWire's own `filter-chain.service` — there is no extra daemon.

### Two insertion modes

Aqualizer picks between them on its own and switches automatically when the
device changes.

| Mode | Used for | How it works |
|---|---|---|
| `smart` | Speakers, HDMI, USB, analogue | The node is marked `filter.smart` and WirePlumber inserts it. The real device stays visible in the sound settings. |
| `sink` | Bluetooth | The node becomes a virtual sink that is made the default output, with its own output pinned to the real device. |

Sink mode is necessary for Bluetooth because WirePlumber creates Bluetooth sinks
as smart filters of their own (`bluez.lua`). During target selection,
`filter-utils.lua` always resolves to those first, so another filter would never
be traversed. Forcing an earlier insertion is possible, but it bypasses the node
that holds the device's volume control — hence the virtual sink route instead.

### When the chain gets reloaded

Almost never. Gain values are written in two places: to the configuration file
(what a loading chain starts from) and to the running graph via
`pw-cli set-param` (which takes effect instantly). A reload only happens when the
mode changes, or when the node has never been active — and in that case there is
no audio to interrupt anyway, because a `suspended` PipeWire node discards Props
changes without reporting an error.

## Requirements

- PipeWire 1.0 or newer, plus `pipewire-bin`
- WirePlumber
- For the GUI: `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`

## Tested on

| | |
|---|---|
| Distribution | Ubuntu 26.04 LTS (Resolute Raccoon), amd64 |
| Desktop | GNOME Shell 50.1 on Wayland |
| Audio stack | PipeWire 1.6.2, WirePlumber 0.5.13 |
| Toolkit | Python 3.14.4, GTK 4.22.2, libadwaita 1.9.0 |
| Output devices | Intel Alder Lake PCH-P HD Audio — laptop speakers and three HDMI/DisplayPort outputs · Baseus Bass BP1 Pro over Bluetooth (A2DP) |

What was exercised on that machine:

- Both insertion modes, and the automatic switch between them when moving
  between laptop speakers and the Bluetooth headset
- Presets switching during playback without a chain reload or an audible gap
- Gain values read back from the running DSP graph and compared against each
  preset's definition
- Bypass in both modes, and `uninstall` restoring the previous default output
- A `aqualizer set …` run from a terminal while the window is open, with the
  window following along instead of overwriting it
- The default microphone staying untouched while the equalizer is installed

### Not yet tested

Reports from any of these are welcome:

- Distributions other than Ubuntu — Debian, Fedora and the rest
- X11 sessions, and desktops other than GNOME
- Surround output. The filter chain is fixed at stereo
  (`audio.channels = 2`), so a 5.1 or 7.1 sink is not handled yet
- USB DACs and HDMI as the active output. HDMI sinks were listed and
  selectable, but no audio was played through one
- PipeWire versions other than 1.6.x

## Development

```bash
python3 -m unittest discover -s tests -v   # 34 tests, no PipeWire needed
PYTHONPATH=src python3 -m aqualizer        # run the GUI from the source tree
PYTHONPATH=src python3 -m aqualizer status
```

## License

GPL-3.0-or-later.
