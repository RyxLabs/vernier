# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional dependencies - pip packages into a private profile folder, plus external CAD tools that are only ever located on disk, never downloaded."""

# ezdxf depends on numpy, which QGIS ships. letting pip resolve that can replace QGIS's numpy and break startup, so it goes in with --no-deps plus its pure-Python companions listed by hand, and the folder is appended to the END of sys.path so QGIS always wins a collision

import importlib
import os
import sys

from qgis.core import (  # type: ignore
    Qgis, QgsApplication, QgsMessageLog, QgsSettings,
)

from ..i18n import tr as _tr

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _libs_dir() -> str:
    """Where pip --target installs land, <QGIS profile>/vernier_libs. Not the plugin folder, the installer wipes that on every update and a system-wide one is read-only - it's only the fallback for imports outside a running QGIS."""
    profile = QgsApplication.qgisSettingsDirPath() or ""
    if not profile or not os.path.isdir(profile):
        return os.path.join(PLUGIN_DIR, "libs")
    return os.path.join(profile, "vernier_libs")


LIBS_DIR = _libs_dir()

PLUGIN_NAME = "Vernier"

# shown to the user, never fetched automatically
ODA_DOWNLOAD_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"

# import name -> (pip name, --no-deps?, pure-Python companions). --no-deps keeps pip off QGIS's numpy, so ezdxf's own pure-Python deps get listed here instead
PACKAGES = {
    "ezdxf": ("ezdxf", True, ("pyparsing", "fonttools", "typing_extensions")),
}

# what pip actually gets asked for. the names above stay bare so they read well in messages, the upper bound here stops a future major release breaking an install that works today
SPECS = {
    "ezdxf": "ezdxf>=1.1,<2",
    "pyparsing": "pyparsing>=3,<4",
    "fonttools": "fonttools>=4,<5",
    "typing_extensions": "typing_extensions>=4,<5",
}


def _log(message: str, level=Qgis.Info):
    QgsMessageLog.logMessage(message, PLUGIN_NAME, level=level)


# --- sys.path bootstrap ---

def bootstrap_sys_path():
    """Make the pip --target packages importable, idempotent. Called from the plugin's __init__.py so they're found before any `import ezdxf`, and appended rather than inserted so QGIS's own packages win a collision."""
    # a hand-made libs/ inside the plugin folder counts too
    for path in (LIBS_DIR, os.path.join(PLUGIN_DIR, "libs")):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)


# --- QGIS python interpreter ---

def find_python():
    """The real python.exe of this QGIS install, or None. Inside QGIS sys.executable is qgis.exe, which pip can't run under, so this walks the known layouts - None means the caller falls back to manual instructions."""
    from pathlib import Path

    if sys.platform != "win32":
        return sys.executable

    exe_dir = Path(sys.executable).parent
    prefix = Path(sys.prefix)
    candidates = [
        exe_dir / "python.exe",
        exe_dir / "python3.exe",
        prefix / "python.exe",
        prefix / "python3.exe",
        prefix / "Scripts" / "python.exe",
    ]
    # standalone layout, ...\apps\qgis\bin\qgis.exe sits next to ...\apps\PythonXX\python.exe
    for v in ("Python313", "Python312", "Python311", "Python310", "Python39"):
        candidates.append(exe_dir.parent.parent / "apps" / v / "python.exe")
        candidates.append(exe_dir.parent / "apps" / v / "python.exe")
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            pass

    # last resort, scan apps/ for any python3*
    apps = exe_dir.parent.parent / "apps"
    try:
        if apps.is_dir():
            for child in sorted(apps.iterdir(), reverse=True):
                if child.name.lower().startswith("python3"):
                    py = child / "python.exe"
                    if py.is_file():
                        return str(py)
    except OSError:
        pass

    # nothing found. running "-m pip" under qgis.exe would spawn a second hidden QGIS instead of installing anything
    return None


# --- python packages ---

