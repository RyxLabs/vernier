# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source-level checks on how vernier.py resolves icons: the light/dark split, both dark-mode detection paths, and the Qt6 rules the resolver has to keep. Pure text analysis, so it needs no QgsApplication and runs in milliseconds.

The design-system suites that used to live here - contrast ratios, the token
palettes, the half-pixel grid and the dark-twin regeneration check - tested
scripts/icon_tokens.py and scripts/build_icon_themes.py, which are no longer
part of the repository.
"""

import ast
import os
import unittest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


if __name__ == "__main__":
    unittest.main()
