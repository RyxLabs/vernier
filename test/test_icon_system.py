# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The icon design system - a 24x24 viewBox on a half-pixel grid, at most two stroke weights, the contrast-checked token palettes, and a dark twin for every light icon. Pure text analysis, so it needs no QgsApplication and runs in milliseconds."""

import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import build_icon_themes, icon_tokens  # noqa: E402

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(PLUGIN_DIR, "icons")

MIGRATED = tuple(sorted(
    name for name in os.listdir(ICONS)
    if name.endswith(".svg") and name != "vernier.svg"))


class TestContrast(unittest.TestCase):
    """The numbers that justify splitting the light and dark sets."""

    def test_light_tokens_clear_3to1_on_default_theme(self):
        for color in (icon_tokens.FG_LIGHT, icon_tokens.AC_LIGHT):
            with self.subTest(color=color):
                self.assertGreaterEqual(
                    icon_tokens.contrast(color, icon_tokens.BG_DEFAULT), 3.0)

    def test_dark_tokens_clear_3to1_on_dark_themes(self):
        for bg in (icon_tokens.BG_NIGHT, icon_tokens.BG_BLACK):
            for color in (icon_tokens.FG_DARK, icon_tokens.AC_DARK):
                with self.subTest(bg=bg, color=color):
                    self.assertGreaterEqual(
                        icon_tokens.contrast(color, bg), 3.0)

    def test_brand_red_fails_on_night_mapping(self):
        # the whole reason the sets are split. if this ever passes, the dark set's #FF4554 substitution stops being necessary
        self.assertLess(
            icon_tokens.contrast(icon_tokens.AC_LIGHT, icon_tokens.BG_NIGHT),
            3.0)

    def test_contrast_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(icon_tokens.contrast("#FFFFFF", "#000000"), 21.0,
                               places=1)
        self.assertAlmostEqual(icon_tokens.contrast("#000000", "#FFFFFF"), 21.0,
                               places=1)
        self.assertAlmostEqual(icon_tokens.contrast("#777777", "#777777"), 1.0,
                               places=6)


class TestSvgHelpers(unittest.TestCase):

    def test_svg_colors_collects_fill_and_stroke(self):
        text = '<rect fill="#272324" stroke="#E6202E"/><line stroke="none"/>'
        self.assertEqual(icon_tokens.svg_colors(text),
                         {"#272324", "#E6202E", "none"})

    def test_svg_colors_ignores_hyphenated_attributes(self):
        text = '<a fill-opacity="0.25" stroke-width="2" stroke-linecap="round"/>'
        self.assertEqual(icon_tokens.svg_colors(text), set())

    def test_svg_stroke_widths_parsed_as_floats(self):
        text = '<a stroke-width="2"/><b stroke-width="1.5"/>'
        self.assertEqual(icon_tokens.svg_stroke_widths(text), {2.0, 1.5})

    def test_grid_offenders_flags_quarter_pixels(self):
        text = '<rect x="1.2" y="2.5" width="3" height="4.75"/>'
        self.assertEqual(sorted(icon_tokens.svg_grid_offenders(text)),
                         ['height="4.75"', 'x="1.2"'])

    def test_grid_offenders_ignores_path_data(self):
        text = '<path d="M 2.667,9.667 a 2.667,2.667 0 0 1 .385,1"/>'
        self.assertEqual(icon_tokens.svg_grid_offenders(text), [])

    def test_grid_offenders_ignores_stroke_width(self):
        # a plain \b lets "stroke-width" match the "width" rule and misreports a bad weight as a grid offence
        text = '<path stroke-width="2.2" d="M 0,0"/>'
        self.assertEqual(icon_tokens.svg_grid_offenders(text), [])

    def test_grid_offenders_checks_points_lists(self):
        text = '<polyline points="3,19 9.25,8"/>'
        self.assertEqual(icon_tokens.svg_grid_offenders(text),
                         ['points="3,19 9.25,8"'])


