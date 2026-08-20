# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The command registry's pure logic - prefix matching, alias collisions and history, all headless, plus AST convention checks on the CAD Mode modules."""

import ast
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from qgis.core import QgsApplication  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier import features  # noqa: E402
from vernier.command_bar import (  # noqa: E402
    BUILTIN_COMMANDS, CommandBar, CommandHistory, CommandRegistry,
    build_registry,
)

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _noop_a():
    pass


def _noop_b():
    pass


def _registry():
    """Small fixture registry with a multi-alias command and a neighbor."""
    reg = CommandRegistry()
    reg.register(("sel", "select"), "Select", _noop_a, "select things")
    reg.register(("save",), "Save Edits", _noop_b, "save edits")
    return reg


class TestCommandRegistryResolve(unittest.TestCase):

    def test_exact_match(self):
        kind, name, label, callback = _registry().resolve("sel")
        self.assertEqual(kind, "exact")
        self.assertEqual(name, "sel")
        self.assertEqual(label, "Select")
        self.assertIs(callback, _noop_a)

    def test_exact_match_is_case_and_space_insensitive(self):
        kind, name, _label, _cb = _registry().resolve("  SEL ")
        self.assertEqual(kind, "exact")
        self.assertEqual(name, "sel")

    def test_unique_prefix_resolves(self):
        kind, name, _label, callback = _registry().resolve("sa")
        self.assertEqual(kind, "exact")
        self.assertEqual(name, "save")
        self.assertIs(callback, _noop_b)

    def test_aliases_of_same_callback_do_not_make_prefix_ambiguous(self):
        # "sel" and "select" are one command; only "save" competes on "s"
        result = _registry().resolve("s")
        self.assertEqual(result[0], "ambiguous")
        # exactly two logical commands share the "s" prefix
        self.assertEqual(len(result[1]), 2)

    def test_ambiguous_prefix_lists_names(self):
        result = _registry().resolve("s")
        self.assertEqual(result[0], "ambiguous")
        self.assertIn("save", result[1])

    def test_exact_match_wins_over_ambiguity(self):
        # "sel" is a prefix of "select" but also an exact alias
        reg = _registry()
        reg.register(("self_intersect",), "Other", _noop_b, "")
        kind, name, _label, _cb = reg.resolve("sel")
        self.assertEqual(kind, "exact")
        self.assertEqual(name, "sel")

    def test_unknown_command(self):
        self.assertEqual(_registry().resolve("zz"), ("unknown",))

    def test_empty_input_is_unknown(self):
        self.assertEqual(_registry().resolve("   "), ("unknown",))

    def test_prefix_matches_dedup_and_sort(self):
        matches = _registry().prefix_matches("s")
        # sel/select collapse to one entry (first alias in sorted order)
        self.assertEqual(matches, [("save", "Save Edits"),
                                   ("sel", "Select")])

    def test_prefix_matches_empty_input(self):
        self.assertEqual(_registry().prefix_matches(""), [])


class TestCommandRegistryCollisions(unittest.TestCase):

    def test_reclaiming_an_alias_raises(self):
        reg = _registry()
        with self.assertRaises(ValueError):
            reg.register(("sel",), "Other", _noop_b, "")

    def test_duplicate_alias_within_one_command_raises(self):
        with self.assertRaises(ValueError):
            CommandRegistry().register(("x", "x"), "X", _noop_a, "")

    def test_empty_alias_raises(self):
        with self.assertRaises(ValueError):
            CommandRegistry().register(("  ",), "X", _noop_a, "")


