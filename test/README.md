# Running the tests

The suite needs QGIS's bundled Python - plain system Python cannot
import `qgis.core`. Run each module as its own process: QGIS allows a
single `initQgis`/`exitQgis` cycle per process, so `unittest discover`
would crash.

On Windows, from the plugin folder:

    "C:/Program Files/QGIS 3.40/bin/python-qgis.bat" test/test_topology_service.py

(`python-qgis-ltr.bat` on an LTR install). On Linux the `qgis/qgis`
docker images work the same way with plain `python3` - see
`.github/workflows/tests.yml`, which loops over every `test_*.py` on
the 3.28 floor, the 3.34 LTR and the latest image.

A few tests need optional packages, pip-installed into the QGIS Python:

- `ezdxf` for the DXF round-trip assertions in `test_dxf_export_service`
  and the per-group exports in `test_split_export_service`
- `shapely` for `test_centerline_service`, which skips itself without it

The `sys.path` bootstrap at the top of the tests that import `vernier`
points at the repo's parent directory, mirroring how QGIS resolves
`import vernier` from a profile's plugins folder. The pure-AST
convention tests need no bootstrap at all.