def is_installed(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _run_pip(args, timeout=180):
    """Run pip without flashing a console window on Windows."""
    import subprocess
    # explicit encoding - text=True alone picks the locale codec, and one stray byte in pip's output raises UnicodeDecodeError and reports a good install as a failure
    kwargs = dict(capture_output=True, text=True, timeout=timeout,
                  encoding="utf-8", errors="replace")
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    return subprocess.run(args, **kwargs)


def _pip_install(python_exe, pip_name, no_deps, companions):
    """pip --target into Vernier's libs folder, returns (ok, error_message). --target only: the consent dialog promises the install doesn't touch the QGIS installation, so escalating to --user or a system install would break that promise and can corrupt QGIS's own packages."""
    base = [python_exe, "-m", "pip", "install", "--quiet",
            "--no-warn-script-location", "--disable-pip-version-check",
            # the GUI thread waits on pip, so an offline machine gives up in seconds instead of retrying for minutes
            "--retries", "1", "--timeout", "15"]
    extra = ["--no-deps"] if no_deps else []
    pkgs = [SPECS.get(n, n) for n in [pip_name] + list(companions)]

    try:
        # inside the try, an unwritable target directory should reach the user as the manual-install message and not a traceback
        os.makedirs(LIBS_DIR, exist_ok=True)
        r = _run_pip(base + ["--target", LIBS_DIR] + extra + pkgs)
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or r.stdout or "").strip()
    except Exception as e:
        err = str(e)
    _log(f"pip --target install failed: {err[:200]}", Qgis.Warning)
    return False, err


