"""Tests for heat_planner (standard library only).

Run from the repo root: python3 -m unittest discover -s planner -t planner
"""

import contextlib
import io
import json
import math
import os
import tempfile
import unittest
from pathlib import Path

import heat_planner as hp

SAMPLE = Path(__file__).resolve().parent.parent / "sample_env_params.json"


def result(rh, wb, stamps=None, tz=None):
    meta = {"timestamps": stamps} if stamps else {}
    if tz:
        meta["timezone"] = tz
    return {
        "metadata": meta,
        "locations": [{"parameters": {"relative_humidity_percent": rh, "wet_bulb_temperature_celsius": wb}}],
    }


class Formulas(unittest.TestCase):
    def test_stull_reference_point(self):
        # Stull (2011) worked example: T = 20 °C, RH = 50 % -> Tw ≈ 13.7 °C
        self.assertAlmostEqual(hp.stull_wet_bulb(20, 50), 13.7, delta=0.1)

    def test_inversion_against_an_independent_psychrometric_value(self):
        # Psychrometric chart at sea level: 30 °C dry bulb, 50 % RH -> wet bulb ≈ 22.2 °C
        self.assertAlmostEqual(hp.air_temp_from_wet_bulb(22.2, 50), 30, delta=0.7)

    def test_inversion_round_trip(self):
        for t, rh in [(20, 50), (35, 25), (38.5, 24.9), (30, 80), (42, 12)]:
            with self.subTest(t=t, rh=rh):
                self.assertAlmostEqual(hp.air_temp_from_wet_bulb(hp.stull_wet_bulb(t, rh), rh), t, delta=0.01)

    def test_inversion_rejects_out_of_range_inputs(self):
        with self.assertRaises(ValueError):
            hp.air_temp_from_wet_bulb(20, 2)  # RH below Stull's 5 %
        with self.assertRaises(ValueError):
            hp.air_temp_from_wet_bulb(35, 20)  # would need an air temperature above 50 °C

    def test_heat_index_matches_nws_chart(self):
        # NWS heat index chart (weather.gov/safety/heat-index); the chart is rounded.
        for t_f, rh, expected in [(90, 50, 96), (96, 65, 121), (100, 40, 109), (86, 90, 105), (80, 40, 80)]:
            with self.subTest(t_f=t_f, rh=rh):
                self.assertAlmostEqual(hp.heat_index_f(t_f, rh), expected, delta=2)

    def test_low_humidity_adjustment(self):
        # Rothfusz at 100 °F / 8 % is 94.46 °F; the NWS adjustment subtracts (13-8)/4 * sqrt(12/17) = 1.05
        self.assertAlmostEqual(hp.heat_index_f(100, 8), 94.462 - 1.25 * math.sqrt(12 / 17), delta=0.01)

    def test_bands(self):
        for hi_f, label in [
            (79, "Lower risk"),
            (85, "Caution"),
            (95, "Extreme caution"),
            (110, "Danger"),
            (130, "Extreme danger"),
        ]:
            with self.subTest(hi_f=hi_f):
                self.assertEqual(hp.band(hi_f), label)


class Inputs(unittest.TestCase):
    def test_load_result_accepts_rest_mcp_and_bare_shapes(self):
        bare = result([50], [22.2])
        for wrapped in (bare, {"body": {"data": {"result": bare}}}, {"structuredContent": {"result": {"value": bare}}}):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(wrapped, f)
            try:
                self.assertEqual(hp.load_result(Path(f.name)), bare)
            finally:
                os.unlink(f.name)

    def test_missing_and_out_of_range_hours_are_skipped(self):
        hours = hp.plan_hours(result([50, -999, 2, 50], [22.2, 20, 20, None]))
        self.assertEqual([h.hour for h in hours], [0])

    def test_offset_timestamps_are_local_and_only_the_first_day_is_used(self):
        stamps = ["2025-08-18T23:00:00-05:00", "2025-08-19T00:00:00-05:00"]
        hours = hp.plan_hours(result([50, 50], [22.2, 22.2], stamps, "America/Chicago"))
        self.assertEqual([h.hour for h in hours], [23])

    def test_utc_timestamps_convert_to_the_given_timezone(self):
        try:
            hp.ZoneInfo("America/Phoenix")
        except hp.ZoneInfoNotFoundError:
            self.skipTest("no tz database (pip install tzdata)")
        hours = hp.plan_hours(result([50], [22.2], ["2025-07-09T22:00:00Z"], "America/Phoenix"))
        self.assertEqual(hours[0].hour, 15)

    def test_empty_input_renders_a_message(self):
        self.assertIn("No usable hours", hp.render([], None, 6, 19, 4))


class Cli(unittest.TestCase):
    def test_rejects_impossible_windows(self):
        for args in (["--hours", "0"], ["--start", "19", "--end", "6"], ["--hours", "20"]):
            with self.subTest(args=args), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                hp.main([str(SAMPLE), *args])


class SyntheticDay(unittest.TestCase):
    def setUp(self):
        self.result = hp.load_result(SAMPLE)
        self.hours = hp.plan_hours(self.result)

    def test_recovers_the_synthetic_air_temperature(self):
        # Circular by design (the sample is built with Stull), so this checks the inversion and rounding only.
        truth = self.result["locations"][0]["parameters"]["synthetic_air_temperature_celsius"]
        for h in self.hours:
            self.assertAlmostEqual(h.air_temp_c, truth[h.hour], delta=0.25)

    def test_heat_index_peaks_mid_afternoon(self):
        self.assertTrue(13 <= max(self.hours, key=lambda h: h.heat_index_c).hour <= 16)

    def test_best_window_is_the_morning(self):
        self.assertEqual(hp.best_window(self.hours, 6, 19, 4), (6, 10))

    def test_cli_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hp.main([str(SAMPLE), "--json"])
        self.assertEqual(json.loads(buf.getvalue())["window"], [6, 10])


if __name__ == "__main__":
    unittest.main(verbosity=2)
