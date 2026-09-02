# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""BaseDialog's shared widgets, its remembered-value store and the plural phrases the result messages are built from. Needs a GUI-enabled QgsApplication, offscreen is fine."""

import os
import sys
import unittest

# make the plugins folder importable so the package-relative imports resolve
_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer,
)

from vernier.dialogs.base_dialog import BaseDialog  # noqa: E402

QGS = None


def setUpModule():
    global QGS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _polygon_layer(name, count=2):
    layer = QgsVectorLayer("Polygon?field=id:integer", name, "memory")
    feats = []
    for i in range(count):
        f = QgsFeature(layer.fields())
        x = i * 20
        f.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} 0, {x + 10} 0, {x + 10} 10, {x} 10, {x} 0))"))
        f.setAttributes([i])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


class SelectedOnlyCheckboxTests(unittest.TestCase):
    """The "selected features only" box must track the live selection."""

    def setUp(self):
        self.layer = _polygon_layer("parcels")
        QgsProject.instance().addMapLayer(self.layer)
        self.dialog = BaseDialog()
        self.group, self.combo, self.checkbox = (
            self.dialog.create_layer_group("Layer"))
        self.combo.setLayer(self.layer)

    def tearDown(self):
        self.dialog.done(0)
        self.dialog.deleteLater()
        QgsProject.instance().removeAllMapLayers()

    def test_disabled_without_selection(self):
        self.assertFalse(self.checkbox.isEnabled())
        self.assertFalse(self.checkbox.isChecked())

    def test_enables_when_selection_appears(self):
        self.layer.selectAll()
        self.assertTrue(self.checkbox.isEnabled())

    def test_unchecks_when_selection_cleared_on_same_layer(self):
        # a box left ticked after the canvas selection is cleared would send "selected only" against an empty layer, or the whole one for tools that fall back
        self.layer.selectAll()
        self.checkbox.setChecked(True)
        self.layer.removeSelection()
        self.assertFalse(self.checkbox.isChecked())
        self.assertFalse(self.checkbox.isEnabled())

    def test_follows_layer_swap(self):
        other = _polygon_layer("buildings")
        QgsProject.instance().addMapLayer(other)
        other.selectAll()
        self.combo.setLayer(other)
        self.assertTrue(self.checkbox.isEnabled())
        # the old layer's selection must no longer drive the box
        self.layer.selectAll()
        other.removeSelection()
        self.assertFalse(self.checkbox.isEnabled())

    def test_finished_dialog_stops_tracking(self):
        self.layer.selectAll()
        self.dialog.done(0)
        self.checkbox.setEnabled(False)
        # a later selection change on the layer must not touch the widget
        self.layer.removeSelection()
        self.layer.selectAll()
        self.assertFalse(self.checkbox.isEnabled())


class _MemoryProbeDialog(BaseDialog):
    """Its class name is the settings namespace, so it must not collide with a real dialog's."""


