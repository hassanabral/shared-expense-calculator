# Shared Expense Calculator

Settle shared expenses between two partners using their [PocketGuard](https://pocketguard.com) transaction CSV exports.

Tag a transaction in PocketGuard with `#shared` in its **Notes**. The script splits every shared expense 50/50 and tells you who owes whom, both per category and in total. It also produces an interactive HTML report listing every shared transaction.

## Requirements

Python 3.9+. No third-party packages.

## Usage

1. Each partner exports their transactions from PocketGuard as a CSV.
2. Run:

```bash
python3 settle.py partner_a.csv partner_b.csv --names Alex Sam --start 2026-10-01 --end 2026-10-31
```

The script prints the result and opens the report in your browser:

```
15 shared transactions for Alex, 13 for Sam
Alex owes Sam $573.85
Wrote output/settlement.csv and output/report.html
```

### Options

| Option | Description |
|---|---|
| `csv_a`, `csv_b` | The two partners' PocketGuard CSV exports (required) |
| `--names A B` | Display names, in the same order as the files (default: `Partner A` `Partner B`) |
| `--start YYYY-MM-DD` | First date to include (default: earliest) |
| `--end YYYY-MM-DD` | Last date to include, inclusive (default: latest) |
| `--out DIR` | Output directory (default: `output`) |
| `--no-open` | Don't open the report in a browser |

Each run overwrites the files in `--out`. To keep a history, use a directory per period, e.g. `--out output/2026-10`.

### Try it with sample data

`fake_partner_b.csv` is a made-up export for October 2026. Pass it as either partner to see how the tool works:

```bash
python3 settle.py your_export.csv fake_partner_b.csv --start 2026-10-01 --end 2026-10-31
```

## How it works

**Shared transactions.** Only transactions whose Notes contain the `#shared` tag count. Plain text such as "Shared with Sam" is ignored.

**Grouping.** A shared transaction goes in its **Category** (e.g. Groceries) unless it has another tag, such as a trip tag:

| Notes | Group |
|---|---|
| `#shared` | its Category |
| `#hawaii2026 #shared` | `#hawaii2026` |
| `#sep2026 #shared` | its Category (month tags are ignored) |
| `#edited #shared` | its Category (`#edited` is added by PocketGuard) |
| `#hawaii2026 #maui #shared` | both `#hawaii2026` and `#maui` |

A transaction with several group tags shows up in each of those groups, but the **total counts it only once**. Because of this, the group rows can add up to more than the total.

**Settlement.** For each group and for the total, the amount owed is `(A paid − B paid) / 2`. If A paid more, B owes A that amount; if B paid more, A owes B.

- Refunds (positive amounts) on `#shared` transactions reduce that partner's spend.
- Pending transactions are included.
- Each group's amount is rounded to the cent on its own, so the group rows can differ from the total by a cent or two. Settle on the total.

## Output

- **`settlement.csv`**: one row per group plus a `TOTAL` row, with each partner's spend, the combined total, who owes whom and the amount.
- **`report.html`**: a self-contained page with:
  - who owes whom, and how much
  - the settlement broken down by group
  - every shared transaction for each partner, grouped by category/tag, with all the original CSV columns, including Notes

  In the report you can switch between partners, search, flip the date order and collapse groups.

## Tests

```bash
python3 -m unittest test_settle
```

## Privacy

Your exports and the generated reports contain your financial data. `.gitignore` excludes `PocketGuard-transactions-*.csv` and `output/`. If you rename your exports, keep them out of the repo yourself.
