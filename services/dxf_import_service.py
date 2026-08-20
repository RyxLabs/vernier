# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""DXF/DWG import - ODA converts DWG to DXF, ogr2ogr turns that into a GPKG inside GDAL rather than per-entity Python, ezdxf reads just the layer color table, then a QML gets written per CAD layer and geometry class."""

# the ogr2ogr and ODA paths come from the caller via services.deps, so this module never hunts for binaries itself and tests can hand it stubs

import contextlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from qgis.core import Qgis, QgsMessageLog, QgsTask  # type: ignore

from ..i18n import tr as _tr
from .dxf_symbology import aci_rgb, lt_dash, lw_mm, make_qml

PLUGIN_NAME = "Vernier"

# the OGR DXF driver hands back one mixed "entities" layer, so the conversion tags every entity with its geometry class here - it's what separates text and nodes from linework. the name is spelled out in the SQL below, so the two move together
GEOM_FIELD = "GeomType"
ENTITIES_SQL = "SELECT *, OGR_GEOMETRY AS GeomType FROM entities"


# --- CAD noise-layer filtering ---

# some layer names are drawing noise once they land in GIS - construction points, contour output, grids, free annotation, hatch fills. matching is case-insensitive substring, and the keyword list lives in settings_service under dxf_import/skip_keywords


def is_skipped_layer(layer_name, keywords):
    """True when the CAD layer name carries any of the skip keywords."""
    upper = str(layer_name).upper()
    return any(kw in upper for kw in keywords)


# --- geometry classes ---

# OGR geometry name -> (QML template, uri geometrytype token). anything unlisted keeps the line symbol and gets no uri filter
# the tokens are the 25D forms because CAD geometry is 2.5D - a DXF point carries an elevation even when the drawing is flat, and ogr2ogr stores it as PointZ. a plain "Point" token would declare the layer 2D, and QgsVectorFileWriter builds an export from the declared type, so the elevations would be dropped on the way out
_GEOM_CLASSES = {
    "POINT": ("points", "Point25D"),
    "MULTIPOINT": ("points", "MultiPoint25D"),
    "LINESTRING": ("lines", "LineString25D"),
    "MULTILINESTRING": ("lines", "MultiLineString25D"),
    "POLYGON": ("polygons", "Polygon25D"),
    "MULTIPOLYGON": ("polygons", "MultiPolygon25D"),
}


def geom_style(geom_name, count, text_count):
    """(QML template, is_text, uri geometrytype) for a geometry class. CAD text arrives as point entities carrying a Text value, so a mostly-text point group gets labeled instead of symbolized."""
    gtype, uri_type = _GEOM_CLASSES.get(str(geom_name).upper(),
                                        ("lines", ""))
    is_text = gtype == "points" and text_count * 2 >= count
    return ("texts" if is_text else gtype), is_text, uri_type


# --- layer styles from an ezdxf doc ---

def read_layer_styles(doc):
    """The layer table off an ezdxf document, as {layer_name: {aci, r, g, b, lw_mm, dash}}."""
    styles = {}
    for lyr in doc.layers:
        name = lyr.dxf.name
        aci = abs(lyr.color)
        lw = lyr.dxf.get("lineweight", -3)
        lt = lyr.dxf.get("linetype", "Continuous")
        r, g, b = aci_rgb(aci)
        styles[name] = {
            "aci": aci, "r": r, "g": g, "b": b,
            "lw_mm": lw_mm(lw), "dash": lt_dash(lt),
        }
    return styles


# --- subprocess helper ---

# ODA rewrites the whole drawing, ogr2ogr parses every entity in it
ODA_TIMEOUT = 120
OGR2OGR_TIMEOUT = 300


def _run_hidden(cmd, timeout):
    """subprocess.run without flashing a console window on Windows."""
    kwargs = dict(capture_output=True, text=True, timeout=timeout)
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    # nosec B603 - cmd is an argv list with no shell. the ogr2ogr and converter paths come from services.deps and the drawing path from the user's file dialog, but argv elements reach the process untouched, so there is nothing to inject into
    return subprocess.run(cmd, **kwargs)  # nosec B603


def _silent_remove(path):
    """Delete a file, shrugging off a missing one or a Windows lock."""
    try:
        os.remove(path)
    except OSError:
        pass


# --- ODA File Converter, DWG to DXF ---

