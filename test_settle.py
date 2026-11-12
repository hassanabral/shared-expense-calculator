import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from settle import group_tags, load_shared, settle

HEADER = ["Date", "Merchant", "Original description", "Amount", "Category", "Special marks", "Account", "Notes"]


def write_export(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.writer(f)
    w.writerow(HEADER)
    for d, amount, category, notes in rows:
        w.writerow([d, "Merchant", "DESC", amount, category, "", "CREDIT CARD", notes])
    f.close()
    return Path(f.name)


class GroupTagsTest(unittest.TestCase):
    def test_ignores_shared_edited_and_month_tags(self):
        self.assertEqual(group_tags("#edited #shared"), [])
        self.assertEqual(group_tags("#sep2026 smooth #shared"), [])
        self.assertEqual(group_tags("#alaska2026 #aug2026 #shared"), ["#alaska2026"])

    def test_trip_tags_with_digits_are_kept(self):
        self.assertEqual(group_tags("#vegas0326 #dc0326 #shared"), ["#vegas0326", "#dc0326"])


class LoadAndSettleTest(unittest.TestCase):
    def setUp(self):
        self.a = write_export([
            ("09/01/2026", "-100.00", "Groceries", "#shared"),
            ("09/02/2026", "-40.00", "Travel", "#hawaii2026 #shared"),
            ("09/03/2026", "-20.00", "Eating out", "#hawaii2026 #maui #shared"),  # two groups
            ("09/04/2026", "-999.00", "Groceries", ""),                           # not shared
            ("09/05/2026", "-5.00", "Groceries", "Shared with Sam"),              # no '#', not shared
            ("07/01/2026", "-50.00", "Groceries", "#shared"),                     # outside range
        ])
        self.b = write_export([
            ("09/10/2026", "-30.00", "Groceries", "#aug2026 #shared"),
            ("09/11/2026", "10.00", "Groceries", "#shared"),                      # refund
        ])

    def tearDown(self):
        self.a.unlink()
        self.b.unlink()

    def test_filters_and_groups(self):
        _, a = load_shared(self.a, date(2026, 8, 1), date(2026, 9, 30))
        self.assertEqual([t["groups"] for t in a], [["Groceries"], ["#hawaii2026"], ["#hawaii2026", "#maui"]])

    def test_settlement(self):
        _, a = load_shared(self.a, date(2026, 8, 1), date(2026, 9, 30))
        _, b = load_shared(self.b, date(2026, 8, 1), date(2026, 9, 30))
        rows, total = settle(a, b)
        by = {r["group"]: r for r in rows}
        # Groceries: A 100, B 30 - 10 refund = 20 -> B owes A 40
        self.assertEqual(by["Groceries"]["net"], Decimal("40.00"))
        self.assertEqual(by["#hawaii2026"]["a"], Decimal("60.00"))
        self.assertEqual(by["#maui"]["a"], Decimal("20.00"))
        # Total counts the multi-tag row once: A 160, B 20 -> B owes A 70
        self.assertEqual((total["a"], total["b"], total["net"]), (Decimal("160.00"), Decimal("20.00"), Decimal("70.00")))
        # Categories listed before tag groups
        self.assertEqual([r["group"] for r in rows], ["Groceries", "#hawaii2026", "#maui"])


if __name__ == "__main__":
    unittest.main()
