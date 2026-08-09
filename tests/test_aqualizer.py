"""Tests for the pure-logic parts — nothing here touches PipeWire."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aqualizer import graph, pipewire, presets, state  # noqa: E402
from aqualizer.const import GAIN_MAX_DB, GAIN_MIN_DB, N_BANDS  # noqa: E402
from aqualizer.engine import Engine  # noqa: E402


class IsolatedConfig(unittest.TestCase):
    """Point XDG_CONFIG_HOME at a temporary directory for the duration of a test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._saved
        self._tmp.cleanup()


class TestPresets(unittest.TestCase):
    def test_every_builtin_preset_has_ten_bands(self):
        for preset in presets.BUILTIN_PRESETS:
            with self.subTest(preset=preset.id):
                self.assertEqual(len(preset.gains), N_BANDS)

    def test_builtin_ids_are_unique(self):
        ids = [p.id for p in presets.BUILTIN_PRESETS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_builtin_gains_stay_within_safe_limits(self):
        for preset in presets.BUILTIN_PRESETS:
            for gain in preset.gains:
                self.assertGreaterEqual(gain, GAIN_MIN_DB)
                self.assertLessEqual(gain, GAIN_MAX_DB)

    def test_default_preset_exists_and_is_flat(self):
        default = presets.find(presets.DEFAULT_ID)
        self.assertIsNotNone(default)
        self.assertEqual(default.gains, presets.FLAT)

    def test_clamp_trims_and_pads(self):
        self.assertEqual(presets.clamp_gains([99, -99]), (GAIN_MAX_DB, GAIN_MIN_DB) + (0.0,) * 8)
        self.assertEqual(presets.clamp_gains([]), (0.0,) * N_BANDS)
        self.assertEqual(presets.clamp_gains(["not a number"] * N_BANDS), (0.0,) * N_BANDS)

    def test_slugify(self):
        self.assertEqual(presets.slugify("Late Night"), "late-night")
        self.assertEqual(presets.slugify("  ???  "), "preset")

    def test_find_accepts_id_or_name(self):
        self.assertEqual(presets.find("bass-light").id, "bass-light")
        self.assertEqual(presets.find("Light Bass").id, "bass-light")
        self.assertIsNone(presets.find("no such thing"))


class TestUserPresets(IsolatedConfig):
    def test_save_then_read_back(self):
        gains = [1.0, -2.0, 3.0, 0, 0, 0, 0, 0, 0, 4.0]
        saved = presets.save_user_preset("Test Drive", gains)
        self.assertFalse(saved.builtin)
        loaded = presets.find("test-drive")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.gains, tuple(float(g) for g in gains))
        self.assertEqual(loaded.name, "Test Drive")

    def test_does_not_shadow_a_builtin(self):
        saved = presets.save_user_preset("Bass", presets.FLAT)
        self.assertEqual(saved.id, "bass-user")
        self.assertEqual(presets.find("bass").gains, presets.BUILTIN_PRESETS[1].gains)

    def test_delete(self):
        saved = presets.save_user_preset("Temporary", presets.FLAT)
        self.assertTrue(presets.delete_user_preset(saved))
        self.assertIsNone(presets.find("temporary"))

    def test_builtins_cannot_be_deleted(self):
        self.assertFalse(presets.delete_user_preset(presets.find(presets.DEFAULT_ID)))

    def test_broken_files_are_skipped(self):
        directory = Path(os.environ["XDG_CONFIG_HOME"]) / "aqualizer" / "presets"
        directory.mkdir(parents=True)
        (directory / "broken.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(presets.load_user_presets(), [])


class TestGraph(unittest.TestCase):
    def assertKey(self, text: str, key: str, value: str) -> None:
        """Match a key/value pair without pinning down its alignment width."""
        self.assertRegex(text, rf"(?m)^\s*{re.escape(key)}\s+= {re.escape(value)}\s*$")

    def test_all_bands_and_the_preamp_are_present(self):
        text = graph.render()
        self.assertIn("name = preamp", text)
        for i in range(N_BANDS):
            self.assertIn(f"name = {graph.band_node(i)}", text)
        self.assertEqual(text.count("type = builtin"), N_BANDS + 1)

    def test_chain_is_linked_in_order(self):
        text = graph.render()
        self.assertIn('{ output = "preamp:Out" input = "eq_band_1:In" }', text)
        for i in range(1, N_BANDS):
            self.assertIn(
                f'{{ output = "eq_band_{i}:Out" input = "eq_band_{i + 1}:In" }}', text
            )

    def test_spectrum_edges_use_shelving_filters(self):
        text = graph.render()
        self.assertIn("label = bq_lowshelf", text)
        self.assertIn("label = bq_highshelf", text)

    def test_inputs_and_outputs_are_not_declared(self):
        # Declaring them would lock the chain to mono; PipeWire must be left to
        # duplicate the graph to match the stream's channel count.
        text = graph.render()
        self.assertNotIn("inputs = [", text)
        self.assertNotIn("outputs = [", text)
        self.assertIn("audio.channels   = 2", text)

    def test_smart_mode(self):
        text = graph.render(graph.MODE_SMART)
        self.assertKey(text, "filter.smart", "true")
        self.assertKey(text, "filter.smart.name", '"aqualizer"')
        self.assertNotIn("target.object", text)

    def test_sink_mode(self):
        text = graph.render(graph.MODE_SINK, "test_device")
        self.assertNotIn("filter.smart", text)
        self.assertKey(text, "target.object", '"test_device"')
        self.assertKey(text, "node.virtual", "false")

    def test_sink_mode_sets_no_session_priority(self):
        # Raising it makes WirePlumber pick this node's monitor as the default
        # source, displacing the user's microphone.
        self.assertNotIn("priority.session", graph.render(graph.MODE_SINK, "test_device"))

    def test_suspension_is_disabled_on_both_sides(self):
        # A suspended node discards Props changes without reporting an error.
        self.assertEqual(graph.render().count("session.suspend-timeout-seconds = 0"), 2)

    def test_gain_values_are_written_out(self):
        gains = [8.0, 7.0, 5.0, 2.0, 0.0, -1.0, -1.0, 0.0, 1.0, 2.0]
        text = graph.render(graph.MODE_SMART, None, gains, 0.398107)
        self.assertIn('"Gain" = 8.0000', text)
        self.assertIn('"Gain" = -1.0000', text)
        self.assertIn('"Mult" = 0.398107', text)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            graph.render("whatever")


class TestState(IsolatedConfig):
    def test_round_trip(self):
        original = state.State(
            enabled=False, preset="rock", gains=[1.0] * N_BANDS, target="test_sink"
        )
        state.save(original)
        loaded = state.load()
        self.assertEqual(loaded.enabled, False)
        self.assertEqual(loaded.preset, "rock")
        self.assertEqual(loaded.gains, [1.0] * N_BANDS)
        self.assertEqual(loaded.target, "test_sink")

    def test_missing_file_gives_defaults(self):
        self.assertFalse(state.exists())
        self.assertEqual(state.load().preset, presets.DEFAULT_ID)

    def test_broken_file_gives_defaults(self):
        path = state.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json at all", encoding="utf-8")
        self.assertEqual(state.load().preset, presets.DEFAULT_ID)

    def test_unknown_mode_is_rejected(self):
        path = state.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"mode": "weird"}', encoding="utf-8")
        self.assertEqual(state.load().mode, "smart")

    def test_gains_in_the_file_are_clamped_too(self):
        path = state.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"gains": [999, -999]}', encoding="utf-8")
        self.assertEqual(state.load().gains[0], GAIN_MAX_DB)
        self.assertEqual(state.load().gains[1], GAIN_MIN_DB)