class TestMigratedLightIcons(unittest.TestCase):
    """Every rule from the spec, applied to each redrawn icon."""

    @staticmethod
    def _read(name):
        with open(os.path.join(ICONS, name), encoding="utf-8") as fp:
            return fp.read()

    def test_only_light_tokens_used(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                extra = icon_tokens.svg_colors(self._read(name)) - \
                    icon_tokens.LIGHT_TOKENS
                self.assertEqual(extra, set())

    def test_no_white_knockouts(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                self.assertNotIn("#ffffff", self._read(name).lower())

    def test_only_two_stroke_weights(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                extra = icon_tokens.svg_stroke_widths(self._read(name)) - \
                    {2.0, 1.5}
                self.assertEqual(extra, set())

    def test_coordinates_on_half_pixel_grid(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                self.assertEqual(
                    icon_tokens.svg_grid_offenders(self._read(name)), [])

    def test_standard_viewbox(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                self.assertIn('viewBox="0 0 24 24"', self._read(name))

    def test_no_halo_group_survives(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                self.assertNotIn('class="halo"', self._read(name))


class TestDarkSet(unittest.TestCase):

    DARK = os.path.join(ICONS, "dark")

    def test_every_light_icon_has_a_dark_twin(self):
        self.assertEqual(
            sorted(n for n in os.listdir(self.DARK) if n.endswith(".svg")),
            list(MIGRATED))

    def test_mark_has_no_dark_twin(self):
        self.assertIn("vernier.svg", build_icon_themes.SKIP)
        self.assertFalse(os.path.exists(
            os.path.join(self.DARK, "vernier.svg")))

    def test_only_dark_tokens_used(self):
        for name in MIGRATED:
            with self.subTest(icon=name):
                with open(os.path.join(self.DARK, name),
                          encoding="utf-8") as fp:
                    extra = icon_tokens.svg_colors(fp.read()) - \
                        icon_tokens.DARK_TOKENS
                self.assertEqual(extra, set())

    def test_checked_in_files_match_a_fresh_regeneration(self):
        # catches anyone hand-editing generated output
        for name in MIGRATED:
            with self.subTest(icon=name):
                with open(os.path.join(ICONS, name), encoding="utf-8") as fp:
                    expected = build_icon_themes.to_dark(fp.read())
                with open(os.path.join(self.DARK, name),
                          encoding="utf-8") as fp:
                    self.assertEqual(fp.read(), expected)

    def test_dark_tokens_beat_light_tokens_on_night_mapping(self):
        for light, dark in ((icon_tokens.FG_LIGHT, icon_tokens.FG_DARK),
                            (icon_tokens.AC_LIGHT, icon_tokens.AC_DARK)):
            with self.subTest(token=dark):
                self.assertGreater(
                    icon_tokens.contrast(dark, icon_tokens.BG_NIGHT),
                    icon_tokens.contrast(light, icon_tokens.BG_NIGHT))


class TestPluginMark(unittest.TestCase):
    """The mark is a tiled logo, so different rules than the toolbar glyphs - only brand colors, and an opaque tile that makes it theme-proof on the Plugin Manager's background."""

    BRAND = {"#141416", "#F0F0F4", "#E6202E", "none"}

    def setUp(self):
        with open(os.path.join(ICONS, "vernier.svg"), encoding="utf-8") as fp:
            self.text = fp.read()

    def test_only_brand_colors(self):
        self.assertEqual(icon_tokens.svg_colors(self.text) - self.BRAND, set())

    def test_opaque_tile_covers_the_viewbox(self):
        self.assertIn('width="24" height="24" rx="5" fill="#141416"',
                      self.text)

    def test_standard_viewbox(self):
        self.assertIn('viewBox="0 0 24 24"', self.text)

    def test_exactly_one_aligned_pair_in_accent(self):
        # the vernier principle - two scales at different pitches coincide exactly once, and that coincidence is the only red in the mark
        self.assertEqual(self.text.count("#E6202E"), 1)

    def test_no_halo_group_survives(self):
        self.assertNotIn('class="halo"', self.text)


class TestResolver(unittest.TestCase):
    """Source-level checks on vernier.py's icon resolution, mirroring the AST convention tests in test_style_templates. No display or running QGIS needed."""

    PATH = os.path.join(PLUGIN_DIR, "vernier.py")

    def setUp(self):
        with open(self.PATH, encoding="utf-8") as fp:
            self.source = fp.read()
        self.tree = ast.parse(self.source, filename="vernier.py")

    def _methods(self):
        return {node.name for node in ast.walk(self.tree)
                if isinstance(node, ast.FunctionDef)}

    def _assert_present(self, needle):
        # assertIn would dump the whole of vernier.py into the failure
        self.assertTrue(needle in self.source,
                        "{0!r} missing from vernier.py".format(needle))

    def _assert_absent(self, needle):
        self.assertFalse(needle in self.source,
                         "{0!r} still present in vernier.py".format(needle))

    def test_resolver_methods_exist(self):
        self.assertIn("_icons_dir", self._methods())
        self.assertIn("_dark_ui", self._methods())

    def test_no_hardcoded_icons_path_remains(self):
        # both QIcon call sites must route through _icons_dir()
        self._assert_absent('"icons", feat.icon')
        self._assert_absent('"icons", group_icon')

    def test_both_detection_paths_present(self):
        # theme name alone misses OS dark mode, palette alone misses QGIS's stylesheet themes, so both have to be there
        self._assert_present("themeName()")
        self._assert_present("ColorRole.Window")

    def test_known_dark_themes_listed(self):
        self._assert_present("night mapping")
        self._assert_present("blend of gray")

    def test_no_pyqt5_import(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("PyQt5"))

    def test_no_exec_underscore(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, "exec_")


class TestGenerator(unittest.TestCase):

    LIGHT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
             '<rect x="2" y="2" width="20" height="20" fill="#272324"/>'
             '<circle cx="12" cy="12" r="4" fill="#E6202E"/></svg>')

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fp:
            fp.write(text)

    def _read_dark(self, name):
        with open(os.path.join(self.dir, "dark", name), encoding="utf-8") as fp:
            return fp.read()

    def test_substitutes_every_token(self):
        self.assertIn(icon_tokens.FG_DARK,
                      build_icon_themes.to_dark(self.LIGHT))
        self.assertIn(icon_tokens.AC_DARK,
                      build_icon_themes.to_dark(self.LIGHT))
        self.assertNotIn(icon_tokens.FG_LIGHT,
                         build_icon_themes.to_dark(self.LIGHT))

    def test_soft_fill_carries_across_by_color_substitution(self):
        # the tint rides on the accent color so it needs no rule of its own, see icon_tokens.SOFT_OPACITY for why it isn't rgba()
        source = '<rect fill="#E6202E" fill-opacity="0.12"/>'
        self.assertEqual(build_icon_themes.to_dark(source),
                         '<rect fill="#FF4554" fill-opacity="0.12"/>')

    def test_rgba_notation_is_rejected(self):
        # QtSvg renders rgba() as solid black rather than a tint, so it must never reach a shipped icon
        self._write("tinted.svg", '<rect fill="rgba(230,32,46,0.10)"/>')
        with self.assertRaises(build_icon_themes.IconError) as caught:
            build_icon_themes.build(self.dir)
        self.assertIn("rgba(", str(caught.exception))

    def test_build_writes_dark_folder(self):
        self._write("sample.svg", self.LIGHT)
        self.assertEqual(build_icon_themes.build(self.dir), ["sample.svg"])
        self.assertIn(icon_tokens.FG_DARK, self._read_dark("sample.svg"))

    def test_build_is_idempotent(self):
        self._write("sample.svg", self.LIGHT)
        build_icon_themes.build(self.dir)
        first = self._read_dark("sample.svg")
        build_icon_themes.build(self.dir)
        self.assertEqual(first, self._read_dark("sample.svg"))

    def test_untokenised_color_is_rejected(self):
        self._write("rogue.svg", '<rect fill="#c43b3b"/>')
        with self.assertRaises(build_icon_themes.IconError) as caught:
            build_icon_themes.build(self.dir)
        self.assertIn("#c43b3b", str(caught.exception))
        self.assertIn("rogue.svg", str(caught.exception))

    def test_white_knockout_is_rejected(self):
        self._write("knockout.svg", '<rect fill="#ffffff"/>')
        with self.assertRaises(build_icon_themes.IconError):
            build_icon_themes.build(self.dir)

    def test_skipped_mark_gets_no_dark_variant(self):
        self._write("vernier.svg", '<rect fill="#141416"/>')
        self._write("sample.svg", self.LIGHT)
        self.assertEqual(build_icon_themes.build(self.dir), ["sample.svg"])
        self.assertFalse(
            os.path.exists(os.path.join(self.dir, "dark", "vernier.svg")))

    def test_stale_dark_file_is_removed(self):
        self._write("sample.svg", self.LIGHT)
        build_icon_themes.build(self.dir)
        os.rename(os.path.join(self.dir, "sample.svg"),
                  os.path.join(self.dir, "renamed.svg"))
        build_icon_themes.build(self.dir)
        self.assertFalse(
            os.path.exists(os.path.join(self.dir, "dark", "sample.svg")))


if __name__ == "__main__":
    unittest.main()
