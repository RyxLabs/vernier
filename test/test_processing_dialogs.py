# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Convention checks for the one-shot processing dialogs. Pure AST so they run without a QGIS install - files must parse, must not import PyQt5 directly, must not call exec_(), must not import processing at module level, and every tool must report through BaseDialog's shared result messages."""

import ast
import unittest
from pathlib import Path

DIALOGS = Path(__file__).resolve().parent.parent / "dialogs"

FILES = (
    "buffer_dialog.py",
    "overlay_dialog.py",
    "intersection_dialog.py",
    "difference_dialog.py",
    "dissolve_dialog.py",
    "multi2single_dialog.py",
)

# every dialog that runs a tool and reports a result: warnings and errors titled by the dialog itself, results announced as a created layer
TOOL_DIALOGS = FILES + (
    "centerline_dialog.py",
    "remove_vertices_dialog.py",
    "lines_to_polygons_dialog.py",
    "spatial_join_dialog.py",
    "attribute_join_dialog.py",
    "kmz_export_dialog.py",
    "dxf_export_dialog.py",
    "dxf_import_dialog.py",
)

# hand-rolled titles drift apart; show_tool_warning/show_tool_error/show_tool_failure take the dialog's own window title
BANNED_MESSAGE_CALLS = ("show_warning", "show_error")


class TestProcessingDialogConventions(unittest.TestCase):

    @staticmethod
    def _tree(name):
        source = (DIALOGS / name).read_text(encoding="utf-8")
        return ast.parse(source, filename=name)

    def test_parses(self):
        for name in FILES:
            with self.subTest(file=name):
                self._tree(name)

    def test_no_pyqt5_imports(self):
        for name in FILES:
            with self.subTest(file=name):
                for node in ast.walk(self._tree(name)):
                    if isinstance(node, ast.ImportFrom):
                        self.assertFalse(
                            (node.module or "").startswith("PyQt5"),
                            f"{name} imports PyQt5 directly")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(
                                alias.name.startswith("PyQt5"),
                                f"{name} imports PyQt5 directly")

    def test_no_exec_underscore(self):
        for name in FILES:
            with self.subTest(file=name):
                for node in ast.walk(self._tree(name)):
                    if isinstance(node, ast.Attribute):
                        self.assertNotEqual(
                            node.attr, "exec_",
                            f"{name} uses exec_() instead of exec()")

    def test_no_module_level_processing_import(self):
        # walk everything except function bodies, so imports tucked inside a module-level try/except or if block get caught too
        def module_level_nodes(tree):
            stack = list(tree.body)
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                yield node
                stack.extend(ast.iter_child_nodes(node))

        for name in FILES:
            with self.subTest(file=name):
                for node in module_level_nodes(self._tree(name)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(
                                alias.name == "processing"
                                or alias.name.startswith("processing."),
                                f"{name} imports processing at module level")
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        self.assertFalse(
                            module == "processing"
                            or module.startswith("processing."),
                            f"{name} imports from processing at module level")


class TestSharedResultMessages(unittest.TestCase):
    """The user-facing half of the convention: one message shape across every tool."""

    @staticmethod
    def _tree(name):
        source = (DIALOGS / name).read_text(encoding="utf-8")
        return ast.parse(source, filename=name)

    def test_no_hand_rolled_warning_or_error_titles(self):
        for name in TOOL_DIALOGS:
            with self.subTest(file=name):
                offenders = []
                for node in ast.walk(self._tree(name)):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if (isinstance(func, ast.Attribute)
                            and func.attr in BANNED_MESSAGE_CALLS
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "self"):
                        offenders.append(f"{func.attr} at line {node.lineno}")
                self.assertEqual(
                    offenders, [],
                    f"{name} titles its own messages instead of using "
                    "show_tool_warning / show_tool_error / "
                    "show_tool_failure")


if __name__ == "__main__":
    unittest.main()