def ensure(module: str, parent=None) -> bool:
    """Import check at point of use, offering to pip-install what's missing. True when the module is importable afterwards, and nothing gets installed without consent."""
    bootstrap_sys_path()
    if is_installed(module):
        return True

    # deferred, this module stays QtWidgets-free unless a GUI flow lands here
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QApplication, QMessageBox

    pip_name, no_deps, companions = PACKAGES.get(module, (module, False, ()))
    if companions:
        offer = _tr("Install '{0}' and its pure-Python dependencies ({1}) "
                    "from PyPI into Vernier's own folder in the QGIS "
                    "user profile?").format(
                        pip_name, ", ".join(companions))
    else:
        offer = _tr("Install '{0}' from PyPI into Vernier's own folder "
                    "in the QGIS user profile?").format(pip_name)
    reply = QMessageBox.question(
        parent, _tr("Missing component"),
        _tr("This feature needs the Python package '{0}', which is not "
            "installed.\n\n{1}\n\nIt needs an internet connection and "
            "does not touch the QGIS installation. QGIS stays busy until "
            "the download finishes.").format(pip_name, offer),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
    if reply != QMessageBox.StandardButton.Yes:
        return False

    python_exe = find_python()
    if python_exe is None:
        _log("no QGIS python interpreter found for pip install",
             Qgis.Warning)
        ok = False
        err = _tr("The Python interpreter of this QGIS install was not "
                  "found.")
    else:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, err = _pip_install(python_exe, pip_name, no_deps,
                                   companions)
        finally:
            QApplication.restoreOverrideCursor()

    # make the fresh install importable without a restart, if we can
    bootstrap_sys_path()
    importlib.invalidate_caches()

    if ok and is_installed(module):
        return True
    if ok:
        QMessageBox.information(
            parent, _tr("Restart required"),
            _tr("'{0}' was installed but needs a QGIS restart to become "
                "available.").format(pip_name))
        return False
    # OSGeo4W is Windows-only
    if sys.platform == "win32":
        hint = _tr("You can install it manually from the OSGeo4W Shell:")
    else:
        hint = _tr("You can install it manually from a terminal with the "
                   "QGIS Python environment active:")
    QMessageBox.warning(
        parent, _tr("Install failed"),
        _tr("Could not install '{0}'.\n\n{1}\n  python -m pip install "
            "{0}\n\n{2}").format(pip_name, hint, (err or "")[:300]))
    return False


# --- external tools, never pip ---

def _which_on_path(name: str):
    """Find an executable on PATH and nowhere else - shutil.which() checks os.curdir first on Windows, so something planted next to the drawing being imported would get found and run."""
    if sys.platform == "win32":
        exts = [e for e in (os.environ.get("PATHEXT")
                            or ".EXE").split(os.pathsep) if e]
        names = [name + e for e in exts]
        if os.path.splitext(name)[1]:
            names.insert(0, name)
    else:
        names = [name]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        for candidate in names:
            full = os.path.join(entry, candidate)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    return None


def _tool_key(name: str) -> str:
    return f"Vernier/tools/{name}"


def _cached_tool_path(name: str):
    path = QgsSettings().value(_tool_key(name))
    if path and os.path.isfile(path):
        return path
    return None


def _cache_tool_path(name: str, path: str):
    QgsSettings().setValue(_tool_key(name), path)


def find_oda_converter():
    """Locate ODA File Converter for DWG -> DXF, or None. Cached path first, then PATH, then the usual install locations."""
    import glob
    cached = _cached_tool_path("oda")
    if cached:
        return cached
    oda = _which_on_path("ODAFileConverter")
    if oda:
        return oda
    candidates = [
        r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        "/usr/bin/ODAFileConverter",
        "/usr/local/bin/ODAFileConverter",
    ]
    # ODA installs into versioned folders, "ODAFileConverter 25.6.0"
    candidates += sorted(
        glob.glob(r"C:\Program Files\ODA\*\ODAFileConverter.exe"),
        reverse=True)
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def locate_oda_dialog(parent=None):
    """Let the user point at ODAFileConverter by hand, and remember where."""
    # deferred, keeps the module import UI-free
    from qgis.PyQt.QtWidgets import QFileDialog

    pattern = ("ODAFileConverter.exe" if sys.platform == "win32"
               else "ODAFileConverter*")
    flt = _tr("ODA File Converter") + f" ({pattern})"
    path, _ = QFileDialog.getOpenFileName(
        parent, _tr("Locate ODA File Converter"), "",
        f"{flt};;" + _tr("All files (*)"))
    if path and os.path.isfile(path):
        _cache_tool_path("oda", path)
        return path
    return None


def find_ogr2ogr():
    """Locate the ogr2ogr that ships with QGIS, or None. Seven places to look."""
    import glob
    exe = "ogr2ogr.exe" if sys.platform == "win32" else "ogr2ogr"
    # 1. the QGIS prefix, most reliable inside a running QGIS
    prefix = QgsApplication.prefixPath()
    if prefix:
        c = os.path.join(prefix, "bin", exe)
        if os.path.isfile(c):
            return c
    # 2. PATH
    p = _which_on_path("ogr2ogr")
    if p:
        return p
    # 3. QGIS_PREFIX_PATH
    qp = os.environ.get("QGIS_PREFIX_PATH", "")
    if qp:
        for rel in ("bin", os.path.join("..", "bin")):
            c = os.path.join(qp, rel, exe)
            if os.path.isfile(c):
                return c
    # 4. OSGeo4W
    if sys.platform == "win32":
        for base in (r"C:\OSGeo4W64\bin", r"C:\OSGeo4W\bin"):
            c = os.path.join(base, exe)
            if os.path.isfile(c):
                return c
        # 5. any standalone QGIS 3.x install, newest first
        matches = glob.glob(r"C:\Program Files\QGIS 3.*\bin\ogr2ogr.exe")
        if matches:
            matches.sort(reverse=True)
            return matches[0]
    # 6. linux system paths
    for p in ("/usr/bin/ogr2ogr", "/usr/local/bin/ogr2ogr"):
        if os.path.isfile(p):
            return p
    # 7. next to the interpreter
    pdir = os.path.dirname(sys.executable)
    for name in (exe, "ogr2ogr"):
        for rel in ("", "..", os.path.join("..", "Scripts"),
                    os.path.join("..", "bin")):
            c = os.path.abspath(os.path.join(pdir, rel, name))
            if os.path.isfile(c):
                return c
    return None
