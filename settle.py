#!/usr/bin/env python3
"""Shared expense settlement calculator for PocketGuard CSV exports.

Usage:
    python3 settle.py partner_a.csv partner_b.csv \
        --start 2026-08-01 --end 2026-11-30

Without --start/--end, the previous calendar month is settled.

Transactions whose Notes contain #shared are split 50/50. A #shared transaction
with another tag (e.g. #hawaii2026) is grouped under that tag instead of its
Category. #edited and month tags (e.g. #aug2026) are ignored for grouping.

What has to change hands is a set of settlement items: the categories rolled up
per direction (one amount each way), plus one item per tag. A transfer tagged
#settleup in the payer's export (money out) settles the item it matches: same
payer, amount within $1. Any other #settleup transfer is ignored.

Partner names and Venmo request/pay buttons come from config.json (see config.example.json).
"""
import argparse
import csv
import json
import re
import webbrowser
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode

TAG_RE = re.compile(r"#([\w-]+)")
MONTH_TAG_RE = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\d{2,4}$")
NON_GROUP_TAGS = {"shared", "edited"}
TEMPLATE = Path(__file__).with_name("report_template.html")
CENT = Decimal("0.01")
MATCH_TOLERANCE = Decimal("1.00")


def tags(notes):
    return [t.lower() for t in TAG_RE.findall(notes)]


def group_tags(notes):
    """Tags that put a #shared transaction in its own group instead of its category."""
    out = []
    for t in tags(notes):
        if t not in NON_GROUP_TAGS and not MONTH_TAG_RE.match(t) and "#" + t not in out:
            out.append("#" + t)
    return out


def load_export(path, start=None, end=None):
    """Return (columns, #shared expenses, outgoing #settleup payments) dated within [start, end]."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        txns, payments = [], []
        for row in reader:
            row = {k: (v or "").strip() for k, v in row.items()}
            row_tags = tags(row["Notes"])
            if "shared" not in row_tags and "settleup" not in row_tags:
                continue
            d = datetime.strptime(row["Date"], "%m/%d/%Y").date()
            if (start and d < start) or (end and d > end):
                continue
            spent = -Decimal(row["Amount"])  # money out is negative in the export
            if "settleup" in row_tags:
                if spent > 0:  # only the payer's side counts; the receiver's copy is ignored
                    payments.append({"row": row, "date": d, "spent": spent, "groups": []})
                continue
            txns.append({
                "row": row,
                "date": d,
                "spent": spent,
                "groups": group_tags(row["Notes"]) or [row["Category"]],
            })
    return columns, txns, payments


def settle(a_txns, b_txns):
    """Per-group rows and a categories-only total. net > 0 means B owes A; net < 0 means A owes B.

    Tag groups are settled separately and are not part of the total. A transaction
    with several tags counts in each of its tag groups. The total's net is the sum
    of the rounded category nets, so it matches the category Venmo buttons exactly.
    """
    groups = {}
    for who, txns in (("a", a_txns), ("b", b_txns)):
        for t in txns:
            for g in t["groups"]:
                kind = "Tag" if g.startswith("#") else "Category"
                e = groups.setdefault(g, {"group": g, "type": kind, "a": Decimal(0), "b": Decimal(0)})
                e[who] += t["spent"]
    rows = sorted(groups.values(), key=lambda e: (e["type"] != "Category", e["group"].lower()))
    for e in rows:
        e["net"] = ((e["a"] - e["b"]) / 2).quantize(CENT, ROUND_HALF_UP)
    categories = [e for e in rows if e["type"] == "Category"]
    total = {"group": "TOTAL", "type": ""}
    for key in ("a", "b", "net"):
        total[key] = sum((e[key] for e in categories), Decimal(0))
    return rows, total


def settlement_items(rows):
    """What has to change hands: categories rolled up per direction, plus one item per tag.

    "debtor" is who sends the money: 0 = A pays B, 1 = B pays A.
    """
    items = []
    for debtor in (1, 0):
        parts = [(e["group"], abs(e["net"])) for e in rows
                 if e["type"] == "Category" and e["net"] and (e["net"] > 0) == (debtor == 1)]
        if parts:
            items.append({"group": "Categories", "type": "Category", "debtor": debtor, "parts": parts})
    for e in rows:
        if e["type"] == "Tag" and e["net"]:
            items.append({"group": e["group"], "type": "Tag", "debtor": int(e["net"] > 0),
                          "parts": [(e["group"], abs(e["net"]))]})
    for item in items:
        item["amount"] = sum(a for _, a in item["parts"])
        item["paid"] = None
    return items


def match_payments(items, a_payments, b_payments):
    """Settle items with #settleup payments from their debtor within $1 of the amount.

    Closest matches win, and each payment settles at most one item. Sets item["paid"]
    to the matching payment and payment["settles"] to the item (None = ignored).
    """
    payments = [(0, p) for p in a_payments] + [(1, p) for p in b_payments]
    for _, p in payments:
        p["settles"] = None
    candidates = sorted(
        (abs(p["spent"] - item["amount"]), i, j)
        for i, item in enumerate(items) for j, (payer, p) in enumerate(payments)
        if payer == item["debtor"] and abs(p["spent"] - item["amount"]) <= MATCH_TOLERANCE)
    for _, i, j in candidates:
        item, p = items[i], payments[j][1]
        if item["paid"] is None and p["settles"] is None:
            item["paid"], p["settles"] = p, item


def balance_due(items):
    """Category amount not yet settled. > 0 means B owes A; < 0 means A owes B."""
    return sum((item["amount"] if item["debtor"] == 1 else -item["amount"]
                for item in items if item["type"] == "Category" and not item["paid"]), Decimal(0))


def owes(net, name_a, name_b):
    """(debtor, creditor, amount) for a net value."""
    if net > 0:
        return name_b, name_a, net
    if net < 0:
        return name_a, name_b, -net
    return "", "", Decimal(0)


def item_label(item, names, arrow="->"):
    return f"{item['group']} ({names[item['debtor']]} {arrow} {names[1 - item['debtor']]})"


def load_config(path):
    """Read config.json, or return None (default names, no Venmo buttons) if it doesn't exist."""
    if not path.exists():
        print(f"No {path} found - using default names, no Venmo buttons (copy config.example.json to set it up)")
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("current_partner") not in ("A", "B"):
        raise SystemExit(f'{path}: "current_partner" must be "A" or "B"')
    return config


