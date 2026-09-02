# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""style_templates end to end - schema validation, disk round-trips, role binding, the generated label expression, and applying a template to a real memory layer. TestDialogConventions also AST-checks style_dialog.py so UI regressions fail here without needing a display."""

import ast
import os
import sys
import tempfile
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsExpression, QgsExpressionContext,
    QgsExpressionContextUtils, QgsFeature, QgsGeometry,
    QgsSingleSymbolRenderer, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.services import style_templates  # noqa: E402

QGS = None

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _template(name="Test", **overrides):
    """A minimal valid template, optionally overridden per test."""
    template = {
        "name": name,
        "geometry": "polygon",
        "line": {"color": [0, 0, 255, 255], "width": 0.8,
                 "pen_style": "solid"},
        "vertex_marker": {"enabled": True, "shape": "square",
                          "color": [255, 0, 0, 255], "size": 2.0},
        "labels": {
            "enabled": True,
            "size": 8.0,
            "buffer": {"enabled": True, "size": 0.8,
                       "color": [255, 255, 255, 255]},
            "roles": [
                {"name": "identifier",
                 "field_aliases": ["id", "parcel_id"],
                 "prefix": "", "suffix": "", "skip_empty": True},
                {"name": "area",
                 "field_aliases": ["area", "surface", "sup"],
                 "prefix": "", "suffix": " sqm", "skip_empty": True},
            ],
        },
    }
    template.update(overrides)
    return template


def _fixture_layer():
    """Polygon layer with fields ID (string), Sup (int), notes (string)."""
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=ID:string&field=Sup:integer"
        "&field=notes:string", "fixture", "memory")
    return layer


def _add_feature(layer, attributes):
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromWkt(
        "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"))
    feature.setAttributes(attributes)
    layer.dataProvider().addFeatures([feature])
    return feature


