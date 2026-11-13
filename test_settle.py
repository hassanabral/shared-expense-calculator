import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from settle import (add_venmo_links, balance_due, group_tags, load_export, match_payments, period_label,
                    settle, settlement_items)

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
        _, a, _ = load_export(self.a, date(2026, 8, 1), date(2026, 9, 30))
        self.assertEqual([t["groups"] for t in a], [["Groceries"], ["#hawaii2026"], ["#hawaii2026", "#maui"]])

    def test_settlement(self):
        _, a, _ = load_export(self.a, date(2026, 8, 1), date(2026, 9, 30))
        _, b, _ = load_export(self.b, date(2026, 8, 1), date(2026, 9, 30))
        rows, total = settle(a, b)
        by = {r["group"]: r for r in rows}
        # Groceries: A 100, B 30 - 10 refund = 20 -> B owes A 40
        self.assertEqual(by["Groceries"]["net"], Decimal("40.00"))
        self.assertEqual(by["#hawaii2026"]["a"], Decimal("60.00"))
        self.assertEqual(by["#maui"]["a"], Decimal("20.00"))
        # Total covers categories only (tags settled separately): A 100, B 20 -> B owes A 40
        self.assertEqual((total["a"], total["b"], total["net"]), (Decimal("100.00"), Decimal("20.00"), Decimal("40.00")))
        # Categories listed before tag groups
        self.assertEqual([r["group"] for r in rows], ["Groceries", "#hawaii2026", "#maui"])

    def test_total_matches_sum_of_rounded_categories(self):
        t = lambda spent, group: {"spent": Decimal(spent), "groups": [group]}
        # Each category's half is 0.005 -> rounds to 0.01; total must be 0.02 (not 0.01) to match the buttons
        _, total = settle([t("0.01", "Groceries"), t("0.01", "Home"), t("50.00", "#trip")], [])
        self.assertEqual(total["net"], Decimal("0.02"))


class SettleUpTest(unittest.TestCase):
    def setUp(self):
        self.a = write_export([
            ("10/05/2026", "-200.00", "Groceries", "#shared"),                 # B owes A 100 (categories)
            ("10/20/2026", "100.00", "Transfer", "#settleup"),                 # incoming copy: ignored
            ("10/26/2026", "-150.80", "Transfer", "#settleup"),                # A pays #trip 150 (within $1)
            ("10/27/2026", "-42.00", "Transfer", "#settleup"),                 # $2 off categories 40: ignored
        ])
        self.b = write_export([
            ("10/07/2026", "-80.00", "Home", "#shared"),                       # A owes B 40 (categories)
            ("10/08/2026", "-300.00", "Travel", "#trip #shared"),              # A owes B 150 (#trip)
            ("10/25/2026", "-99.50", "Transfer", "#settleup"),                 # B pays categories 100
            ("10/29/2026", "-150.00", "Transfer", "#settleup"),                # B doesn't owe #trip: ignored
            ("11/02/2026", "-40.00", "Transfer", "#settleup"),                 # outside range
        ])

    def tearDown(self):
        self.a.unlink()
        self.b.unlink()

    def run_month(self):
        _, a, a_pay = load_export(self.a, date(2026, 10, 1), date(2026, 10, 31))
        _, b, b_pay = load_export(self.b, date(2026, 10, 1), date(2026, 10, 31))
        rows, _ = settle(a, b)
        items = settlement_items(rows)
        match_payments(items, a_pay, b_pay)
        return items, a_pay, b_pay

    def test_items(self):
        items, _, _ = self.run_month()
        self.assertEqual([(i["group"], i["debtor"], i["amount"]) for i in items],
                         [("Categories", 1, Decimal("100.00")), ("Categories", 0, Decimal("40.00")),
                          ("#trip", 0, Decimal("150.00"))])

    def test_matching(self):
        items, a_pay, b_pay = self.run_month()
        paid = {(i["group"], i["debtor"]): i["paid"] and i["paid"]["date"] for i in items}
        self.assertEqual(paid, {("Categories", 1): date(2026, 10, 25), ("Categories", 0): None,
                                ("#trip", 0): date(2026, 10, 26)})
        self.assertEqual([p["spent"] for p in a_pay], [Decimal("150.80"), Decimal("42.00")])  # incoming dropped
        self.assertEqual([p["spent"] for p in b_pay], [Decimal("99.50"), Decimal("150.00")])  # November dropped
        self.assertEqual([p["settles"] is None for p in a_pay + b_pay], [False, True, False, True])

    def test_balance_due_is_unsettled_categories(self):
        items, _, _ = self.run_month()
        self.assertEqual(balance_due(items), Decimal("-40.00"))  # A still owes B for categories

    def test_closest_payment_wins(self):
        items = [{"group": "#x", "type": "Tag", "debtor": 0, "amount": Decimal("150.00"), "paid": None}]
        far, near = {"spent": Decimal("150.80")}, {"spent": Decimal("150.10")}
        match_payments(items, [far, near], [])
        self.assertIs(items[0]["paid"], near)
        self.assertIsNone(far["settles"])


