# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the plugins.qgis.org zip - dist/vernier.<version>.zip with a single top-level vernier/ folder, which is the layout the QGIS installer expects. Development files stay out."""

import configparser
import fnmatch
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "vernier"

EXCLUDE_DIRS = {".git", ".github", "__pycache__", "test", "scripts",
                "libs", "dist", ".vscode", ".idea",
                ".pytest_cache", ".mypy_cache", ".ruff_cache",
                ".venv", "venv", "node_modules"}
# these configure the repo, not the installed plugin, so they're noise in a user-facing package
EXCLUDE_FILES = {"JOURNAL.md", ".gitignore", ".gitattributes", ".bandit"}
# *.db because QGIS drops a ~900 KB stock symbology-style.db into whatever directory it runs from, and it would otherwise ride along
EXCLUDE_PATTERNS = ("*.pyc", "*.pyo", "*.zip", "*.db")


def plugin_version() -> str:
    parser = configparser.ConfigParser()
    parser.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    return parser.get("general", "version")


def wanted(name: str) -> bool:
    if name in EXCLUDE_FILES:
        return False
    return not any(fnmatch.fnmatch(name, p) for p in EXCLUDE_PATTERNS)


def build() -> str:
    version = plugin_version()
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, f"{PACKAGE}.{version}.zip")

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in sorted(files):
                if not wanted(name):
                    continue
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, ROOT)
                zf.write(path, os.path.join(PACKAGE, rel))
                count += 1
    size_kb = os.path.getsize(out) / 1024
    print(f"{out}\n{count} files, {size_kb:.0f} KB")
    return out


if __name__ == "__main__":
    build()
