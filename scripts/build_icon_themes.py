# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate icons/dark/ from the hand-authored light icons - the sources carry real colors, not placeholder tokens, so they open correctly in a browser or Inkscape, and this rewrites the two color tokens to their dark equivalents."""

# strict on purpose: a source carrying any color outside the token set is an error, not a pass-through. that's what stops a stray hex surviving into the dark set as an unreadable color

# the output is committed, because QGIS installs plugins from a zip with no build step

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import icon_tokens  # noqa: E402

ICONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")

# the plugin mark sits on an opaque dark tile, so it's already theme-proof and must not get recolored
SKIP = frozenset(("vernier.svg",))


class IconError(Exception):
    """A source icon broke the token palette."""


def to_dark(source):
    """The dark-theme form of a light-palette SVG string."""
    result = source
    for light, dark in icon_tokens.SUBSTITUTIONS:
        result = result.replace(light, dark)
    return result


def _check(name, source):
    for notation in icon_tokens.FORBIDDEN_NOTATION:
        if notation in source:
            raise IconError(
                "{0}: uses {1}) notation - QtSvg does not parse it and "
                "falls back to solid black. Use a hex token plus "
                "fill-opacity instead.".format(name, notation))
    extra = icon_tokens.svg_colors(source) - icon_tokens.LIGHT_TOKENS
    if extra:
        raise IconError(
            "{0}: untokenised color(s) {1} - the light palette is "
            "{2}".format(name, ", ".join(sorted(extra)),
                         ", ".join(sorted(icon_tokens.LIGHT_TOKENS))))


def build(icons_dir=ICONS):
    """Regenerate the dark folder, returning what got written."""
    dark_dir = os.path.join(icons_dir, "dark")
    os.makedirs(dark_dir, exist_ok=True)

    written = []
    for name in sorted(os.listdir(icons_dir)):
        if not name.endswith(".svg") or name in SKIP:
            continue
        path = os.path.join(icons_dir, name)
        with open(path, encoding="utf-8") as fp:
            source = fp.read()
        _check(name, source)
        with open(os.path.join(dark_dir, name), "w",
                  encoding="utf-8", newline="\n") as fp:
            fp.write(to_dark(source))
        written.append(name)

    # drop dark files whose source got renamed or deleted
    for name in sorted(os.listdir(dark_dir)):
        if name.endswith(".svg") and name not in written:
            os.remove(os.path.join(dark_dir, name))

    return written


if __name__ == "__main__":
    names = build()
    print("{0} dark icons written to icons/dark/".format(len(names)))