class VenmoTest(unittest.TestCase):
    ROWS = [
        {"group": "Groceries", "type": "Category", "net": Decimal("100.68")},     # B owes A
        {"group": "Home", "type": "Category", "net": Decimal("17.41")},           # B owes A
        {"group": "Eating out", "type": "Category", "net": Decimal("-27.15")},    # A owes B
        {"group": "#hawaii2026", "type": "Tag", "net": Decimal("-271.88")},       # A owes B
        {"group": "#maui", "type": "Tag", "net": Decimal("0.00")},                # even: nothing to settle
    ]
    CONFIG = {"partner_a_venmo": "@alex-v", "partner_b_venmo": "sam-v"}

    def items(self, me):
        items = settlement_items(self.ROWS)
        config = dict(self.CONFIG, current_partner=me) if me else None
        return add_venmo_links(items, config, "Oct 2026"), {(i["group"], i["debtor"]): i for i in items}

    def test_partner_a_view(self):
        me, by = self.items("A")
        self.assertEqual(me, 0)
        self.assertEqual(set(by), {("Categories", 1), ("Categories", 0), ("#hawaii2026", 0)})
        self.assertEqual(by["Categories", 1]["url"],
                         "https://venmo.com/?txn=charge&recipients=sam-v&amount=118.09"
                         "&note=%3D%3D%3DOct+2026+shared+expenses%3D%3D%3D%0A%0A+-+Groceries%3A+%24100.68"
                         "%0A+-+Home%3A+%2417.41")
        self.assertEqual((by["Categories", 0]["txn"], by["Categories", 0]["amount"]), ("pay", Decimal("27.15")))
        self.assertIn("txn=pay&recipients=sam-v&amount=271.88", by["#hawaii2026", 0]["url"])

    def test_partner_b_view_flips(self):
        me, by = self.items("B")
        self.assertEqual(me, 1)
        self.assertEqual(by["Categories", 1]["txn"], "pay")
        self.assertIn("recipients=alex-v", by["Categories", 1]["url"])  # leading @ stripped
        self.assertEqual(by["#hawaii2026", 0]["txn"], "charge")

    def test_no_config_no_links(self):
        me, by = self.items(None)
        self.assertIsNone(me)
        self.assertNotIn("url", by["Categories", 1])

    def test_period_label(self):
        self.assertEqual(period_label(date(2026, 10, 1), date(2026, 10, 31)), "Oct 2026")
        self.assertEqual(period_label(date(2026, 10, 5), date(2026, 10, 20)), "Oct 2026")
        self.assertEqual(period_label(date(2026, 9, 15), date(2026, 10, 4)), "Sep 15, 2026 to Oct 4, 2026")
        self.assertEqual(period_label(date(2026, 12, 1), date(2027, 1, 31)), "Dec 1, 2026 to Jan 31, 2027")


if __name__ == "__main__":
    unittest.main()