class RememberedValueTests(unittest.TestCase):
    """A tool has to reopen with what it was last run with, and never choke on a stored value that no longer fits the widget."""

    def setUp(self):
        from qgis.PyQt.QtWidgets import (
            QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox,
        )
        self.widgets = {
            "flag": QCheckBox(),
            "count": QSpinBox(),
            "distance": QDoubleSpinBox(),
            "style": QComboBox(),
            "field": QComboBox(),
            "name": QLineEdit(),
        }
        self.widgets["count"].setRange(0, 100)
        self.widgets["distance"].setRange(-100, 100)
        self.widgets["distance"].setDecimals(3)
        self.widgets["style"].addItems(["Round", "Flat", "Square"])
        self.widgets["field"].addItems(["", "sector", "tarla"])

    def tearDown(self):
        from qgis.core import QgsSettings
        from vernier.services import dialog_memory
        settings = QgsSettings()
        settings.beginGroup(f"{dialog_memory.GROUP}/_MemoryProbeDialog")
        settings.remove("")
        settings.endGroup()

    def _dialog(self):
        dialog = _MemoryProbeDialog()
        for key, widget in self.widgets.items():
            dialog.remember(key, widget, by_text=(key == "field"))
        return dialog

    def test_values_survive_a_save_and_restore(self):
        self.widgets["flag"].setChecked(True)
        self.widgets["count"].setValue(7)
        self.widgets["distance"].setValue(1.25)
        self.widgets["style"].setCurrentIndex(1)
        self.widgets["field"].setCurrentIndex(2)
        self.widgets["name"].setText("parcels_buffer")
        self._dialog().save_remembered()

        self.widgets["flag"].setChecked(False)
        self.widgets["count"].setValue(0)
        self.widgets["distance"].setValue(0.0)
        self.widgets["style"].setCurrentIndex(0)
        self.widgets["field"].setCurrentIndex(0)
        self.widgets["name"].clear()

        self._dialog().restore_remembered()
        self.assertTrue(self.widgets["flag"].isChecked())
        self.assertEqual(self.widgets["count"].value(), 7)
        self.assertAlmostEqual(self.widgets["distance"].value(), 1.25)
        self.assertEqual(self.widgets["style"].currentIndex(), 1)
        self.assertEqual(self.widgets["field"].currentText(), "tarla")
        self.assertEqual(self.widgets["name"].text(), "parcels_buffer")

    def test_nothing_stored_leaves_the_defaults_alone(self):
        self.widgets["count"].setValue(5)
        self._dialog().restore_remembered()
        self.assertEqual(self.widgets["count"].value(), 5)

    def test_field_the_layer_no_longer_has_is_ignored(self):
        # by_text exists for this: a remembered field name means nothing against a different layer's list
        self.widgets["field"].setCurrentIndex(1)
        self._dialog().save_remembered()
        self.widgets["field"].clear()
        self.widgets["field"].addItems(["", "cod", "nr"])
        self._dialog().restore_remembered()
        self.assertEqual(self.widgets["field"].currentText(), "")

    def test_stored_strings_are_coerced(self):
        # QSettings hands values back as strings on some platforms
        from vernier.dialogs import _ui_helpers
        _ui_helpers.apply_widget_state(self.widgets["flag"], "true")
        _ui_helpers.apply_widget_state(self.widgets["count"], "9")
        _ui_helpers.apply_widget_state(self.widgets["distance"], "2.5")
        _ui_helpers.apply_widget_state(self.widgets["style"], "2")
        self.assertTrue(self.widgets["flag"].isChecked())
        self.assertEqual(self.widgets["count"].value(), 9)
        self.assertAlmostEqual(self.widgets["distance"].value(), 2.5)
        self.assertEqual(self.widgets["style"].currentIndex(), 2)

    def test_index_past_the_end_is_ignored(self):
        from vernier.dialogs import _ui_helpers
        self.widgets["style"].setCurrentIndex(1)
        _ui_helpers.apply_widget_state(self.widgets["style"], 99)
        self.assertEqual(self.widgets["style"].currentIndex(), 1)


