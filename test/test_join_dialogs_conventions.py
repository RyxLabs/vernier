# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Convention checks for the join dialogs and their shared service. Pure AST so they run without a QGIS install - files must parse, must not import PyQt5 directly, must not call exec_(), must not import processing at module level."""

import ast
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent

FILES = (
    PLUGIN / "dialogs" / "attribute_join_dialog.py",
    PLUGIN / "dialogs" / "spatial_join_dialog.py",
    PLUGIN / "services" / "join_service.py",
)


class TestJoinConventions(unittest.TestCase):

    @staticmethod
    def _tree(path):
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=path.name)

    def test_parses(self):
        for path in FILES:
            with self.subTest(file=path.name):
                self._tree(path)

    def test_no_pyqt5_imports(self):
        for path in FILES:
            with self.subTest(file=path.name):
                for node in ast.walk(self._tree(path)):
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
        for path in FILES:
            with self.subTest(file=path.name):
                for node in ast.walk(self._tree(path)):
                    if isinstance(node, ast.Attribute):
                        self.assertNotEqual(
                            node.attr, "exec_",
                            f"{path.name} uses exec_() instead of exec()")

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

        for path in FILES:
            with self.subTest(file=path.name):
                for node in module_level_nodes(self._tree(path)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(
                                alias.name == "processing"
                                or alias.name.startswith("processing."),
                                f"{path.name} imports processing at "
                                "module level")
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        self.assertFalse(
                            module == "processing"
                            or module.startswith("processing."),
                            f"{path.name} imports from processing at "
                            "module level")


if __name__ == "__main__":
    unittest.main()
