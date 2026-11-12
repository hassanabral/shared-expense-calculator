#!/usr/bin/env python3
"""Shared expense settlement calculator for PocketGuard CSV exports.

Usage:
    python3 settle.py partner_a.csv partner_b.csv --names Alex Sam \
        --start 2026-08-01 --end 2026-11-30

Transactions whose Notes contain #shared are split 50/50. A #shared transaction
with another tag (e.g. #hawaii2026) is grouped under that tag instead of its
Category. #edited and month tags (e.g. #aug2026) are ignored for grouping.
"""
import argparse
import csv
import json
import re
import webbrowser
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

TAG_RE = re.compile(r"#([\w-]+)")
MONTH_TAG_RE = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\d{2,4}$")
NON_GROUP_TAGS = {"shared", "edited"}
TEMPLATE = Path(__file__).with_name("report_template.html")
CENT = Decimal("0.01")


def tags(notes):
    return [t.lower() for t in TAG_RE.findall(notes)]


def group_tags(notes):
    """Tags that put a #shared transaction in its own group instead of its category."""
    out = []
    for t in tags(notes):
        if t not in NON_GROUP_TAGS and not MONTH_TAG_RE.match(t) and "#" + t not in out:
            out.append("#" + t)
    return out


def load_shared(path, start=None, end=None):
    """Return (columns, #shared transactions dated within [start, end])."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        txns = []
        for row in reader:
            row = {k: (v or "").strip() for k, v in row.items()}
            if "shared" not in tags(row["Notes"]):
                continue
            d = datetime.strptime(row["Date"], "%m/%d/%Y").date()
            if (start and d < start) or (end and d > end):
                continue
            txns.append({
                "row": row,
                "date": d,
                "spent": -Decimal(row["Amount"]),  # expenses are negative in the export
                "groups": group_tags(row["Notes"]) or [row["Category"]],
            })
    return columns, txns


def settle(a_txns, b_txns):
    """Per-group rows and a total. net > 0 means B owes A; net < 0 means A owes B.

    A transaction in several tag groups counts in each group, but once in the total.
    """
    groups = {}
    for who, txns in (("a", a_txns), ("b", b_txns)):
        for t in txns:
            for g in t["groups"]:
                kind = "Tag" if g.startswith("#") else "Category"
                e = groups.setdefault(g, {"group": g, "type": kind, "a": Decimal(0), "b": Decimal(0)})
                e[who] += t["spent"]
    rows = sorted(groups.values(), key=lambda e: (e["type"] != "Category", e["group"].lower()))
    total = {
        "group": "TOTAL",
        "type": "",
        "a": sum((t["spent"] for t in a_txns), Decimal(0)),
        "b": sum((t["spent"] for t in b_txns), Decimal(0)),
    }
    for e in rows + [total]:
        e["net"] = ((e["a"] - e["b"]) / 2).quantize(CENT, ROUND_HALF_UP)
    return rows, total


def owes(net, name_a, name_b):
    """(debtor, creditor, amount) for a net value."""
    if net > 0:
        return name_b, name_a, net
    if net < 0:
        return name_a, name_b, -net
    return "", "", Decimal(0)


def write_csv(path, rows, total, name_a, name_b):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Group", "Type", f"{name_a} paid", f"{name_b} paid", "Total shared",
                    "Owed by", "Owed to", "Amount owed"])
        for e in rows + [total]:
            debtor, creditor, amount = owes(e["net"], name_a, name_b)
            w.writerow([e["group"], e["type"], f"{e['a']:.2f}", f"{e['b']:.2f}",
                        f"{e['a'] + e['b']:.2f}", debtor, creditor, f"{amount:.2f}"])


def write_html(path, columns, rows, total, a_txns, b_txns, name_a, name_b, start, end):
    def money(e):
        debtor, creditor, amount = owes(e["net"], name_a, name_b)
        return {"group": e["group"], "type": e["type"], "a": str(e["a"]), "b": str(e["b"]),
                "debtor": debtor, "creditor": creditor, "amount": str(amount)}

    def txn(t):
        return {"values": t["row"], "date": t["date"].isoformat(),
                "spent": str(t["spent"]), "groups": t["groups"]}

    data = {
        "names": [name_a, name_b],
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "generated": date.today().isoformat(),
        "columns": columns,
        "settlement": [money(e) for e in rows],
        "total": money(total),
        "transactions": [[txn(t) for t in a_txns], [txn(t) for t in b_txns]],
    }
    payload = json.dumps(data).replace("</", "<\\/")
    path.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/", payload), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description="Settle shared (#shared) expenses between two partners.")
    p.add_argument("csv_a", type=Path, help="Partner A's PocketGuard CSV export")
    p.add_argument("csv_b", type=Path, help="Partner B's PocketGuard CSV export")
    p.add_argument("--names", nargs=2, metavar=("A", "B"), default=["Partner A", "Partner B"])
    p.add_argument("--start", type=date.fromisoformat, help="First date to include (YYYY-MM-DD)")
    p.add_argument("--end", type=date.fromisoformat, help="Last date to include (YYYY-MM-DD)")
    p.add_argument("--out", type=Path, default=Path("output"), help="Output directory (default: output)")
    p.add_argument("--no-open", action="store_true", help="Don't open the report in a browser")
    args = p.parse_args()

    name_a, name_b = args.names
    columns, a_txns = load_shared(args.csv_a, args.start, args.end)
    _, b_txns = load_shared(args.csv_b, args.start, args.end)
    rows, total = settle(a_txns, b_txns)

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path, html_path = args.out / "settlement.csv", args.out / "report.html"
    write_csv(csv_path, rows, total, name_a, name_b)
    write_html(html_path, columns, rows, total, a_txns, b_txns, name_a, name_b, args.start, args.end)

    debtor, creditor, amount = owes(total["net"], name_a, name_b)
    print(f"{len(a_txns)} shared transactions for {name_a}, {len(b_txns)} for {name_b}")
    print(f"{debtor} owes {creditor} ${amount:.2f}" if amount else "All square - nobody owes anything.")
    print(f"Wrote {csv_path} and {html_path}")
    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