class SelectRowTests(unittest.TestCase):
    """The one All/None row every checkable list ships with."""

    def test_buttons_drive_the_callbacks(self):
        from vernier.dialogs import _ui_helpers
        calls = []
        layout, all_btn, none_btn = _ui_helpers.make_select_row(
            lambda: calls.append("all"), lambda: calls.append("none"))
        self.assertEqual(all_btn.text(), "All")
        self.assertEqual(none_btn.text(), "None")
        all_btn.click()
        none_btn.click()
        self.assertEqual(calls, ["all", "none"])
        # trailing stretch keeps the buttons left-aligned
        self.assertGreater(layout.count(), 2)

    def test_extra_buttons_sit_before_the_stretch(self):
        from qgis.PyQt.QtWidgets import QPushButton
        from vernier.dialogs import _ui_helpers
        extra = QPushButton("Visible only")
        layout, _all_btn, _none_btn = _ui_helpers.make_select_row(
            lambda: None, lambda: None, (extra,))
        self.assertIs(layout.itemAt(2).widget(), extra)

    def test_bulk_check_covers_tree_columns_and_lists(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import (
            QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
        )
        from vernier.dialogs import _ui_helpers

        tree = QTreeWidget()
        tree.setColumnCount(2)
        for i in range(3):
            item = QTreeWidgetItem(["a", "b"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setCheckState(1, Qt.CheckState.Checked)
            tree.addTopLevelItem(item)
        _ui_helpers.set_all_check_states(tree, True)
        _ui_helpers.set_all_check_states(tree, False, column=1)
        for i in range(3):
            item = tree.topLevelItem(i)
            self.assertEqual(item.checkState(0), Qt.CheckState.Checked)
            self.assertEqual(item.checkState(1), Qt.CheckState.Unchecked)

        checkable_list = QListWidget()
        for i in range(3):
            item = QListWidgetItem("v")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            checkable_list.addItem(item)
        _ui_helpers.set_all_check_states(checkable_list, True)
        for i in range(3):
            self.assertEqual(checkable_list.item(i).checkState(),
                             Qt.CheckState.Checked)

    def test_bulk_check_fires_no_item_signals(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QTreeWidget, QTreeWidgetItem
        from vernier.dialogs import _ui_helpers

        tree = QTreeWidget()
        item = QTreeWidgetItem(["a"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        tree.addTopLevelItem(item)
        fired = []
        tree.itemChanged.connect(lambda *_: fired.append(1))
        _ui_helpers.set_all_check_states(tree, True)
        self.assertEqual(fired, [])


class ResultPhraseTests(unittest.TestCase):
    """The one success sentence every tool shows has to agree with itself on singular and plural."""

    def test_feature_and_layer_plurals(self):
        from vernier.dialogs import _ui_helpers
        self.assertEqual(_ui_helpers.features_phrase(1), "1 feature")
        self.assertEqual(_ui_helpers.features_phrase(0), "0 features")
        self.assertEqual(_ui_helpers.features_phrase(42), "42 features")
        self.assertEqual(_ui_helpers.layers_phrase(1), "1 layer")
        self.assertEqual(_ui_helpers.layers_phrase(3), "3 layers")


class TranslationContextTests(unittest.TestCase):
    """Every class calling self.tr() has to route through the shared "Vernier" context, not QObject.tr's class-name one, or its strings land outside it."""

    def test_base_dialog_uses_vernier_context(self):
        from qgis.PyQt.QtWidgets import QDialog
        self.assertIsNot(BaseDialog.tr, QDialog.tr)
        # the identity round-trip proves it reaches QCoreApplication.translate under the "Vernier" context, same as i18n.tr
        from vernier.i18n import tr
        self.assertEqual(BaseDialog().tr("Selected features only"),
                         tr("Selected features only"))

    def test_every_self_tr_user_overrides_tr(self):
        import ast
        plugin = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for sub in ("", "dialogs", "tools"):
            folder = os.path.join(plugin, sub)
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                tree = ast.parse(open(path, encoding="utf-8").read(), name)
                for cls in [n for n in ast.walk(tree)
                            if isinstance(n, ast.ClassDef)]:
                    uses_tr = any(
                        isinstance(n, ast.Attribute) and n.attr == "tr"
                        and isinstance(n.value, ast.Name)
                        and n.value.id == "self"
                        for n in ast.walk(cls))
                    if not uses_tr:
                        continue
                    defines = any(isinstance(n, ast.FunctionDef)
                                  and n.name == "tr" for n in cls.body)
                    inherits = any(
                        isinstance(b, ast.Name)
                        and b.id in ("BaseDialog", "OverlayDialog")
                        for b in cls.bases)
                    if not (defines or inherits):
                        offenders.append(f"{sub}/{name}:{cls.name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