def period_label(start, end):
    """'Oct 2026' for a range within one month; 'Sep 15, 2026 to Oct 14, 2026' if it spans months."""
    start, end = start or end, end or date.today()
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%b %Y}"
    return f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}"


def add_venmo_links(items, config, label):
    """Give each item a Venmo link from the current partner's point of view (https://venmo.com/paymentlinks).

    Items the current partner owes become Pay links, items owed to them become
    Request (charge) links; both go to the other partner. Returns the current
    partner's index (0 = A, 1 = B), or None without a config.
    """
    if not config:
        return None
    me = 0 if config["current_partner"] == "A" else 1
    other = config[("partner_b_venmo", "partner_a_venmo")[me]].lstrip("@")
    for item in items:
        item["txn"] = "pay" if item["debtor"] == me else "charge"
        note = f"==={label} shared expenses===\n\n" + "\n".join(f" - {g}: ${a:.2f}" for g, a in item["parts"])
        item["url"] = "https://venmo.com/?" + urlencode(
            {"txn": item["txn"], "recipients": other, "amount": f"{item['amount']:.2f}", "note": note})
    return me


def write_csv(path, rows, total, items, name_a, name_b):
    names = [name_a, name_b]
    tag_items = {item["group"]: item for item in items if item["type"] == "Tag"}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Group", "Type", f"{name_a} spend", f"{name_b} spend", "Total shared",
                    "Owed by", "Owed to", "Amount owed", "Settled on"])

        def group_row(e, settled=""):
            debtor, creditor, amount = owes(e["net"], name_a, name_b)
            w.writerow([e["group"], e["type"], f"{e['a']:.2f}", f"{e['b']:.2f}",
                        f"{e['a'] + e['b']:.2f}", debtor, creditor, f"{amount:.2f}", settled])

        def settled_on(item):
            return item["paid"]["date"].isoformat() if item and item["paid"] else ""

        for e in [e for e in rows if e["type"] == "Category"] + [total]:
            group_row(e)
        for item in items:
            if item["type"] == "Category":
                w.writerow(["CATEGORY SETTLEMENT", "", "", "", "", names[item["debtor"]],
                            names[1 - item["debtor"]], f"{item['amount']:.2f}", settled_on(item)])
        debtor, creditor, amount = owes(balance_due(items), name_a, name_b)
        w.writerow(["BALANCE DUE", "", "", "", "", debtor, creditor, f"{amount:.2f}", ""])
        for e in rows:  # tags are settled separately, outside the total
            if e["type"] == "Tag":
                group_row(e, settled_on(tag_items.get(e["group"])))


