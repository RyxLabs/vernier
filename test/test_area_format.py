# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pure area formatting helper. See README.md in this folder for how to run these."""

import os
import sys
import unittest

# make the plugins folder importable so the package-relative imports resolve
_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from vernier.tools.area_readout import (  # noqa: E402
    SECONDARY_MODES, UNIT_MODES, format_area, resolve_units,
)


class TestFormatArea(unittest.TestCase):

    def test_auto_small_stays_m2(self):
        self.assertEqual(format_area(123.456, "auto"), "123.46 m²")

    def test_auto_just_below_hectare_threshold(self):
        self.assertEqual(format_area(9999.99, "auto"), "9999.99 m²")

    def test_auto_switches_to_ha_at_one_hectare(self):
        self.assertEqual(format_area(10_000.0, "auto"), "1.0000 ha")

    def test_auto_mid_range_ha(self):
        self.assertEqual(format_area(25_000.0, "auto"), "2.5000 ha")

    def test_auto_switches_to_km2_at_100_ha(self):
        self.assertEqual(format_area(1_000_000.0, "auto"), "1.0000 km²")

    def test_auto_large_km2(self):
        self.assertEqual(format_area(2_500_000.0, "auto"), "2.5000 km²")

    def test_auto_zero(self):
        self.assertEqual(format_area(0.0, "auto"), "0.00 m²")

    def test_explicit_m2(self):
        self.assertEqual(format_area(1234.5, "m2"), "1234.50 m²")

    def test_explicit_ha(self):
        self.assertEqual(format_area(5000.0, "ha"), "0.5000 ha")

    def test_explicit_km2(self):
        self.assertEqual(format_area(5000.0, "km2"), "0.0050 km²")

    def test_one_acre(self):
        self.assertEqual(format_area(4046.8564224, "acres"), "1.0000 ac")

    def test_one_square_foot(self):
        self.assertEqual(format_area(0.09290304, "ft2"), "1.00 ft²")

    def test_unknown_unit_falls_back_to_m2(self):
        self.assertEqual(format_area(50.0, "furlongs"), "50.00 m²")

    def test_default_mode_is_auto(self):
        self.assertEqual(format_area(25_000.0), "2.5000 ha")

    def test_unit_modes_cover_settings_choices(self):
        self.assertEqual(
            UNIT_MODES, ("auto", "m2", "ha", "km2", "acres", "ft2"))

    def test_settings_dialog_units_match_readout(self):
        # the settings combo indexes map positionally onto UNIT_MODES, this locks the two tuples together
        from vernier.dialogs.settings_dialog import _AREA_UNITS
        self.assertEqual(_AREA_UNITS, UNIT_MODES)

    def test_secondary_defaults_off_for_the_bare_helper(self):
        # the setting defaults to "auto", the function doesn't - a caller wanting a second figure has to ask
        self.assertEqual(format_area(2_500_000.0, "auto"), "2.5000 km²")


class TestSecondaryUnit(unittest.TestCase):

    def test_explicit_secondary_appends_in_parentheses(self):
        self.assertEqual(format_area(1_234_500.0, "km2", "ha"),
                         "1.2345 km² (123.4500 ha)")

    def test_auto_pairs_km2_with_ha(self):
        self.assertEqual(format_area(2_500_000.0, "auto", "auto"),
                         "2.5000 km² (250.0000 ha)")

    def test_auto_pairs_ha_with_m2(self):
        self.assertEqual(format_area(25_000.0, "auto", "auto"),
                         "2.5000 ha (25000.00 m²)")

    def test_auto_pairs_m2_with_ha(self):
        # ha is the cadastral companion for m², and there's no size floor so a sub-hectare parcel still gets its second figure
        self.assertEqual(format_area(850.0, "auto", "auto"),
                         "850.00 m² (0.0850 ha)")

    def test_auto_pairs_m2_with_ha_for_a_small_parcel(self):
        self.assertEqual(format_area(250.0, "auto", "auto"),
                         "250.00 m² (0.0250 ha)")

    def test_explicit_m2_primary_takes_the_auto_companion(self):
        # the same rule when only the *secondary* combo is on auto
        self.assertEqual(format_area(9999.0, "m2", "auto"),
                         "9999.00 m² (0.9999 ha)")

    def test_auto_pairs_acres_with_ha(self):
        self.assertEqual(format_area(4046.8564224, "acres", "auto"),
                         "1.0000 ac (0.4047 ha)")

    def test_auto_pairs_ft2_with_acres(self):
        self.assertEqual(format_area(4046.8564224, "ft2", "auto"),
                         "43560.00 ft² (1.0000 ac)")

    def test_same_unit_twice_is_collapsed(self):
        self.assertEqual(format_area(5000.0, "ha", "ha"), "0.5000 ha")

    def test_auto_primary_resolving_onto_the_secondary_is_collapsed(self):
        # "auto" lands on ha here, so an explicit ha secondary is a dup
        self.assertEqual(format_area(25_000.0, "auto", "ha"), "2.5000 ha")

    def test_none_secondary_is_a_single_figure(self):
        self.assertEqual(format_area(1_234_500.0, "km2", "none"),
                         "1.2345 km²")

    def test_unknown_secondary_is_ignored(self):
        self.assertEqual(format_area(1_234_500.0, "km2", "furlongs"),
                         "1.2345 km²")

    def test_unknown_primary_still_takes_a_secondary(self):
        self.assertEqual(format_area(25_000.0, "furlongs", "ha"),
                         "25000.00 m² (2.5000 ha)")

    def test_secondary_modes_cover_settings_choices(self):
        self.assertEqual(
            SECONDARY_MODES,
            ("none", "auto", "m2", "ha", "km2", "acres", "ft2"))

    def test_settings_dialog_secondary_matches_readout(self):
        from vernier.dialogs.settings_dialog import _AREA_UNITS_SECONDARY
        self.assertEqual(_AREA_UNITS_SECONDARY, SECONDARY_MODES)

    def test_every_secondary_mode_renders(self):
        # no mode may raise, whatever the magnitude
        for sqm in (0.0, 850.0, 25_000.0, 2_500_000.0):
            for mode in SECONDARY_MODES:
                with self.subTest(sqm=sqm, mode=mode):
                    self.assertTrue(format_area(sqm, "auto", mode))


class TestResolveUnits(unittest.TestCase):

    def test_auto_resolves_by_magnitude(self):
        self.assertEqual(resolve_units(850.0, "auto"), "m2")
        self.assertEqual(resolve_units(25_000.0, "auto"), "ha")
        self.assertEqual(resolve_units(2_500_000.0, "auto"), "km2")

    def test_explicit_unit_passes_through(self):
        self.assertEqual(resolve_units(25_000.0, "acres"), "acres")

    def test_unknown_unit_falls_back_to_m2(self):
        self.assertEqual(resolve_units(25_000.0, "furlongs"), "m2")


if __name__ == "__main__":
    unittest.main()
