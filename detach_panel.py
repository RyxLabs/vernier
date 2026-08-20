# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Split to Target Areas - cut fixed-area pieces off one polygon with straight cuts parallel to a two-click direction line, into a "<layer>_split" memory layer with part_id and area. Thin UI over services.detach_service."""

# owns rubber bands and a map tool, so the plugin has to call cleanup() from unload()

import math

from qgis.PyQt.QtCore import Qt, pyqtSignal  # type: ignore
from qgis.PyQt.QtGui import QColor, QCursor  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QApplication, QCheckBox, QDockWidget, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSplitter, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from qgis.core import (  # type: ignore
    Qgis, QgsCoordinateTransform, QgsCsException, QgsFeature, QgsField,
    QgsGeometry, QgsMapLayerProxyModel, QgsPointLocator, QgsPointXY,
    QgsProject, QgsUnitTypes, QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import (  # type: ignore
    QgsMapLayerComboBox, QgsMapTool, QgsRubberBand, QgsSnapIndicator,
)

from .qt_compat import FIELD_DOUBLE, FIELD_STRING
from .dialogs import _ui_helpers
from .i18n import tr as _tr
from .services import settings_service
from .services.detach_service import DetachError, detach_by_areas

_TOLERANCE = 0.001
# mm rounding quantizes achievable areas in steps of about cut length x 1 mm, so the convergence tolerance has to sit above that grid. half a square meter covers cuts up to ~1 km and matches deliverables specified in whole square meters
_MM_TOLERANCE = 0.5
_MM_DECIMALS = 3

# cycled in cut order, the remainder is always grey
_PREVIEW_COLORS = ("#e6194b", "#3cb44b", "#4363d8", "#f58231",
                   "#911eb4", "#0bb4c8", "#f032e6", "#9a6324")
_REMAINDER_COLOR = "#7f8c8d"

_WARN_STYLE = "color: #d9822b; font-weight: bold;"
_OVER_STYLE = "color: #c0392b; font-weight: bold;"

# group boxes default to ~9px padding all round, and four stacked in a narrow panel eat more height than the table can spare
_GROUP_MARGINS = (8, 4, 8, 6)
_GROUP_SPACING = 4

# roughly four rows plus the header, so the table stays usable however short the panel gets
_TABLE_MIN_HEIGHT = 120

# setup / table / actions heights for the first open, after that the stored split wins
_DEFAULT_SPLIT = (170, 300, 150)


def _fmt(value: float) -> str:
    """Label formatting - thousands separators, two decimals."""
    return f"{value:,.2f}"


def _fmt_cell(value: float) -> str:
    """Canonical cell text - dot decimal, no trailing zeros, so float(text) always round-trips."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _area_suffix(layer) -> str:
    """A " m²"-style suffix off the layer CRS, empty when we can't tell."""
    if layer is None or not layer.crs().isValid():
        return ""
    return " " + QgsUnitTypes.toAbbreviatedString(
        QgsUnitTypes.distanceToAreaUnit(layer.crs().mapUnits()))


def _parse_number(cell: str, dot_is_thousands: bool = False) -> float:
    """Float out of a spreadsheet-style cell - "1.234,56", "1,234.56", "1 234,5" and "1,234,567" all parse. With both separators present the rightmost is the decimal mark, and a lone dot is ambiguous, which is what dot_is_thousands settles."""
    s = cell.replace("\u00a0", "").replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(",") > 1:
        s = s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s and dot_is_thousands:
        s = s.replace(".", "")
    return float(s)


def parse_area_rows(text: str, start_id: int = 1):
    """Parse an Excel clipboard block into (id, area) rows - each line is either ID<TAB>area or a bare area that gets a sequential id. Raises ValueError with a translated message naming the first line that doesn't parse."""
    entries = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        cells = [c.strip() for c in line.split("\t") if c.strip()]
        if not cells:
            continue
        if len(cells) == 1:
            entries.append((line_no, None, cells[0]))
        else:
            entries.append((line_no, cells[0], cells[1]))

    # a pasted header row is a convenience skip, not an error
    if (len(entries) > 1
            and entries[0][2].replace(" ", "").replace("_", "").isalpha()):
        entries = entries[1:]

    # if any cell in the block clearly uses a comma decimal, a lone dot elsewhere is a thousands separator. otherwise a European "1.234" (1234 m²) quietly shrinks 1000x
    dot_is_thousands = any("," in area and "." not in area
                           for _line, _rid, area in entries)

    rows = []
    auto_id = start_id
    for line_no, row_id, area_text in entries:
        try:
            value = _parse_number(area_text, dot_is_thousands)
        except ValueError:
            raise ValueError(_tr(
                'Line {0}: "{1}" is not a number.').format(
                    line_no, area_text))
        # under 0.0005 it rounds to "0" in the table and fails validation later with a confusing message, so reject it here
        if not math.isfinite(value) or value < 0.0005:
            raise ValueError(_tr(
                "Line {0}: the target area must be a positive number, "
                'got "{1}".').format(line_no, area_text))
        if row_id is None:
            row_id = str(auto_id)
        rows.append((row_id, value))
        auto_id += 1
    return rows


class _DirectionMapTool(QgsMapTool):
    """Two-click direction line picker - first click anchors, a band follows the cursor, second click emits picked(start, end) in canvas CRS. Right-click or Escape emits cancelled."""

    # both clicks go through the project snapping. a custom map tool gets none for free, and this line sets the angle every cut is parallel to, so a pixel of slop at the click is metres of drift at the far end of a long polygon

    picked = pyqtSignal(object, object)  # QgsPointXY, QgsPointXY (map CRS)
    cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._start = None
        self._band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._band.setColor(QColor(219, 30, 42, 220))
        self._band.setWidth(2)
        self._snap_indicator = QgsSnapIndicator(canvas)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _snap(self, pos):
        """Screen position to map coordinate, snapped when something is in range, raw otherwise so the tool still works with snapping off."""
        match = self.canvas().snappingUtils().snapToMap(pos)
        self._snap_indicator.setMatch(match)
        if match.isValid():
            return QgsPointXY(match.point())
        return self.toMapCoordinates(pos)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._reset_state()
            self.cancelled.emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._snap(event.pos())
        if self._start is None:
            self._start = QgsPointXY(point)
            if self._band is not None:
                self._band.reset(QgsWkbTypes.LineGeometry)
                self._band.addPoint(point)
                self._band.addPoint(point)
            return
        if point.sqrDist(self._start) == 0.0:
            return  # same spot twice, keep waiting for a real end
        start = self._start
        self._reset_state()
        self.picked.emit(start, QgsPointXY(point))

    def canvasMoveEvent(self, event):
        # snap on every move, even before the first click - _snap() drives the indicator, so bailing early leaves you aiming the first point with no marker
        point = self._snap(event.pos())
        if self._start is None or self._band is None:
            return
        self._band.movePoint(point)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._reset_state()
            self.cancelled.emit()
            event.accept()

    def deactivate(self):
        self._reset_state()
        super().deactivate()

    def _reset_state(self):
        self._start = None
        self._snap_indicator.setMatch(QgsPointLocator.Match())
        if self._band is not None:
            try:
                self._band.reset(QgsWkbTypes.LineGeometry)
            except RuntimeError:
                self._band = None

    def cleanup(self):
        """Take the rubber band off the canvas. Call before deleting."""
        if self._band is None:
            return
        try:
            self._band.reset()
            self.canvas().scene().removeItem(self._band)
        except RuntimeError:
            pass
        self._band = None


class _AreaDelegate(QStyledItemDelegate):
    """Positive-double editor for the target area column."""

    def createEditor(self, parent, option, index):
        spin = QDoubleSpinBox(parent)
        spin.setDecimals(3)
        spin.setRange(0.0, 1e12)
        spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        return spin

    def setEditorData(self, editor, index):
        try:
            editor.setValue(float(index.data() or 0.0))
        except (TypeError, ValueError):
            editor.setValue(0.0)

    def setModelData(self, editor, model, index):
        editor.interpretText()
        value = editor.value()
        # 0 means "not set" - the run-time validation rejects empty cells
        model.setData(index, "" if value <= 0.0 else _fmt_cell(value))


class DetachPanel(QDockWidget):
    """Instantiate on first use, call cleanup() from unload()."""

    def tr(self, text: str) -> str:
        # one "Vernier" context for the whole plugin, see i18n.py. QObject.tr would use the class name instead and these strings would land outside it
        return _tr(text)

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(self.tr("Split to Target Areas"))
        self.setObjectName("VernierDetachPanel")

        self._layer = None             # layer whose selectionChanged is wired
        self._running = False
        self._splitter_restored = False
        self._direction_points = None  # (QgsPointXY, QgsPointXY) in map CRS
        self._direction_crs = None     # canvas CRS at draw time
        self._previous_tool = None
        self._preview_bands = []

        canvas = iface.mapCanvas()
        self._tool = _DirectionMapTool(canvas)
        self._tool.picked.connect(self._on_direction_picked)
        self._tool.cancelled.connect(self._on_direction_cancelled)
        self._tool.deactivated.connect(self._on_tool_deactivated)

        # the fixed direction line stays on the canvas until it's cleared
        self._direction_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._direction_band.setColor(QColor(30, 100, 220, 220))
        self._direction_band.setWidth(2)
        self._direction_band.setLineStyle(Qt.PenStyle.DashLine)

        self._setup_ui()

        QgsProject.instance().layersWillBeRemoved.connect(
            self._on_layers_removed)

    def _setup_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)

        # three resizable panes - pick the source, fill the table, run. only the table gets leftover space, and nothing collapses: a cramped pane beats losing the layer combo or the Split button to a stray drag
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)

        # pane 1, source layer and cut direction
        setup_pane = QWidget()
        setup_layout = QVBoxLayout(setup_pane)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(_GROUP_SPACING)

        source_group = QGroupBox(self.tr("Source polygon"))
        source_layout = QVBoxLayout()
        source_layout.setContentsMargins(*_GROUP_MARGINS)
        source_layout.setSpacing(_GROUP_SPACING)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        source_layout.addWidget(self.layer_combo)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        source_layout.addWidget(self.source_label)
        source_group.setLayout(source_layout)
        setup_layout.addWidget(source_group)

        self.crs_warning = QLabel(self.tr(
            "Warning: this layer uses a geographic CRS, so areas are in "
            "square degrees. Reproject to a projected CRS to work in "
            "square meters."))
        self.crs_warning.setWordWrap(True)
        self.crs_warning.setStyleSheet(_WARN_STYLE + " padding: 4px;")
        self.crs_warning.setVisible(False)
        setup_layout.addWidget(self.crs_warning)

        direction_group = QGroupBox(self.tr("Cut direction"))
        direction_layout = QVBoxLayout()
        direction_layout.setContentsMargins(*_GROUP_MARGINS)
        direction_layout.setSpacing(_GROUP_SPACING)
        self.direction_label = QLabel()
        self.direction_label.setWordWrap(True)
        direction_layout.addWidget(self.direction_label)
        draw_row = QHBoxLayout()
        self.draw_button = QPushButton(self.tr("Draw direction line"))
        self.draw_button.setCheckable(True)
        self.draw_button.setToolTip(self.tr(
            "Click two points on the map. Clicks snap to the layers set\n"
            "up for snapping in the project. Cuts run parallel to this\n"
            "line, and the first piece comes off the side of the polygon\n"
            "the line was drawn against. Right-click or Escape cancels."))
        self.draw_button.clicked.connect(self._on_draw_clicked)
        draw_row.addWidget(self.draw_button)
        self.clear_direction_button = QPushButton(self.tr("Clear"))
        self.clear_direction_button.clicked.connect(self._clear_direction)
        draw_row.addWidget(self.clear_direction_button)
        draw_row.addStretch()
        direction_layout.addLayout(draw_row)
        direction_group.setLayout(direction_layout)
        setup_layout.addWidget(direction_group)
        setup_layout.addStretch()
        self.splitter.addWidget(setup_pane)

        # pane 2, the target area table
        table_group = QGroupBox(self.tr("Target areas"))
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(*_GROUP_MARGINS)
        table_layout.setSpacing(_GROUP_SPACING)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            [self.tr("ID"), self.tr("Target area")])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setItemDelegateForColumn(1, _AreaDelegate(self.table))
        self.table.setMinimumHeight(_TABLE_MIN_HEIGHT)
        self.table.itemChanged.connect(self._refresh_math)
        table_layout.addWidget(self.table)

        table_buttons = QHBoxLayout()
        self.add_row_button = QPushButton(self.tr("Add row"))
        self.add_row_button.clicked.connect(lambda: self._append_row())
        table_buttons.addWidget(self.add_row_button)
        self.remove_row_button = QPushButton(self.tr("Remove"))
        self.remove_row_button.setToolTip(self.tr("Remove the selected rows"))
        self.remove_row_button.clicked.connect(self._remove_selected_rows)
        table_buttons.addWidget(self.remove_row_button)
        self.paste_button = QPushButton(self.tr("Paste"))
        self.paste_button.setToolTip(self.tr(
            "Append rows from the clipboard: two Excel columns (ID, area)\n"
            "or a single area column with automatic IDs"))
        self.paste_button.clicked.connect(self._paste_rows)
        table_buttons.addWidget(self.paste_button)
        table_buttons.addStretch()
        table_layout.addLayout(table_buttons)
        table_group.setLayout(table_layout)
        self.splitter.addWidget(table_group)

        # pane 3, totals, options and run
        action_pane = QWidget()
        action_layout = QVBoxLayout(action_pane)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(_GROUP_SPACING)

        math_group = QGroupBox(self.tr("Areas"))
        form = QFormLayout()
        form.setContentsMargins(*_GROUP_MARGINS)
        form.setVerticalSpacing(2)
        self.area_source_label = QLabel("-")
        form.addRow(self.tr("Source:"), self.area_source_label)
        self.area_targets_label = QLabel("-")
        form.addRow(self.tr("Targets total:"), self.area_targets_label)
        self.area_remainder_label = QLabel("-")
        form.addRow(self.tr("Remainder:"), self.area_remainder_label)
        math_group.setLayout(form)
        action_layout.addWidget(math_group)

        # no group box here, the frame and title cost more height than the two checkboxes do, and the tooltips carry the detail
        self.mm_check = QCheckBox(self.tr("Round coordinates to mm"))
        self.mm_check.setToolTip(self.tr(
            "Round every output coordinate to 3 decimals (millimeters).\n"
            "Achievable areas are then quantized by the grid, so piece\n"
            "areas may differ from the target by up to {0} square "
            "units.").format(_MM_TOLERANCE))
        action_layout.addWidget(self.mm_check)
        self.split_check = QCheckBox(self.tr("Split disconnected fragments"))
        self.split_check.setToolTip(self.tr(
            "A cut can leave a piece in several disconnected parts.\n"
            "Checked: one feature per part, sharing the same part_id.\n"
            "Unchecked: one multipart feature per piece."))
        action_layout.addWidget(self.split_check)

        run_row = QHBoxLayout()
        self.run_button = QPushButton(self.tr("Split"))
        self.run_button.clicked.connect(self._run)
        run_row.addWidget(self.run_button)
        run_row.addStretch()
        action_layout.addLayout(run_row)
        self.splitter.addWidget(action_pane)

        # only the table grows when the panel gets taller
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        # just a starting hint, the real restore happens in showEvent once setSizes() can actually be honoured
        self.splitter.setSizes(list(_DEFAULT_SPLIT))

        self.setWidget(content)

        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self._preselect_active_layer()
        self._on_layer_changed(self.layer_combo.currentLayer())
        self._append_row()
        self._update_direction_label()

    # --- pane sizes ---

    def _stored_splitter_sizes(self):
        """Saved pane heights, or None. Comma-separated pixels, and a stale value from a build with a different pane count gets ignored rather than half-applied."""
        stored = settings_service.get("detach/splitter_sizes")
        if not stored:
            return None
        try:
            parsed = [int(part) for part in str(stored).split(",")]
        except ValueError:
            return None
        if (len(parsed) == self.splitter.count()
                and all(size > 0 for size in parsed)):
            return parsed
        return None

    def _restore_splitter_state(self):
        """Apply the stored pane heights, _DEFAULT_SPLIT otherwise. Only works once the panel has real geometry, which is why showEvent calls it and not _setup_ui - setSizes() on an un-laid-out splitter gets clamped to its empty height and thrown away."""
        self.splitter.setSizes(
            list(self._stored_splitter_sizes() or _DEFAULT_SPLIT))

    def _save_splitter_state(self):
        """Remember the pane heights, on close and on unload."""
        try:
            sizes = self.splitter.sizes()
        except (AttributeError, RuntimeError):
            return  # never built, or the C++ object is gone
        if sizes and all(size > 0 for size in sizes):
            settings_service.set_("detach/splitter_sizes",
                                  ",".join(str(size) for size in sizes))

    # --- lifecycle ---

    def cleanup(self):
        """Put the map tool back, drop the canvas items, disconnect everything. unload() calls this and the panel is inert afterwards."""
        self._save_splitter_state()
        self._restore_map_tool()
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(
                self._on_layers_removed)
        except (TypeError, RuntimeError):
            pass
        self._disconnect_layer()
        self._clear_preview()
        canvas = None
        try:
            canvas = self.iface.mapCanvas()
        except RuntimeError:
            pass
        if self._direction_band is not None:
            try:
                self._direction_band.reset()
                if canvas is not None:
                    canvas.scene().removeItem(self._direction_band)
            except RuntimeError:
                pass
            self._direction_band = None
        if self._tool is not None:
            self._tool.cleanup()
            self._tool.deleteLater()
            self._tool = None

    def showEvent(self, event):
        # first paint is the earliest the splitter has a real height, so the earliest the stored split can be applied
        super().showEvent(event)
        if not self._splitter_restored:
            self._splitter_restored = True
            self._restore_splitter_state()

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        self._save_splitter_state()
        # _clear_direction puts the map tool back too. closing has to take the guide line with it, a stray rubber band can only be removed by reopening the panel
        self._clear_direction()
        self._clear_preview()
        super().closeEvent(event)

    # --- source layer / selection ---

    def _preselect_active_layer(self):
        """Preselect the active layer, if it got past the combo's filter."""
        active = self.iface.activeLayer()
        if not isinstance(active, QgsVectorLayer):
            return
        for i in range(self.layer_combo.count()):
            layer = self.layer_combo.layer(i)
            if layer is not None and layer.id() == active.id():
                self.layer_combo.setLayer(active)
                return

    def _on_layer_changed(self, layer):
        self._disconnect_layer()
        if isinstance(layer, QgsVectorLayer):
            self._layer = layer
            layer.selectionChanged.connect(self._update_source_info)
        self._update_source_info()

    def _disconnect_layer(self):
        if self._layer is None:
            return
        try:
            self._layer.selectionChanged.disconnect(self._update_source_info)
        except (TypeError, RuntimeError):
            pass  # never connected, or the C++ object is gone
        self._layer = None

    def _on_layers_removed(self, layer_ids):
        if self._layer is None:
            return
        try:
            gone = self._layer.id() in layer_ids
        except RuntimeError:
            gone = True
        if gone:
            self._disconnect_layer()
            self._update_source_info()

    def _source_state(self):
        """(layer, selected count, area of the one selected feature)."""
        layer = self._layer
        if layer is None:
            return None, 0, 0.0
        try:
            count = layer.selectedFeatureCount()
            if count != 1:
                return layer, count, 0.0
            geometry = layer.selectedFeatures()[0].geometry()
        except RuntimeError:
            self._layer = None  # C++ object deleted under us
            return None, 0, 0.0
        area = 0.0
        if geometry is not None and not geometry.isEmpty():
            area = geometry.area()
        return layer, 1, area

    def _update_source_info(self, *args):
        layer, count, area = self._source_state()
        geographic = (layer is not None and layer.crs().isValid()
                      and layer.crs().isGeographic())
        self.crs_warning.setVisible(geographic)
        if layer is None:
            text = self.tr("No polygon layer selected.")
            warn = True
        elif count == 0:
            text = self.tr(
                "No feature selected - select exactly one polygon.")
            warn = True
        elif count > 1:
            text = self.tr(
                "{0} features selected - select exactly one.").format(count)
            warn = True
        else:
            text = self.tr("1 feature selected - area {0}{1}").format(
                _fmt(area), _area_suffix(layer))
            warn = False
        self.source_label.setText(text)
        self.source_label.setStyleSheet(_WARN_STYLE if warn else "")
        self._refresh_math()

    # --- direction line ---

    def _on_draw_clicked(self, checked):
        if checked:
            self._start_drawing()
        else:
            # clicking the checked button again aborts
            self._restore_map_tool()

    def _start_drawing(self):
        canvas = self.iface.mapCanvas()
        current = canvas.mapTool()
        if current is not self._tool:
            self._previous_tool = current
        canvas.setMapTool(self._tool)
        self.direction_label.setText(
            self.tr("Click two points on the map..."))

    def _restore_map_tool(self):
        canvas = None
        try:
            canvas = self.iface.mapCanvas()
        except RuntimeError:
            return
        if self._tool is None or canvas.mapTool() is not self._tool:
            return
        previous = self._previous_tool
        self._previous_tool = None
        try:
            if previous is not None:
                canvas.setMapTool(previous)
            else:
                canvas.unsetMapTool(self._tool)
        except RuntimeError:
            canvas.unsetMapTool(self._tool)  # previous tool got deleted

    def _on_direction_picked(self, start, end):
        self._direction_points = (QgsPointXY(start), QgsPointXY(end))
        self._direction_crs = (
            self.iface.mapCanvas().mapSettings().destinationCrs())
        if self._direction_band is not None:
            self._direction_band.reset(QgsWkbTypes.LineGeometry)
            self._direction_band.addPoint(self._direction_points[0])
            self._direction_band.addPoint(self._direction_points[1])
        self._restore_map_tool()
        self._update_direction_label()

    def _on_direction_cancelled(self):
        self._restore_map_tool()
        self._update_direction_label()

    def _on_tool_deactivated(self):
        # fires on pick, on cancel, and when another tool gets grabbed mid-draw
        self.draw_button.setChecked(False)
        self._update_direction_label()

    def _update_direction_label(self):
        if self._direction_points is None:
            self.direction_label.setText(self.tr("No direction line drawn."))
            return
        a, b = self._direction_points
        azimuth = (math.degrees(math.atan2(b.x() - a.x(), b.y() - a.y()))
                   + 360.0) % 360.0
        self.direction_label.setText(
            self.tr("Direction set - azimuth {0:.1f}°.").format(azimuth))

    def _clear_direction(self):
        self._restore_map_tool()
        self._direction_points = None
        self._direction_crs = None
        if self._direction_band is not None:
            self._direction_band.reset(QgsWkbTypes.LineGeometry)
        self._update_direction_label()

    def _direction_in_layer_crs(self, layer):
        """Direction endpoints in the layer CRS, None if they won't transform."""
        a, b = self._direction_points
        crs = self._direction_crs
        if (crs is None or not crs.isValid()
                or not layer.crs().isValid()):
            return QgsPointXY(a), QgsPointXY(b)
        transform = QgsCoordinateTransform(
            crs, layer.crs(), QgsProject.instance())
        try:
            return transform.transform(a), transform.transform(b)
        except QgsCsException:
            _ui_helpers.show_warning(
                self, self.tr("Split to Target Areas"),
                self.tr("The direction line could not be transformed to "
                        "the layer CRS - draw it again."))
            return None

    # --- divisions table ---

    def _append_row(self, row_id=None, area_text=""):
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(
            str(row + 1) if row_id is None else row_id))
        self.table.setItem(row, 1, QTableWidgetItem(area_text))
        self.table.blockSignals(False)
        self._refresh_math()

    def _remove_selected_rows(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()},
                      reverse=True)
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        for row in rows:
            self.table.removeRow(row)
        self._refresh_math()

    def _row_is_empty(self, row):
        area_item = self.table.item(row, 1)
        if area_item is not None and area_item.text().strip():
            return False
        id_item = self.table.item(row, 0)
        id_text = id_item.text().strip() if id_item is not None else ""
        # a row with only the auto-filled default ID was never touched, counting it as occupied would poison the first paste into a fresh panel
        return not id_text or id_text == str(row + 1)

    def _paste_rows(self):
        text = QApplication.clipboard().text()
        if not text.strip():
            _ui_helpers.show_warning(self, self.tr("Paste"),
                                     self.tr("The clipboard is empty."))
            return
        occupied = sum(1 for row in range(self.table.rowCount())
                       if not self._row_is_empty(row))
        try:
            rows = parse_area_rows(text, start_id=occupied + 1)
        except ValueError as exc:
            _ui_helpers.show_warning(self, self.tr("Paste"), str(exc))
            return
        if not rows:
            _ui_helpers.show_warning(
                self, self.tr("Paste"),
                self.tr("No rows found in the clipboard."))
            return
        # leftover blanks would land between the old and the pasted data
        for row in range(self.table.rowCount() - 1, -1, -1):
            if self._row_is_empty(row):
                self.table.removeRow(row)
        for row_id, value in rows:
            self._append_row(row_id, _fmt_cell(value))
        # show the parsed total, a 1000x separator misparse has to be obvious right away
        total = _fmt(math.fsum(v for _rid, v in rows))
        message = (
            self.tr("Pasted 1 row - targets total {0}.").format(total)
            if len(rows) == 1 else
            self.tr("Pasted {0} rows - targets total {1}.").format(
                len(rows), total))
        _ui_helpers.show_success(message, iface=self.iface, duration=5)

    def _table_rows(self):
        """(id, area) for every non-empty row, in table order. Raises ValueError with a showable message on a row that has an ID but no usable area."""
        rows = []
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            area_item = self.table.item(row, 1)
            id_text = id_item.text().strip() if id_item else ""
            area_text = area_item.text().strip() if area_item else ""
            if not id_text and not area_text:
                continue
            if not area_text:
                raise ValueError(_tr(
                    "Row {0} has no target area.").format(row + 1))
            try:
                value = float(area_text)
            except ValueError:
                raise ValueError(_tr(
                    'Row {0}: "{1}" is not a number.').format(
                        row + 1, area_text))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(_tr(
                    "Row {0}: the target area must be a positive "
                    "number.").format(row + 1))
            rows.append((id_text or str(row + 1), value))
        return rows

    # --- live math ---

    def _refresh_math(self, *args):
        layer, count, source_area = self._source_state()
        suffix = _area_suffix(layer)
        total = 0.0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is None:
                continue
            try:
                value = float(item.text())
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                total += value
        self.area_targets_label.setText(_fmt(total) + suffix)
        if count == 1:
            remainder = source_area - total
            self.area_source_label.setText(_fmt(source_area) + suffix)
            self.area_remainder_label.setText(_fmt(remainder) + suffix)
            self.area_remainder_label.setStyleSheet(
                _OVER_STYLE if remainder < 0.0 else "")
        else:
            self.area_source_label.setText("-")
            self.area_remainder_label.setText("-")
            self.area_remainder_label.setStyleSheet("")

    # --- run ---

    def _run(self):
        if self._running:
            return
        title = self.tr("Split to Target Areas")
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            _ui_helpers.show_warning(
                self, title, self.tr("Select a polygon layer first."))
            return
        try:
            selected = layer.selectedFeatures()
        except RuntimeError:
            _ui_helpers.show_warning(
                self, title, self.tr("Select a polygon layer first."))
            return
        if len(selected) != 1:
            _ui_helpers.show_warning(
                self, title,
                self.tr("Select exactly one feature on the source layer "
                        "({0} selected).").format(len(selected)))
            return
        if self._direction_points is None:
            _ui_helpers.show_warning(
                self, title, self.tr("Draw the direction line first."))
            return
        try:
            rows = self._table_rows()
        except ValueError as exc:
            _ui_helpers.show_warning(self, title, str(exc))
            return
        if not rows:
            _ui_helpers.show_warning(
                self, title, self.tr("Add at least one target area."))
            return
        direction = self._direction_in_layer_crs(layer)
        if direction is None:
            return

        self._clear_preview()
        mm_mode = self.mm_check.isChecked()
        self._running = True
        self.run_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                result = detach_by_areas(
                    selected[0].geometry(), direction,
                    [area for _row_id, area in rows],
                    tolerance=_MM_TOLERANCE if mm_mode else _TOLERANCE,
                    coord_decimals=_MM_DECIMALS if mm_mode else None,
                    split_fragments=self.split_check.isChecked())
            except DetachError as exc:
                _ui_helpers.show_warning(self, title, str(exc))
                return
            except Exception as exc:  # GEOS oddities must not surface as a raw QGIS python-error dialog
                _ui_helpers.show_error(
                    self, title,
                    self.tr("Splitting failed: {0}").format(exc))
                return

            ids = [row_id for row_id, _area in rows]
            output = self._build_output_layer(layer, result, ids)
            if output is None:
                return
            QgsProject.instance().addMapLayer(output)
            self._show_preview(result.pieces, layer)

            areas = ", ".join(_fmt(p.area) for p in result.pieces[:8])
            if len(result.pieces) > 8:
                areas += ", ..."
            _ui_helpers.show_success(
                self.tr('Split into {0} pieces ({1}{2}) - layer "{3}" '
                        "added.").format(
                    len(result.pieces), areas,
                    _area_suffix(layer), output.name()),
                iface=self.iface, duration=6)
            if result.fixed_input:
                self.iface.messageBar().pushMessage(
                    title,
                    self.tr("The source geometry was invalid and was "
                            "repaired before splitting."),
                    level=Qgis.MessageLevel.Warning, duration=8)
        finally:
            QApplication.restoreOverrideCursor()
            self._running = False
            self.run_button.setEnabled(True)

    def _build_output_layer(self, source, result, ids):
        output = QgsVectorLayer(
            "MultiPolygon", f"{source.name()}_split", "memory")
        if not output.isValid():
            _ui_helpers.show_error(
                self, self.tr("Split to Target Areas"),
                self.tr("Could not create the output memory layer."))
            return None
        output.setCrs(source.crs())
        output.dataProvider().addAttributes([
            QgsField("part_id", FIELD_STRING),
            QgsField("area", FIELD_DOUBLE),
        ])
        output.updateFields()
        features = []
        for piece in result.pieces:
            # remainder rows carry index == len(ids), and split fragments of one target share its index and so its part_id
            part_id = (ids[piece.index] if piece.index < len(ids)
                       else "remainder")
            geometry = QgsGeometry(piece.geometry)
            geometry.convertToMultiType()
            feature = QgsFeature(output.fields())
            feature.setGeometry(geometry)
            feature.setAttributes([part_id, round(piece.area, 4)])
            features.append(feature)
        output.dataProvider().addFeatures(features)
        output.updateExtents()
        return output

    # --- canvas preview ---

    def _show_preview(self, pieces, layer):
        canvas = self.iface.mapCanvas()
        for piece in pieces:
            band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
            # keyed on piece.index so fragments of one target share a color, remainder is always grey
            color = QColor(
                _REMAINDER_COLOR if piece.target is None
                else _PREVIEW_COLORS[piece.index % len(_PREVIEW_COLORS)])
            band.setColor(color)
            band.setWidth(2)
            fill = QColor(color)
            fill.setAlpha(70)
            band.setFillColor(fill)
            band.setToGeometry(QgsGeometry(piece.geometry), layer)
            self._preview_bands.append(band)
        canvas.refresh()

    def _clear_preview(self):
        if not self._preview_bands:
            return
        canvas = None
        try:
            canvas = self.iface.mapCanvas()
        except RuntimeError:
            pass
        for band in self._preview_bands:
            try:
                band.reset()
                if canvas is not None:
                    canvas.scene().removeItem(band)
            except RuntimeError:
                pass
        self._preview_bands = []
        if canvas is not None:
            canvas.refresh()