class TestCalculations(unittest.TestCase):
    def test_db_to_linear(self):
        self.assertAlmostEqual(pipewire.db_to_linear(0.0), 1.0)
        self.assertAlmostEqual(pipewire.db_to_linear(-6.0), 0.501187, places=5)
        self.assertAlmostEqual(pipewire.db_to_linear(-20.0), 0.1)

    def test_auto_preamp_offsets_the_largest_boost(self):
        self.assertEqual(pipewire.auto_preamp_db([8.0, 3.0, -2.0]), -8.0)

    def test_auto_preamp_never_boosts(self):
        # Every band cut: the preamp stays at 0 rather than raising the volume.
        self.assertEqual(pipewire.auto_preamp_db([-3.0, -6.0]), 0.0)
        self.assertEqual(pipewire.auto_preamp_db([]), 0.0)

    def test_mode_for_device(self):
        ordinary = pipewire.Sink(1, "alsa_test", "Speaker", smart_filter=False)
        bluetooth = pipewire.Sink(2, "bluez_test", "Headset", smart_filter=True)
        self.assertEqual(pipewire.recommended_mode(ordinary), graph.MODE_SMART)
        self.assertEqual(pipewire.recommended_mode(bluetooth), graph.MODE_SINK)
        self.assertEqual(pipewire.recommended_mode(None), graph.MODE_SMART)

    def test_preset_matching(self):
        self.assertEqual(Engine._match_preset(list(presets.FLAT)), presets.DEFAULT_ID)
        self.assertEqual(Engine._match_preset(list(presets.find("rock").gains)), "rock")
        self.assertEqual(Engine._match_preset([0.5] * N_BANDS), presets.CUSTOM_ID)


class TestMetadataGuard(unittest.TestCase):
    """The periodic check must not rewrite metadata that is already correct."""

    DUMP = [
        {
            "id": 42,
            "type": "PipeWire:Interface:Metadata",
            "props": {"metadata.name": "filters"},
            "metadata": [
                {"subject": 7, "key": "filter.smart.disabled", "value": False},
                {"subject": 7, "key": "filter.smart.target", "value": {"node.name": "spk"}},
            ],
        }
    ]

    def test_reads_existing_values(self):
        self.assertIs(pipewire.metadata_value(self.DUMP, "filters", 7, "filter.smart.disabled"), False)
        self.assertEqual(
            pipewire.metadata_value(self.DUMP, "filters", 7, "filter.smart.target"),
            {"node.name": "spk"},
        )

    def test_unset_keys_and_other_subjects_are_distinct(self):
        missing = pipewire.metadata_value(self.DUMP, "filters", 7, "nope")
        self.assertIsNot(missing, None)
        self.assertIsNot(pipewire.metadata_value(self.DUMP, "filters", 8, "filter.smart.disabled"), False)


if __name__ == "__main__":
    unittest.main()