def write_html(path, columns, rows, total, items, me, a_txns, b_txns, a_payments, b_payments,
               name_a, name_b, start, end):
    names = [name_a, name_b]

    def money(e, net):
        debtor, creditor, amount = owes(net, name_a, name_b)
        return {"group": e["group"], "type": e["type"], "a": str(e["a"]), "b": str(e["b"]),
                "debtor": debtor, "creditor": creditor, "amount": str(amount)}

    def txn(t):
        out = {"values": t["row"], "date": t["date"].isoformat(), "spent": str(t["spent"]), "groups": t["groups"]}
        if "settles" in t:
            out["settles"] = item_label(t["settles"], names, "→") if t["settles"] else None
        return out

    def item(i):
        out = {"group": i["group"], "type": i["type"], "debtor": i["debtor"], "amount": str(i["amount"]),
               "parts": [[g, str(a)] for g, a in i["parts"]], "txn": i.get("txn"), "url": i.get("url"),
               "paid": None}
        if i["paid"]:
            out["paid"] = {"date": i["paid"]["date"].isoformat(), "amount": str(i["paid"]["spent"])}
        return out

    data = {
        "names": names,
        "me": me,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "generated": date.today().isoformat(),
        "columns": columns,
        "settlement": [money(e, e["net"]) for e in rows],
        "total": money(total, total["net"]),
        "balance": money(total, balance_due(items)),
        "items": [item(i) for i in items],
        "transactions": [[txn(t) for t in a_txns], [txn(t) for t in b_txns]],
        "payments": [[txn(p) for p in a_payments], [txn(p) for p in b_payments]],
    }
    payload = json.dumps(data).replace("</", "<\\/")
    path.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/", payload), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description="Settle shared (#shared) expenses between two partners.")
    p.add_argument("csv_a", type=Path, help="Partner A's PocketGuard CSV export")
    p.add_argument("csv_b", type=Path, help="Partner B's PocketGuard CSV export")
    p.add_argument("--start", type=date.fromisoformat,
                   help="First date to include (YYYY-MM-DD; default: first day of last month)")
    p.add_argument("--end", type=date.fromisoformat,
                   help="Last date to include (YYYY-MM-DD; default: last day of last month)")
    p.add_argument("--config", type=Path, default=Path("config.json"),
                   help="Partner names, Venmo usernames and current partner (default: config.json)")
    p.add_argument("--out", type=Path, default=Path("output"), help="Output directory (default: output)")
    p.add_argument("--no-open", action="store_true", help="Don't open the report in a browser")
    args = p.parse_args()

    if not args.start and not args.end:  # default: settle last month
        args.end = date.today().replace(day=1) - timedelta(days=1)
        args.start = args.end.replace(day=1)

    config = load_config(args.config) or {}
    name_a = config.get("partner_a_name", "Partner A")
    name_b = config.get("partner_b_name", "Partner B")
    names = [name_a, name_b]
    columns, a_txns, a_payments = load_export(args.csv_a, args.start, args.end)
    _, b_txns, b_payments = load_export(args.csv_b, args.start, args.end)
    rows, total = settle(a_txns, b_txns)
    items = settlement_items(rows)
    match_payments(items, a_payments, b_payments)
    me = add_venmo_links(items, config, period_label(args.start, args.end))

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path, html_path = args.out / "settlement.csv", args.out / "report.html"
    write_csv(csv_path, rows, total, items, name_a, name_b)
    write_html(html_path, columns, rows, total, items, me, a_txns, b_txns, a_payments, b_payments,
               name_a, name_b, args.start, args.end)

    print(f"{len(a_txns)} shared transactions for {name_a}, {len(b_txns)} for {name_b}")
    debtor, creditor, amount = owes(total["net"], name_a, name_b)
    print(f"Categories: {debtor} owes {creditor} ${amount:.2f}" if amount else "Categories: even")
    for item in items:
        status = f"settled {item['paid']['date']}" if item["paid"] else "open"
        print(f"  {item_label(item, names)}: ${item['amount']:.2f} - {status}")
    debtor, creditor, amount = owes(balance_due(items), name_a, name_b)
    print(f"Balance due: {debtor} owes {creditor} ${amount:.2f}" if amount else "Balance due: none")
    ignored = sum(1 for p in a_payments + b_payments if p["settles"] is None)
    if ignored:
        print(f"Ignored {ignored} #settleup payment(s) that match no settlement within ${MATCH_TOLERANCE}")
    print(f"Wrote {csv_path} and {html_path}")
    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