def _evaluate(layer, feature, expression):
    context = QgsExpressionContext()
    context.appendScopes(
        QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    context.setFeature(feature)
    expr = QgsExpression(expression)
    assert not expr.hasParserError(), expr.parserErrorString()
    value = expr.evaluate(context)
    assert not expr.hasEvalError(), expr.evalErrorString()
    return value


class TestValidation(unittest.TestCase):

    def test_minimal_template_passes(self):
        style_templates.validate(
            {"name": "Bare", "line": {"color": [0, 0, 0], "width": 0.3}})

    def test_full_template_passes(self):
        style_templates.validate(_template())

    def test_missing_name_fails(self):
        with self.assertRaises(style_templates.TemplateError):
            style_templates.validate(
                {"line": {"color": [0, 0, 0], "width": 0.3}})

    def test_bad_pen_style_fails(self):
        template = _template()
        template["line"]["pen_style"] = "dashed"
        with self.assertRaises(style_templates.TemplateError):
            style_templates.validate(template)

    def test_bad_color_fails(self):
        template = _template()
        template["line"]["color"] = [0, 0, 999]
        with self.assertRaises(style_templates.TemplateError):
            style_templates.validate(template)

    def test_duplicate_role_names_fail(self):
        template = _template()
        template["labels"]["roles"].append(
            {"name": "identifier", "field_aliases": ["x"]})
        with self.assertRaises(style_templates.TemplateError):
            style_templates.validate(template)

    def test_role_without_aliases_fails(self):
        template = _template()
        template["labels"]["roles"][0]["field_aliases"] = []
        with self.assertRaises(style_templates.TemplateError):
            style_templates.validate(template)

    def test_unknown_placement_fails(self):
        template = _template()
        template["labels"]["placement"] = "diagonal"
        with self.assertRaises(style_templates.TemplateError):
            style_templates.validate(template)


class TestStorageRoundTrip(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_list_load(self):
        template = _template("Roundtrip")
        style_templates.save(template, self.dir)
        self.assertEqual(style_templates.list_templates(self.dir),
                         ["Roundtrip"])
        self.assertEqual(style_templates.load("Roundtrip", self.dir),
                         template)

    def test_save_same_name_overwrites_in_place(self):
        style_templates.save(_template("Once"), self.dir)
        tweaked = _template("Once")
        tweaked["line"]["width"] = 1.5
        style_templates.save(tweaked, self.dir)
        files = [f for f in os.listdir(self.dir) if f.endswith(".json")]
        self.assertEqual(len(files), 1)
        self.assertEqual(
            style_templates.load("Once", self.dir)["line"]["width"], 1.5)

    def test_filename_collision_gets_suffix(self):
        # both names sanitize to the same file basename
        style_templates.save(_template("A/B"), self.dir)
        style_templates.save(_template("A_B"), self.dir)
        self.assertEqual(style_templates.list_templates(self.dir),
                         ["A/B", "A_B"])

    def test_rename(self):
        style_templates.save(_template("Old"), self.dir)
        style_templates.rename("Old", "New", self.dir)
        self.assertEqual(style_templates.list_templates(self.dir), ["New"])
        self.assertEqual(style_templates.load("New", self.dir)["name"],
                         "New")
        with self.assertRaises(style_templates.TemplateError):
            style_templates.load("Old", self.dir)

    def test_rename_to_existing_fails(self):
        style_templates.save(_template("A"), self.dir)
        style_templates.save(_template("B"), self.dir)
        with self.assertRaises(style_templates.TemplateError):
            style_templates.rename("A", "B", self.dir)

    def test_delete(self):
        style_templates.save(_template("Doomed"), self.dir)
        style_templates.delete("Doomed", self.dir)
        self.assertEqual(style_templates.list_templates(self.dir), [])
        with self.assertRaises(style_templates.TemplateError):
            style_templates.delete("Doomed", self.dir)

    def test_load_missing_fails(self):
        with self.assertRaises(style_templates.TemplateError):
            style_templates.load("Ghost", self.dir)

    def test_unreadable_file_is_skipped(self):
        with open(os.path.join(self.dir, "junk.json"), "w",
                  encoding="utf-8") as fp:
            fp.write("{ not json")
        style_templates.save(_template("Good"), self.dir)
        self.assertEqual(style_templates.list_templates(self.dir), ["Good"])


class TestNothingIsSeeded(unittest.TestCase):
    """Quick Symbology starts blank - no template ships with the plugin."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_profile_lists_nothing(self):
        self.assertEqual(style_templates.list_templates(self.dir), [])

    def test_no_template_files_ship_with_the_plugin(self):
        # a stray .json under the plugin folder gets picked up by build_zip and reintroduces a default
        stray = []
        for folder, dirs, files in os.walk(PLUGIN_DIR):
            dirs[:] = [d for d in dirs
                       if d not in (".git", "test", "dist", "__pycache__")]
            stray += [os.path.join(folder, f) for f in files
                      if f.lower().endswith(".json")]
        self.assertEqual(stray, [])

    def test_no_seeding_helper_remains(self):
        self.assertFalse(hasattr(style_templates, "ensure_defaults"))

    def test_saving_then_listing_round_trips(self):
        style_templates.save(_template(), self.dir)
        self.assertEqual(len(style_templates.list_templates(self.dir)), 1)


class TestBindRoles(unittest.TestCase):

    def test_case_insensitive_binding_and_unbound_report(self):
        layer = _fixture_layer()
        template = _template()
        template["labels"]["roles"].append(
            {"name": "zone", "field_aliases": ["zone", "district"]})
        binding, unbound = style_templates.bind_roles(template, layer)
        # "id" matches the field "ID" and "sup" matches "Sup", and the real names come back case-preserved
        self.assertEqual(binding, {"identifier": "ID", "area": "Sup"})
        self.assertEqual(unbound, ["zone"])

    def test_first_alias_wins(self):
        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:3857&field=surface:integer&field=area:integer",
            "fixture", "memory")
        template = _template()
        binding, _ = style_templates.bind_roles(template, layer)
        # role aliases are ["area", "surface", ...]: alias order decides
        self.assertEqual(binding["area"], "area")

    def test_no_roles_binds_nothing(self):
        layer = _fixture_layer()
        template = _template()
        template["labels"]["roles"] = []
        binding, unbound = style_templates.bind_roles(template, layer)
        self.assertEqual(binding, {})
        self.assertEqual(unbound, [])


class TestLabelExpression(unittest.TestCase):

    ROLES = (
        {"name": "identifier", "field_aliases": ["id"],
         "prefix": "", "suffix": "", "skip_empty": True},
        {"name": "area", "field_aliases": ["sup"],
         "prefix": "", "suffix": " sqm", "skip_empty": True},
    )
    BINDING = {"identifier": "ID", "area": "Sup"}

    def _result(self, attributes, roles=None, binding=None):
        layer = _fixture_layer()
        feature = _add_feature(layer, attributes)
        expression = style_templates.build_label_expression(
            list(roles or self.ROLES), binding or self.BINDING)
        self.assertIsNotNone(expression)
        return _evaluate(layer, feature, expression)

    def test_both_values_newline_joined(self):
        self.assertEqual(self._result(["A1", 350, None]), "A1\n350 sqm")

    def test_null_value_drops_its_line(self):
        self.assertEqual(self._result([None, 350, None]), "350 sqm")

    def test_empty_string_drops_its_line(self):
        self.assertEqual(self._result(["", 350, None]), "350 sqm")

    def test_other_role_null_keeps_first_line(self):
        self.assertEqual(self._result(["A1", None, None]), "A1")

    def test_non_skip_role_keeps_prefix_for_null(self):
        roles = [{"name": "area", "field_aliases": ["sup"],
                  "prefix": "Area: ", "suffix": "", "skip_empty": False}]
        result = self._result([None, None, None], roles=roles,
                              binding={"area": "Sup"})
        self.assertEqual(result, "Area: ")

    def test_quotes_in_prefix_are_escaped(self):
        roles = [{"name": "identifier", "field_aliases": ["id"],
                  "prefix": "Owner's ", "suffix": "", "skip_empty": True}]
        result = self._result(["A1", None, None], roles=roles,
                              binding={"identifier": "ID"})
        self.assertEqual(result, "Owner's A1")

    def test_nothing_bound_returns_none(self):
        self.assertIsNone(
            style_templates.build_label_expression(list(self.ROLES), {}))


class TestApplyToLayer(unittest.TestCase):

    def test_symbology_and_labels_applied(self):
        layer = _fixture_layer()
        _add_feature(layer, ["A1", 350, None])
        unbound = style_templates.apply_to_layer(_template(), layer)
        self.assertEqual(unbound, [])

        renderer = layer.renderer()
        self.assertIsInstance(renderer, QgsSingleSymbolRenderer)
        # fill layer plus the vertex marker line
        self.assertEqual(renderer.symbol().symbolLayerCount(), 2)

        self.assertTrue(layer.labelsEnabled())
        settings = layer.labeling().settings()
        self.assertTrue(settings.isExpression)
        self.assertIn('"ID"', settings.fieldName)
        self.assertIn('"Sup"', settings.fieldName)

    def test_disabled_vertex_marker_stays_off(self):
        layer = _fixture_layer()
        template = _template()
        template["vertex_marker"]["enabled"] = False
        style_templates.apply_to_layer(template, layer)
        # just the fill layer, no marker line appended
        self.assertEqual(layer.renderer().symbol().symbolLayerCount(), 1)

    def test_unbound_roles_reported_and_rest_labeled(self):
        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:3857&field=ID:string", "fixture", "memory")
        unbound = style_templates.apply_to_layer(_template(), layer)
        self.assertEqual(unbound, ["area"])
        self.assertTrue(layer.labelsEnabled())
        self.assertNotIn("area", layer.labeling().settings().fieldName)

    def test_template_without_labels_disables_labeling(self):
        layer = _fixture_layer()
        layer.setLabelsEnabled(True)
        template = _template()
        template["labels"] = {"enabled": False}
        style_templates.apply_to_layer(template, layer)
        self.assertFalse(layer.labelsEnabled())

    def test_replaces_non_single_symbol_renderer(self):
        from qgis.core import QgsCategorizedSymbolRenderer  # type: ignore
        layer = _fixture_layer()
        layer.setRenderer(QgsCategorizedSymbolRenderer("ID", []))
        style_templates.apply_to_layer(_template(), layer)
        self.assertIsInstance(layer.renderer(), QgsSingleSymbolRenderer)

    def test_explicit_binding_respected(self):
        layer = _fixture_layer()
        unbound = style_templates.apply_to_layer(
            _template(), layer, {"identifier": "notes"})
        self.assertEqual(unbound, ["area"])
        self.assertIn('"notes"', layer.labeling().settings().fieldName)


class TestDialogConventions(unittest.TestCase):
    """AST checks mirroring test_processing_dialogs."""

    FILES = (
        os.path.join(PLUGIN_DIR, "dialogs", "style_dialog.py"),
        os.path.join(PLUGIN_DIR, "services", "style_templates.py"),
    )

    @staticmethod
    def _tree(path):
        with open(path, encoding="utf-8") as fp:
            return ast.parse(fp.read(), filename=os.path.basename(path))

    def test_no_pyqt5_imports(self):
        for path in self.FILES:
            with self.subTest(file=os.path.basename(path)):
                for node in ast.walk(self._tree(path)):
                    if isinstance(node, ast.ImportFrom):
                        self.assertFalse(
                            (node.module or "").startswith("PyQt5"))
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(alias.name.startswith("PyQt5"))

    def test_no_exec_underscore(self):
        for path in self.FILES:
            with self.subTest(file=os.path.basename(path)):
                for node in ast.walk(self._tree(path)):
                    if isinstance(node, ast.Attribute):
                        self.assertNotEqual(node.attr, "exec_")

    def test_service_never_imports_qtwidgets(self):
        for node in ast.walk(self._tree(self.FILES[1])):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("QtWidgets", node.module or "")

    def test_vertex_group_is_checkable_and_drives_capture(self):
        # the Custom tab has to be able to save marker-less templates, so the group is checkable and capture reads its state instead of hardcoding enabled=True
        with open(self.FILES[0], encoding="utf-8") as fp:
            source = fp.read()
        self.assertIn("setCheckable(True)", source)
        self.assertIn('"enabled": self.vertex_group.isChecked()', source)

    def test_templates_tab_can_strip_vertex_markers(self):
        # the Templates tab override applies a template minus its markers, on a copy so the stored dict is never mutated and written back
        with open(self.FILES[0], encoding="utf-8") as fp:
            source = fp.read()
        self.assertIn("template_vertex_chk", source)
        self.assertIn("copy.deepcopy(template)", source)


if __name__ == "__main__":
    unittest.main()