def convert_dwg_to_dxf(dwg_path, oda_exe):
    """Convert a DWG to DXF with ODA File Converter. (dxf_path, None) on success, with the DXF in a fresh temp dir the caller cleans up, or (None, reason) with a showable message so a timeout or a refused drawing doesn't get reported as missing software."""
    if not oda_exe:
        return None, _tr(
            "ODA File Converter was not found. Install it, then point "
            "Vernier at ODAFileConverter.exe and retry.")
    in_dir = os.path.dirname(os.path.abspath(dwg_path))
    out_dir = tempfile.mkdtemp(prefix="vernier_dwg_")
    fname = os.path.basename(dwg_path)
    try:
        result = _run_hidden(
            [oda_exe, in_dir, out_dir, "ACAD2018", "DXF", "0", "1", fname],
            timeout=ODA_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(out_dir, ignore_errors=True)
        QgsMessageLog.logMessage(
            f"ODA conversion timed out after {ODA_TIMEOUT}s",
            PLUGIN_NAME, Qgis.MessageLevel.Critical)
        return None, _tr(
            "The DWG conversion timed out after {0} seconds - the "
            "drawing may be too large, or ODA File Converter is waiting "
            "on a dialog of its own.").format(ODA_TIMEOUT)
    except Exception as e:
        shutil.rmtree(out_dir, ignore_errors=True)
        QgsMessageLog.logMessage(
            f"ODA conversion failed: {e}", PLUGIN_NAME, Qgis.MessageLevel.Critical)
        return None, _tr(
            "ODA File Converter could not be started: {0}").format(e)

    base = os.path.splitext(fname)[0]
    dxf_out = os.path.join(out_dir, base + ".dxf")
    if os.path.isfile(dxf_out):
        return dxf_out, None
    # ODA sometimes renames the output, so take the first .dxf
    for f in os.listdir(out_dir):
        if f.lower().endswith(".dxf"):
            return os.path.join(out_dir, f), None

    shutil.rmtree(out_dir, ignore_errors=True)
    QgsMessageLog.logMessage(
        f"ODA exit code {result.returncode}, no DXF written. stderr: "
        f"{(result.stderr or '').strip()[:500]}",
        PLUGIN_NAME, Qgis.MessageLevel.Critical)
    return None, _tr(
        "ODA File Converter wrote no DXF (exit code {0}). Check Log "
        "Messages > Vernier for its output.").format(result.returncode)


# --- background import task ---

# QML sidecar names come from CAD layer names, which can hold filesystem-illegal characters
_FS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class DXFImportTask(QgsTask):
    """DXF/DWG to styled GPKG, off the UI thread. skip_keywords is a set of substrings or None to keep everything, and finished_cb runs back on the main thread through QgsTask.finished()."""

    def __init__(self, dxf_path, output_path, simplify, crs_def,
                 skip_keywords, ogr2ogr_path, oda_path, finished_cb):
        super().__init__(
            f"Import DXF: {os.path.basename(dxf_path)}",
            QgsTask.Flag.CanCancel,
        )
        self.dxf_path = dxf_path
        self.output_path = output_path
        self.simplify = simplify
        self.crs_def = crs_def  # authid or WKT, goes in through ogr2ogr -a_srs
        self.skip_keywords = skip_keywords
        self.ogr2ogr_path = ogr2ogr_path
        self.oda_path = oda_path
        self.finished_cb = finished_cb
        self.error_msg = None
        self.stats = {}
        self._dwg_tmp_dir = None
        self._tmp_out = None

    def _log(self, msg, level=Qgis.MessageLevel.Info):
        QgsMessageLog.logMessage(msg, PLUGIN_NAME, level)

    def run(self):
        try:
            return self._process()
        except Exception as e:
            import traceback
            # traceback goes to the log, the dialog gets the one-line reason and a pointer at the log
            self._log(f"{e}\n{traceback.format_exc()}", Qgis.MessageLevel.Critical)
            self.error_msg = str(e) or e.__class__.__name__
            return False
        finally:
            # clean up the DWG temp dir even on an early exit
            if self._dwg_tmp_dir and os.path.isdir(self._dwg_tmp_dir):
                shutil.rmtree(self._dwg_tmp_dir, ignore_errors=True)
            # a half-written conversion never gets left next to the output
            if self._tmp_out:
                _silent_remove(self._tmp_out)

    def _process(self):
        actual_path = self.dxf_path

        # phase 1, DWG -> DXF
        if actual_path.lower().endswith(".dwg"):
            self._log("Converting DWG -> DXF...")
            self.setProgress(1)
            dxf_path, reason = convert_dwg_to_dxf(
                actual_path, self.oda_path)
            if not dxf_path:
                self.error_msg = reason or _tr(
                    "Could not convert DWG to DXF.")
                return False
            actual_path = dxf_path
            self._dwg_tmp_dir = os.path.dirname(dxf_path)

        # canceled mid-convert. error_msg stays None so the dialog reports a quiet cancel rather than an error
        if self.isCanceled():
            return False

        if not self.ogr2ogr_path:
            self.error_msg = _tr(
                "ogr2ogr was not found. It ships with QGIS - add the QGIS "
                "bin folder to PATH.")
            return False

        self._log(f"ogr2ogr: {self.ogr2ogr_path}")

        # phase 2, ogr2ogr DXF -> GPKG
        self.setProgress(5)

        # convert into a sibling part file and only move it over the target once ogr2ogr succeeded, so a failed import doesn't take the previous output with it
        base, ext = os.path.splitext(self.output_path)
        tmp_out = f"{base}.vernier-part{ext or '.gpkg'}"
        _silent_remove(tmp_out)
        self._tmp_out = tmp_out

        cmd = [
            self.ogr2ogr_path,
            "-f", "GPKG",
            tmp_out,
            actual_path,
            "-a_srs", self.crs_def,
            # with blocks inlined the driver only exposes one mixed layer, so the geometry class has to travel as an attribute
            "-sql", ENTITIES_SQL,
            "--config", "DXF_INLINE_BLOCKS", "TRUE",
            "--config", "DXF_FEATURE_LIMIT_PER_BLOCK", "-1",
        ]
        if self.simplify > 0:
            cmd.extend(["-simplify", str(self.simplify)])

        self._log(f"Running: {' '.join(cmd[:6])}...")
        try:
            result = _run_hidden(cmd, timeout=OGR2OGR_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.error_msg = _tr(
                "The conversion timed out after {0} seconds. Simplify or "
                "split the drawing in CAD, then retry.").format(
                    OGR2OGR_TIMEOUT)
            return False

        if result.returncode != 0:
            self._log(
                f"ogr2ogr exit code {result.returncode}:\n"
                f"{(result.stderr or '').strip()}", Qgis.MessageLevel.Critical)
            self.error_msg = _tr(
                "ogr2ogr could not convert the drawing (exit code {0}). "
                "Check Log Messages > Vernier for its output.").format(
                    result.returncode)
            return False

        if not os.path.isfile(tmp_out):
            self.error_msg = _tr("ogr2ogr did not create the output file.")
            return False

        # canceling here still leaves the previous output alone
        if self.isCanceled():
            return False

        try:
            os.replace(tmp_out, self.output_path)
        except OSError as e:
            self._log(f"Could not replace {self.output_path}: {e}",
                      Qgis.MessageLevel.Critical)
            self.error_msg = _tr(
                "Could not write {0} - the file is in use. Remove its "
                "layers from the project, then retry.").format(
                    os.path.basename(self.output_path))
            return False
        self._tmp_out = None

        self._log("ogr2ogr OK")
        self.setProgress(50)

        # phase 3, layer styles via ezdxf
        self._log("Reading DXF layer styles...")
        layer_styles = {}
        try:
            # deferred, ezdxf may have landed in libs/ mid-session
            import ezdxf
        except ImportError:
            ezdxf = None
            self._log(
                "ezdxf not installed - skipping style extraction. "
                "Layers will use default colors.", Qgis.MessageLevel.Warning)
        if ezdxf is not None:
            try:
                doc = ezdxf.readfile(actual_path)
                layer_styles = read_layer_styles(doc)
                del doc
            except Exception as e:
                self._log(f"ezdxf style read failed: {e}", Qgis.MessageLevel.Warning)

        # ogr2ogr and ezdxf are both done with the DWG temp dir now
        if self._dwg_tmp_dir and os.path.isdir(self._dwg_tmp_dir):
            shutil.rmtree(self._dwg_tmp_dir, ignore_errors=True)
            self._dwg_tmp_dir = None

        self.setProgress(55)

        # phase 4, SQLite index plus the unique CAD layers
        self._log("Indexing the Layer field...")
        skipped_layers = set()
        # {cad_layer: [(table, geometry class, count, text count), ...]}
        unique_layers = {}
        table_fields = {}    # {table: name of its Layer column}

        # sqlite3 connections don't close themselves on context exit, and a live handle keeps the GPKG locked on Windows
        with contextlib.closing(sqlite3.connect(self.output_path)) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT table_name FROM gpkg_contents "
                "WHERE data_type='features'")]
            self._log(f"ogr2ogr tables: {tables}")

            for tbl in tables:
                cols = [r[1] for r in conn.execute(
                    f"PRAGMA table_info([{tbl}])")]
                layer_field = None
                for candidate in ("Layer", "layer", "LAYER"):
                    if candidate in cols:
                        layer_field = candidate
                        break
                if not layer_field:
                    continue
                table_fields[tbl] = layer_field

                # index on Layer so the subset strings stay fast
                try:
                    safe_tbl = tbl.replace('"', '""')
                    conn.execute(
                        f'CREATE INDEX IF NOT EXISTS "idx_{safe_tbl}_layer" '
                        f'ON [{tbl}] ([{layer_field}])')
                    conn.commit()
                except Exception as e:
                    self._log(
                        f"Index creation failed on {tbl}: {e}", Qgis.MessageLevel.Warning)

                # count per CAD layer and geometry class plus how many of those rows carry text. a table written without the two columns collapses to one line layer
                geom_sel = f"[{GEOM_FIELD}]" if GEOM_FIELD in cols else "''"
                if "Text" in cols:
                    text_sel = ("SUM(CASE WHEN [Text] IS NOT NULL AND "
                                "[Text] <> '' THEN 1 ELSE 0 END)")
                else:
                    text_sel = "0"

                # nosec B608 - none of this is user input: layer_field is one of the three literals above, the other fragments are our own column names, and tbl comes out of gpkg_contents in the GeoPackage this task just wrote itself
                rows = conn.execute(
                    f'SELECT [{layer_field}], {geom_sel}, '  # nosec B608
                    f'COUNT(*), {text_sel} FROM [{tbl}] '
                    f'GROUP BY [{layer_field}], {geom_sel}'
                ).fetchall()

                for lname, gname, cnt, n_text in rows:
                    if not lname:
                        continue
                    if self.skip_keywords and is_skipped_layer(
                            lname, self.skip_keywords):
                        skipped_layers.add(lname)
                        continue
                    unique_layers.setdefault(lname, []).append(
                        (tbl, gname or "", cnt, n_text or 0))

        if skipped_layers:
            self._log(f"Skipped layers: {len(skipped_layers)}")
        self._log(f"CAD layers: {len(unique_layers)}")
        self.setProgress(60)

        # phase 5, one QML per CAD layer and geometry class
        qml_dir = os.path.splitext(self.output_path)[0] + "_styles"
        os.makedirs(qml_dir, exist_ok=True)

        # {display: {table, geometrytype, subset, qml_path, is_text, count}}
        layer_info = {}

        for cad_layer, groups in sorted(unique_layers.items()):
            for tbl, gname, cnt, n_text in groups:
                gtype, is_text, uri_type = geom_style(gname, cnt, n_text)

                display = cad_layer
                if len(groups) > 1:
                    # a CAD layer mixing geometry classes can't be one GIS layer, so it becomes one per class
                    display = f"{cad_layer} ({gname or tbl})"
                    if display in layer_info:
                        display = f"{display} [{tbl}]"

                style = layer_styles.get(cad_layer, {
                    "r": 0, "g": 0, "b": 0,
                    "lw_mm": 0.25, "dash": "", "aci": 7,
                })

                qml_content = make_qml(
                    gtype, style["r"], style["g"], style["b"],
                    style["lw_mm"], style["dash"], feature_count=cnt,
                )
                qml_name = _FS_ILLEGAL.sub("_", display)
                qml_path = os.path.join(qml_dir, f"{qml_name}.qml")
                with open(qml_path, "w", encoding="utf-8") as f:
                    f.write(qml_content)

                escaped = cad_layer.replace("'", "''")
                subset = f'"{table_fields[tbl]}" = \'{escaped}\''
                if gname:
                    geom = gname.replace("'", "''")
                    subset += f' AND "{GEOM_FIELD}" = \'{geom}\''

                layer_info[display] = {
                    "table": tbl,
                    # the mixed entities table settles on one geometry type unless the layer uri says otherwise
                    "geometrytype": uri_type,
                    "subset": subset,
                    "qml_path": qml_path,
                    "is_text": is_text,
                    "count": cnt,
                }

        self.stats["layers_written"] = len(layer_info)
        self.stats["n_ok"] = sum(v["count"] for v in layer_info.values())
        self.stats["qml_dir"] = qml_dir
        self.stats["layer_info"] = layer_info

        self._log(
            f"Done: {len(layer_info)} layers, "
            f"{self.stats['n_ok']:,} features")
        self.setProgress(100)
        return True

    def finished(self, result):
        """Runs back on the main thread once run() returns."""
        self.finished_cb(result, self.output_path, self.stats, self.error_msg)
