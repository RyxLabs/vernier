# Vernier

[![tests](https://github.com/ryxlabs/vernier/actions/workflows/tests.yml/badge.svg)](https://github.com/ryxlabs/vernier/actions/workflows/tests.yml)

**Vector editing toolkit for QGIS.**

Vernier bundles a CAD-style canvas and command bar, topology validation with styled error layers, polygon splitting to exact target areas, centerline extraction, DXF/DWG and KMZ exchange, and versioned autosave backups into a single toolbar. The core tools run on a stock QGIS - no extra packages.

## Tools

| Tool | What it adds over core QGIS |
|---|---|
| CAD Mode | Follows the workspace conventions of AutoCAD, BricsCAD, ZWCAD, GstarCAD, DraftSight and other DWG/DXF software: typed command bar (`pl`, `split`, `topo`…) with autocomplete, dark canvas with a scalable grid, live status strip with F2/F4/F9 toggles and an F8 basemap toggle. Off by default. |
| Smart Snapping | One click applies a full snapping profile (all layers, vertex+segment, intersections, topological editing) instead of manually clicking five separate toolbar buttons |
| Autosave Backups | Timestamped side-copies of project + edited layers, retention, restore browser |
| Find Duplicate Geometries | Extracts every member of each duplicate group into a review layer before you delete anything |
| Remove Close Vertices | Topology-aware: keeps the vertex shared by neighboring features, so common boundaries survive |
| Centerline Extraction | Polygon medial axis with trunk extraction and straightening |
| Attribute / Spatial Join | Multi-source joins, match preview, configurable multi-value policies, provenance columns |
| KMZ Export | Multi-layer KMZ with label placemarks, per-layer colors, label prefix/suffix remembered per field and shared with the DXF export |
| Area Readout | Live ellipsoidal area of the selected polygons in the status bar, units configurable |
| Geoprocessing Shortcuts | One-click buffer / intersection / difference / dissolve / multipart-to-single |
| Topology Validator | Checks for invalid geometries, duplicates, overlaps, gaps and vertex errors, with clickable results and styled, attributed error layers |
| DXF/DWG Import | Drawings from any DWG/DXF package, including AutoCAD, BricsCAD, ZWCAD and GstarCAD. Any DWG version (via ODA), one styled layer per CAD layer, true ACI colors, lineweights and linetypes |
| DXF Export | Labels as TEXT entities with true colors, each label point placed inside its polygon and rotated to the part's main axis, per-layer style table, split-by-attribute into one DXF per value |
| CAD Lines to Polygons | Build polygon layers from closed CAD lines, one output layer per value of the drawing's `Layer` attribute |
| Quick Symbology | Reusable style templates with field-role auto-binding |
| Split to Target Areas | Split a polygon into pieces of exact areas along a drawn direction line. Excel paste, remainder tracking, millimeter-grid mode |

## Installation

QGIS > *Plugins > Manage and Install Plugins* > search **Vernier**.

From source (development):

```bash
cd <your QGIS profile>/python/plugins
git clone https://github.com/ryxlabs/vernier vernier
```

Then enable it in *Plugins > Manage and Install Plugins > Installed*.

## Requirements

- QGIS 3.28 or newer, including QGIS 4 (Qt5 and Qt6 both supported)
- **No external dependencies for the core tools** - they run on a stock QGIS.

Optional, per-tool:

| Tool | Needs | How to get it |
|---|---|---|
| DXF import / export | `ezdxf` (Python) | The plugin offers a one-click install into a private folder of your QGIS user profile after asking, so it survives plugin updates. It never touches the QGIS installation and never installs without consent. Manual alternative: `python -m pip install ezdxf` from the OSGeo4W Shell (Windows) or your system Python (Linux/macOS). |
| Centerline extraction | `shapely` (Python) | Ships with QGIS, there's nothing to do. |
| **DWG** import (not DXF) | [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) | Free, installed separately by you. The plugin only looks for it on disk, or lets you point at it; it is never downloaded automatically. |

## Folder Structure

```
vernier/
├── vernier.py         # plugin class; actions built from features.py
├── features.py        # declarative feature catalog (toolbar/menu/help)
├── command_bar.py     # CAD Mode: command bar, registry and status strip
├── cad_grid.py        # CAD Mode: scalable grid overlay for the canvas
├── detach_panel.py    # Split to Target Areas dock panel
├── topology_panel.py  # Topology Validator dock panel
├── i18n.py            # one translation context for the whole plugin
├── qt_compat.py       # field-type constants for Qt5 and Qt6 builds
├── dialogs/           # dialog windows (inherit BaseDialog)
├── services/          # business logic, one service per tool
├── tools/             # canvas tools and small per-tool helpers
├── icons/             # light-theme icons (see ATTRIBUTION.md)
│   └── dark/          # dark-theme icons (generated - do not edit)
├── test/              # per-module suites (see test/README.md)
└── scripts/           # build tooling: release zip, dark icons
```

## Development

Tests run with QGIS's bundled Python, one process per module - see
[test/README.md](test/README.md) for the exact commands on Windows and
Linux. CI runs the whole suite on QGIS 3.28, 3.34 LTR and latest on
every push.

`python scripts/build_zip.py` builds `dist/vernier.<version>.zip`, the
package uploaded to the QGIS plugin repository.

## Support

Bug reports and feature requests are welcome on the [issue tracker](https://github.com/ryxlabs/vernier/issues). For commercial support or custom development: hello@ryxlabs.dev.

## License

GNU General Public License v2 or later (`GPL-2.0-or-later`) - the same license as QGIS itself. See [LICENSE](LICENSE).

Some icons adapted from [Tabler Icons](https://tabler.io/icons) (MIT) -
see [icons/ATTRIBUTION.md](icons/ATTRIBUTION.md).

Copyright © 2026 RYXLAB SOFT TECH SRL ([RyxLabs](https://ryxlabs.dev))
