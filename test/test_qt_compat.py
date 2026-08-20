# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The version probes in qt_compat, driven against stand-in classes rather than a real QgsGeometry. The 3.28 shape cannot be reached from a newer QGIS, and getting it wrong only shows up on the oldest supported build, so the shapes are modelled here instead."""

import os
import sys
import unittest

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from vernier.qt_compat import (  # noqa: E402
    VALIDATOR_GEOS, _validator_geos,
)


class _Modern:
    """QGIS 3.30+: the names live under a populated ValidationMethod."""

    class ValidationMethod:
        ValidatorGeos = 1
        ValidatorQgisInternal = 0


class _Legacy:
    """QGIS 3.28: ValidationMethod exists but is empty, the names sit on the class."""

    class ValidationMethod:
        pass

    ValidatorGeos = 1
    ValidatorQgisInternal = 0


class _NoNamespace:
    """No ValidationMethod attribute at all."""

    ValidatorGeos = 1


class TestValidatorGeosProbe(unittest.TestCase):

    def test_modern_shape_uses_the_scoped_member(self):
        self.assertEqual(_validator_geos(_Modern), 1)

    def test_legacy_shape_falls_past_the_empty_namespace(self):
        # the 3.28 trap: the namespace resolves, so checking it exists is not enough
        self.assertEqual(_validator_geos(_Legacy), 1)

    def test_missing_namespace_falls_back_to_the_class(self):
        self.assertEqual(_validator_geos(_NoNamespace), 1)

    def test_every_shape_agrees(self):
        values = {_validator_geos(c)
                  for c in (_Modern, _Legacy, _NoNamespace)}
        self.assertEqual(len(values), 1)

    def test_nothing_to_find_raises(self):
        class _Empty:
            pass

        with self.assertRaises(AttributeError):
            _validator_geos(_Empty)

    def test_resolves_against_the_real_qgis_build(self):
        self.assertIsNotNone(VALIDATOR_GEOS)


if __name__ == "__main__":
    unittest.main()
