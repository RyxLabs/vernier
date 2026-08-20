# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared vocabulary for the icon system - the token palette, the light to dark substitution map, WCAG contrast maths and the SVG text probes. build_icon_themes applies the substitutions and test_icon_system enforces them, so a palette change lands in one place."""

import re

# --- the palette, names from the RyxLabs brand palette ---

FG_LIGHT = "#272324"                    # Dark
FG_DARK = "#B8B8C4"                     # Light
AC_LIGHT = "#E6202E"                    # Red
AC_DARK = "#FF4554"                     # Red Bright

# "Red Subtle" is a tint of the accent expressed as an opacity on it, not a stylistic call - Qt's SVG renderer is SVG Tiny 1.2 and does NOT parse CSS rgba(), it silently falls back to solid black (checked through QSvgRenderer). fill-opacity is the SVG 1.1 spelling, renders right, and lets the substitution below carry the tint across themes for free
SOFT_OPACITY = "0.12"

# no token is a prefix of another so the order doesn't matter, it's fixed only to keep generated output byte-stable across runs
SUBSTITUTIONS = (
    (FG_LIGHT, FG_DARK),
    (AC_LIGHT, AC_DARK),
)

LIGHT_TOKENS = frozenset((FG_LIGHT, AC_LIGHT, "none"))
DARK_TOKENS = frozenset((FG_DARK, AC_DARK, "none"))

# functional color notation is out - rgba() renders as solid black in QtSvg, and rgb()/hsl() are inconsistent across the Qt5 and Qt6 renderers we have to satisfy
FORBIDDEN_NOTATION = ("rgba(", "rgb(", "hsl(")

# reference toolbar backgrounds - QGIS Default, Night Mapping, brand black
BG_DEFAULT = "#F0F0F0"
BG_NIGHT = "#333333"
BG_BLACK = "#0A0A0B"


# --- WCAG contrast ---

def _channel(value):
    if value <= 0.03928:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_color):
    """WCAG relative luminance of a #rrggbb string."""
    channels = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    red, green, blue = (_channel(c) for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    """WCAG contrast ratio, 1.0 to 21.0. Icons want at least 3:1."""
    values = (luminance(first), luminance(second))
    return (max(values) + 0.05) / (min(values) + 0.05)


# --- SVG text probes ---

# text probes rather than a DOM walk. the icons are flat hand-authored files, and matching the literal attribute text is what lets an error quote the offending snippet back at whoever wrote it

_COLOR = re.compile(r'(?<![-\w])(?:fill|stroke)="([^"]+)"')
_STROKE_WIDTH = re.compile(r'stroke-width="([^"]+)"')

# positional attributes only, path "d" data is exempt on purpose - arc flags, relative deltas and Bezier control points aren't pixel-aligned quantities, so scanning them is all noise
_GRID_ATTRS = ("x", "y", "width", "height", "cx", "cy", "r", "rx", "ry",
               "x1", "y1", "x2", "y2")
# the lookbehind matters - a plain \b lets "stroke-width" match the "width" alternative, and a bad stroke weight gets misreported as a grid offence
_POSITIONAL = re.compile(
    r'(?<![-\w])(' + "|".join(_GRID_ATTRS) + r')="(-?\d+(?:\.\d+)?)"')
_POINTS = re.compile(r'(?<![-\w])points="([^"]+)"')
_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')


def svg_colors(text):
    """Every fill and stroke value in there, "none" included."""
    return set(_COLOR.findall(text))


def svg_stroke_widths(text):
    """Every stroke-width in there, as floats."""
    return {float(w) for w in _STROKE_WIDTH.findall(text)}


def _off_grid(value):
    return (float(value) * 2.0) % 1.0 != 0.0


def svg_grid_offenders(text):
    """Attribute snippets whose numbers aren't on a 0.5 step."""
    offenders = []
    for name, value in _POSITIONAL.findall(text):
        if _off_grid(value):
            offenders.append('{0}="{1}"'.format(name, value))
    for value in _POINTS.findall(text):
        if any(_off_grid(n) for n in _NUMBER.findall(value)):
            offenders.append('points="{0}"'.format(value))
    return offenders
