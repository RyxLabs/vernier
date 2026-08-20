# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""parse_area_rows and the table cell formatter, with the import doubling as a headless check on the whole panel. The number cases mirror what actually comes out of Excel - decimal commas, dot or space or NBSP thousands separators, and the block heuristic that reads a lone dot as thousands only when the block around it uses comma decimals."""

import os
import sys
import unittest

from qgis.core import QgsApplication  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.detach_panel import (  # noqa: E402
    _fmt_cell, parse_area_rows,
)

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


class TestTwoColumnBlocks(unittest.TestCase):

    def test_id_and_area_columns(self):
        rows = parse_area_rows("A\t100\nB\t200.5")
        self.assertEqual(rows, [("A", 100.0), ("B", 200.5)])

    def test_extra_columns_ignored(self):
        rows = parse_area_rows("Lot 1\t350\tsome note\textra")
        self.assertEqual(rows, [("Lot 1", 350.0)])

    def test_crlf_and_blank_lines(self):
        rows = parse_area_rows("A\t10\r\n\r\nB\t20\r\n   \r\n")
        self.assertEqual(rows, [("A", 10.0), ("B", 20.0)])

    def test_cells_are_stripped(self):
        rows = parse_area_rows("  A  \t  12.5  ")
        self.assertEqual(rows, [("A", 12.5)])


class TestSingleColumnBlocks(unittest.TestCase):

    def test_auto_ids_from_one(self):
        rows = parse_area_rows("100\n200\n300")
        self.assertEqual(rows, [("1", 100.0), ("2", 200.0), ("3", 300.0)])

    def test_auto_ids_honor_start_id(self):
        rows = parse_area_rows("100\n200", start_id=4)
        self.assertEqual(rows, [("4", 100.0), ("5", 200.0)])

    def test_mixed_one_and_two_column_rows(self):
        rows = parse_area_rows("A\t100\n250\nB\t300", start_id=1)
        # the bare row gets the running auto id (one per parsed row)
        self.assertEqual(rows, [("A", 100.0), ("2", 250.0), ("B", 300.0)])


class TestNumberFormats(unittest.TestCase):

    def test_decimal_comma(self):
        self.assertEqual(parse_area_rows("1000,50"), [("1", 1000.5)])

    def test_european_thousands_dot_decimal_comma(self):
        self.assertEqual(parse_area_rows("1.234,56"), [("1", 1234.56)])

    def test_english_thousands_comma_decimal_dot(self):
        self.assertEqual(parse_area_rows("1,234.56"), [("1", 1234.56)])

    def test_repeated_comma_is_thousands(self):
        self.assertEqual(parse_area_rows("1,234,567"), [("1", 1234567.0)])

    def test_repeated_dot_is_thousands(self):
        self.assertEqual(parse_area_rows("1.234.567"), [("1", 1234567.0)])

    def test_space_thousands_with_decimal_comma(self):
        self.assertEqual(parse_area_rows("1 234,5"), [("1", 1234.5)])

    def test_nbsp_thousands(self):
        # chr(160) = U+00A0, what Excel uses as the group separator
        self.assertEqual(parse_area_rows("1" + chr(160) + "234,5"),
                         [("1", 1234.5)])

    def test_plain_dot_decimal(self):
        self.assertEqual(parse_area_rows("42.75"), [("1", 42.75)])


class TestErrors(unittest.TestCase):

    def test_garbage_names_the_line(self):
        with self.assertRaises(ValueError) as ctx:
            parse_area_rows("A\t100\nB\tabc")
        self.assertIn("2", str(ctx.exception))
        self.assertIn("abc", str(ctx.exception))

    def test_zero_area_rejected(self):
        with self.assertRaises(ValueError):
            parse_area_rows("0")

    def test_negative_area_rejected(self):
        with self.assertRaises(ValueError):
            parse_area_rows("A\t-5")

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            parse_area_rows("nan")

    def test_below_display_precision_rejected(self):
        # anything under 0.0005 would render as "0" in the table
        with self.assertRaises(ValueError):
            parse_area_rows("0.0004")

    def test_smallest_displayable_area_accepted(self):
        rows = parse_area_rows("0.001")
        self.assertEqual(rows, [("1", 0.001)])

    def test_empty_text_gives_no_rows(self):
        self.assertEqual(parse_area_rows(""), [])
        self.assertEqual(parse_area_rows("\n\r\n  \n"), [])


class TestCellFormatter(unittest.TestCase):
    """The strict table reader float()s these back - must round-trip."""

    def test_integer_value_has_no_decimals(self):
        self.assertEqual(_fmt_cell(1000.0), "1000")

    def test_trailing_zeros_trimmed(self):
        self.assertEqual(_fmt_cell(0.5), "0.5")
        self.assertEqual(_fmt_cell(12.340), "12.34")

    def test_three_decimals_kept(self):
        self.assertEqual(_fmt_cell(1.234), "1.234")

    def test_round_trips_through_float(self):
        for value in (1.0, 0.001, 12345.678, 999999.999):
            self.assertAlmostEqual(float(_fmt_cell(value)), value, places=3)


class TestBlockHeuristics(unittest.TestCase):

    def test_header_row_is_skipped(self):
        self.assertEqual(parse_area_rows("ID\tArea\nA\t100"),
                         [("A", 100.0)])

    def test_header_only_line_still_raises(self):
        with self.assertRaises(ValueError):
            parse_area_rows("Area")

    def test_comma_decimal_block_makes_lone_dot_thousands(self):
        # "1.234" alone is ambiguous, but next to comma-decimal "56,7" it's a European thousands dot, so 1234 and not 1.234
        self.assertEqual(parse_area_rows("1.234\n56,7"),
                         [("1", 1234.0), ("2", 56.7)])

    def test_lone_dot_without_comma_context_stays_decimal(self):
        self.assertEqual(parse_area_rows("1.234"), [("1", 1.234)])


if __name__ == "__main__":
    unittest.main()