class TestCommandHistory(unittest.TestCase):

    def _history(self):
        h = CommandHistory(maxlen=5)
        for text in ("one", "two", "three"):
            h.append(text)
        return h

    def test_previous_walks_back_from_newest(self):
        h = self._history()
        self.assertEqual(h.previous(), "three")
        self.assertEqual(h.previous(), "two")
        self.assertEqual(h.previous(), "one")

    def test_previous_stops_at_oldest(self):
        h = self._history()
        for _ in range(10):
            h.previous()
        self.assertEqual(h.previous(), "one")

    def test_next_without_navigation_returns_none(self):
        self.assertIsNone(self._history().next())

    def test_next_walks_forward(self):
        h = self._history()
        h.previous()  # three
        h.previous()  # two
        self.assertEqual(h.next(), "three")

    def test_next_past_newest_returns_empty_and_leaves_navigation(self):
        h = self._history()
        h.previous()  # three (newest)
        self.assertEqual(h.next(), "")
        self.assertFalse(h.is_navigating())

    def test_append_resets_navigation(self):
        h = self._history()
        h.previous()
        h.append("four")
        self.assertFalse(h.is_navigating())
        self.assertEqual(h.previous(), "four")

    def test_reset_leaves_navigation(self):
        h = self._history()
        h.previous()
        h.reset()
        self.assertIsNone(h.next())

    def test_maxlen_evicts_oldest(self):
        h = CommandHistory(maxlen=3)
        for text in ("a", "b", "c", "d"):
            h.append(text)
        self.assertEqual(h.entries(), ["b", "c", "d"])

    def test_empty_history(self):
        h = CommandHistory()
        self.assertIsNone(h.previous())
        self.assertIsNone(h.next())
        self.assertEqual(len(h), 0)


class TestShippedAliasTable(unittest.TestCase):

    def test_builtin_aliases_are_unique(self):
        seen = set()
        for aliases, _label, _method, _hint in BUILTIN_COMMANDS:
            for alias in aliases:
                self.assertNotIn(alias, seen,
                                 f"built-in alias '{alias}' claimed twice")
                seen.add(alias)

    def test_builtin_methods_exist_on_command_bar(self):
        for _aliases, _label, method, _hint in BUILTIN_COMMANDS:
            self.assertTrue(
                callable(getattr(CommandBar, method, None)),
                f"BUILTIN_COMMANDS references missing method '{method}'")

    def test_no_collisions_between_catalog_and_builtins(self):
        # the production path validates the shipped tables itself - register() raises on any alias claimed twice. the stub exposes every catalog method so nothing gets skipped, and passing the CommandBar class gives the builtin loop real functions
        stub = SimpleNamespace(**{
            feat.method: (lambda: None) for feat in features.CATALOG})
        registry = build_registry(stub, CommandBar)
        expected = (sum(len(feat.aliases) for feat in features.CATALOG)
                    + sum(len(aliases) for aliases, *_ in BUILTIN_COMMANDS))
        self.assertEqual(len(registry), expected)

    def test_catalog_aliases_are_lowercase_and_short(self):
        for feat in features.CATALOG:
            for alias in feat.aliases:
                self.assertEqual(alias, alias.lower().strip(),
                                 f"alias '{alias}' is not normalized")
                self.assertLessEqual(len(alias), 12,
                                     f"alias '{alias}' is too long to type")


class TestCadModeConventions(unittest.TestCase):
    """AST + source conventions for the CAD Mode modules."""

    PLUGIN = Path(__file__).resolve().parent.parent
    FILES = (PLUGIN / "command_bar.py", PLUGIN / "cad_grid.py")

    @classmethod
    def _sources(cls):
        for path in cls.FILES:
            yield path, path.read_text(encoding="utf-8")

    def test_parses(self):
        for path, source in self._sources():
            with self.subTest(file=path.name):
                ast.parse(source, filename=path.name)

    def test_no_pyqt5_imports(self):
        for path, source in self._sources():
            with self.subTest(file=path.name):
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.ImportFrom):
                        self.assertFalse(
                            (node.module or "").startswith("PyQt5"),
                            f"{path.name} imports PyQt5 directly")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(
                                alias.name.startswith("PyQt5"),
                                f"{path.name} imports PyQt5 directly")

    def test_no_exec_underscore(self):
        for path, source in self._sources():
            with self.subTest(file=path.name):
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.Attribute):
                        self.assertNotEqual(
                            node.attr, "exec_",
                            f"{path.name} uses exec_() instead of exec()")

    def test_no_trademarked_cad_product_names(self):
        # the CAD Mode modules stay vendor-neutral, the feature is "CAD Mode" and not a clone of one product. README, metadata and tool hints do name products, on purpose, to state format compatibility
        banned = "auto" + "cad"  # assembled so this file doesn't trip its own check
        for path, source in self._sources():
            with self.subTest(file=path.name):
                self.assertNotIn(banned, source.lower(),
                                 f"{path.name} mentions a trademarked name")


if __name__ == "__main__":
    unittest.main()
