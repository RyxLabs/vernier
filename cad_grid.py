# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CAD-style scalable grid overlay for the map canvas - minor/major lines plus origin axes, interval scales with zoom."""

import math

from qgis.PyQt.QtCore import QRectF, Qt  # type: ignore
from qgis.PyQt.QtGui import QColor, QPen  # type: ignore
from qgis.core import QgsPointXY  # type: ignore
from qgis.gui import QgsMapCanvasItem  # type: ignore

# dark CAD background, set as the canvas background brush next to a transparent render background
CANVAS_BG = QColor(0x21, 0x28, 0x30)


class CadGrid(QgsMapCanvasItem):
    # colors tuned for the dark canvas theme
    _PEN_MINOR = QPen(QColor(0x23, 0x2A, 0x33), 1, Qt.PenStyle.SolidLine)
    _PEN_MAJOR = QPen(QColor(0x30, 0x36, 0x45), 1, Qt.PenStyle.SolidLine)
    _PEN_AXIS_X = QPen(QColor(0x8C, 0x23, 0x23), 1, Qt.PenStyle.SolidLine)  # Red (X)
    _PEN_AXIS_Y = QPen(QColor(0x23, 0x6B, 0x23), 1, Qt.PenStyle.SolidLine)  # Green (Y)
    _MAJOR_EVERY = 5

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        # below the map render so features draw on top. only shows through while the render background is transparent - CAD Mode owns those colors, we just paint lines
        self.setZValue(-15)
        self._visible = True
        canvas.extentsChanged.connect(self._on_extent_changed)
        self.updateCanvas()

    @staticmethod
    def _nice_interval(raw):
        """Round a raw interval to a 'nice' number (1, 2, 5 x 10^n)."""
        if raw <= 0:
            return 1.0
        exp = math.floor(math.log10(raw))
        base = 10 ** exp
        normalized = raw / base
        if normalized <= 1.5:
            return base
        elif normalized <= 3.5:
            return 2 * base
        elif normalized <= 7.5:
            return 5 * base
        else:
            return 10 * base

    def _on_extent_changed(self):
        self.updateCanvas()

    def paint(self, painter, option, widget=None):
        if not self._visible:
            return
        extent = self._canvas.extent()
        if extent.isEmpty():
            return

        span = max(extent.width(), extent.height())
        if span <= 0:
            return

        # bail if lines would land under 3px apart. canvas keeps the extent aspect ratio, so pair extent width with canvas width
        canvas_w = self._canvas.width() or 1
        pixel_size = extent.width() / canvas_w
        min_gap = pixel_size * 3

        major_interval = self._nice_interval(span / 20.0)
        minor_interval = major_interval / self._MAJOR_EVERY

        # minor lines too dense, collapse to major-only
        collapsed = minor_interval < min_gap
        if collapsed:
            minor_interval = major_interval
        if minor_interval < min_gap:
            return

        x_min, x_max = extent.xMinimum(), extent.xMaximum()
        y_min, y_max = extent.yMinimum(), extent.yMaximum()
        # after a collapse every line left sits on a major multiple, so none of them may fall back to the near-invisible minor pen
        m = 1 if collapsed else self._MAJOR_EVERY

        # snap the first line onto an interval multiple
        x0 = math.floor(x_min / minor_interval) * minor_interval
        y0 = math.floor(y_min / minor_interval) * minor_interval

        # interval rounding never lets minor drop below span/175, so this stays under ~180 lines per axis
        nx = int((x_max - x0) / minor_interval) + 2
        ny = int((y_max - y0) / minor_interval) + 2

        # where local index 0 lands in the global grid, major lines fall out of a modulo on it
        gx0 = round(x0 / minor_interval)
        gy0 = round(y0 / minor_interval)

        # vertical
        for n in range(nx):
            x = x0 + n * minor_interval
            painter.setPen(
                self._PEN_MAJOR if (gx0 + n) % m == 0 else self._PEN_MINOR)
            p1 = self.toCanvasCoordinates(QgsPointXY(x, y_min))
            p2 = self.toCanvasCoordinates(QgsPointXY(x, y_max))
            painter.drawLine(p1.toPoint(), p2.toPoint())

        # horizontal
        for n in range(ny):
            y = y0 + n * minor_interval
            painter.setPen(
                self._PEN_MAJOR if (gy0 + n) % m == 0 else self._PEN_MINOR)
            p1 = self.toCanvasCoordinates(QgsPointXY(x_min, y))
            p2 = self.toCanvasCoordinates(QgsPointXY(x_max, y))
            painter.drawLine(p1.toPoint(), p2.toPoint())

        # origin axes on top of the grid
        if y_min <= 0 <= y_max:
            painter.setPen(self._PEN_AXIS_X)
            p1 = self.toCanvasCoordinates(QgsPointXY(x_min, 0))
            p2 = self.toCanvasCoordinates(QgsPointXY(x_max, 0))
            painter.drawLine(p1.toPoint(), p2.toPoint())

        if x_min <= 0 <= x_max:
            painter.setPen(self._PEN_AXIS_Y)
            p1 = self.toCanvasCoordinates(QgsPointXY(0, y_min))
            p2 = self.toCanvasCoordinates(QgsPointXY(0, y_max))
            painter.drawLine(p1.toPoint(), p2.toPoint())

    def boundingRect(self):
        return QRectF(self._canvas.rect())

    def cleanup(self):
        """Disconnect signals and remove the item from the canvas scene."""
        try:
            self._canvas.extentsChanged.disconnect(self._on_extent_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            scene = self._canvas.scene()
            if scene:
                scene.removeItem(self)
        except RuntimeError:
            pass  # canvas C++ object already deleted at shutdown

    def set_visible(self, visible):
        """Show or hide the grid without destroying it."""
        self._visible = visible
        self.setVisible(visible)
        self.updateCanvas()

    def is_visible(self):
        return self._visible
