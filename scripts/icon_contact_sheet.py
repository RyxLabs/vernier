# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render every icon at four sizes on both backgrounds into dist/icon_contact_sheet.html. test_icon_system checks the measurable rules, this is for the look-at-it check tests can't make."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import build_icon_themes, icon_tokens  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(ROOT, "icons")
SIZES = (16, 24, 32, 48)

PAGE = """<!doctype html><meta charset="utf-8">
<title>Vernier icon contact sheet</title>
<style>
 body {{ font: 13px/1.5 system-ui, sans-serif; margin: 0; }}
 section {{ padding: 24px 32px; }}
 h2 {{ font-size: 14px; letter-spacing: 2px; text-transform: uppercase;
       font-weight: 500; margin: 0 0 20px; opacity: .75; }}
 .light {{ background: {light}; color: #272324; }}
 .dark  {{ background: {night}; color: #B8B8C4; }}
 .grid  {{ display: flex; flex-wrap: wrap; gap: 20px; }}
 figure {{ margin: 0; text-align: center; width: 140px; }}
 .row   {{ display: flex; align-items: flex-end; justify-content: center;
           gap: 8px; height: 56px; }}
 figcaption {{ margin-top: 6px; font-size: 11px; opacity: .7;
               word-break: break-all; }}
</style>
{body}
"""


def _figure(icons_dir, name):
    src = os.path.relpath(os.path.join(icons_dir, name), ROOT).replace(
        os.sep, "/")
    imgs = "".join(
        '<img src="../{0}" width="{1}" height="{1}" alt="">'.format(src, size)
        for size in SIZES)
    return ('<figure><div class="row">{0}</div>'
            '<figcaption>{1}</figcaption></figure>'.format(imgs, name))


def render(icons_dir=ICONS):
    """The contact-sheet HTML for both themes."""
    names = sorted(n for n in os.listdir(icons_dir) if n.endswith(".svg"))
    light = "".join(_figure(icons_dir, n) for n in names)

    dark_dir = os.path.join(icons_dir, "dark")
    dark = "".join(_figure(dark_dir, n) for n in names
                   if n not in build_icon_themes.SKIP)
    # the mark sits on an opaque tile so the same file is right in both sections, and it's shown here to confirm exactly that
    dark += "".join(_figure(icons_dir, n) for n in names
                    if n in build_icon_themes.SKIP)

    body = ('<section class="light"><h2>Light theme &mdash; {bgl}</h2>'
            '<div class="grid">{0}</div></section>'
            '<section class="dark"><h2>Dark theme &mdash; {bgd}</h2>'
            '<div class="grid">{1}</div></section>').format(
                light, dark, bgl=icon_tokens.BG_DEFAULT,
                bgd=icon_tokens.BG_NIGHT)
    return PAGE.format(light=icon_tokens.BG_DEFAULT,
                       night=icon_tokens.BG_NIGHT, body=body)


if __name__ == "__main__":
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "icon_contact_sheet.html")
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(render())
    print(out)
