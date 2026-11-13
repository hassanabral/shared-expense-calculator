# Shared Expense Calculator

Settle shared expenses between two partners using their [PocketGuard](https://pocketguard.com) transaction CSV exports.

Tag a transaction in PocketGuard with `#shared` in its **Notes**. The script splits every shared expense 50/50 and tells you who owes whom, both per category and in total. It subtracts any payments you've already sent each other and gives you Venmo request/pay buttons for each category and trip tag. It also produces an interactive HTML report listing every shared transaction.

## Requirements

Python 3.9+. No third-party packages.

## Usage

1. Each partner exports their transactions from PocketGuard as a CSV.
2. *(Optional)* Copy `config.example.json` to `config.json` and fill in your names and Venmo usernames. See [Configuration](#configuration).
3. At the start of a month, settle the previous month:

```bash
python3 settle.py partner_a.csv partner_b.csv
```

Or choose the dates yourself:

```bash
python3 settle.py partner_a.csv partner_b.csv --start 2026-10-01 --end 2026-10-31
```

The script prints the result and opens the report in your browser:

```
15 shared transactions for Alex, 12 for Sam
Categories: Alex owes Sam $177.48
  Categories (Sam -> Alex): $17.41 - settled 2026-10-31
  Categories (Alex -> Sam): $194.89 - open
  #hawaii2026 (Alex -> Sam): $271.88 - open
Balance due: Alex owes Sam $194.89
Ignored 1 #settleup payment(s) that match no settlement within $1.00
Wrote output/settlement.csv and output/report.html
```

### Options

| Option | Description |
|---|---|
| `csv_a`, `csv_b` | The two partners' PocketGuard CSV exports (required) |
| `--start YYYY-MM-DD` | First date to include |
| `--end YYYY-MM-DD` | Last date to include, inclusive |
| `--config PATH` | Names and Venmo settings (default: `config.json`). If it's missing, the names are `Partner A`/`Partner B` and the report has no Venmo buttons |
| `--out DIR` | Output directory (default: `output`) |
| `--no-open` | Don't open the report in a browser |

If you give neither `--start` nor `--end`, the **previous calendar month** is settled. If you give only one, the range has no limit on the other side.

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

A transaction with several group tags shows up in each of those tag groups.

**Settlement.** For each group, the amount owed is `(A spend − B spend) / 2`. If A's spend is higher, B owes A that amount; if B's spend is higher, A owes B.

- The **total**, and the headline amount at the top of the report, cover **categories only**. Each tag group (e.g. a trip) is **settled separately**: it's listed below the total and has its own Venmo button.

- Refunds (positive amounts) on `#shared` transactions reduce that partner's spend.
- Pending transactions are included.
- Each group's amount is rounded to the cent, and the total is the sum of the rounded category amounts. That way it matches the category Venmo buttons exactly.

**Settlements.** What has to change hands each period is a set of **settlement items**:

- the **categories**, rolled up into one amount in each direction (e.g. Sam → Alex $17.41 and Alex → Sam $194.89)
- one amount for each **tag** (e.g. Alex → Sam $271.88 for `#hawaii2026`)

When you send one of these (Venmo, Zelle, …), tag the transfer `#settleup` in PocketGuard. The script marks the item as settled and shows the date it was sent. The **balance due** is the category amount that hasn't been settled yet.

- A `#settleup` transfer settles an item when it comes from the partner who owes that item and is **within $1** of its amount. Each transfer settles at most one item, and the closest match wins.
- **Any other `#settleup` transfer is ignored**, e.g. one that matches no amount, or one in the wrong direction. The report lists it as "Ignored".
- Only the **payer's** export counts: the outgoing transfer, a negative amount. The receiver's incoming copy is ignored, so a transfer is never counted twice.
- A transfer counts if its **date** is within `--start`/`--end`. When you pay October's settlement in November, **change the transaction's date in PocketGuard to October** so it counts toward the month it settles.
- `#settleup` transactions never count as shared expenses, even if they're also tagged `#shared`.

### Configuration

Create `config.json` (gitignored, so it stays private) from `config.example.json`:

```json
{
  "current_partner": "A",
  "partner_a_name": "Alex",
  "partner_b_name": "Sam",
  "partner_a_venmo": "alex-venmo-username",
  "partner_b_venmo": "sam-venmo-username"
}
```

`A` is the first CSV on the command line and `B` the second.

| Key | Meaning |
|---|---|
| `current_partner` | Whose point of view the report takes, `A` or `B` (required) |
| `partner_a_name`, `partner_b_name` | Display names (default: `Partner A`, `Partner B`) |
| `partner_a_venmo`, `partner_b_venmo` | Venmo usernames for the request/pay buttons |

### Venmo buttons

Every button sends to the **other** partner (not `current_partner`):

- **Categories** roll up into two buttons in the summary:
  - **Request** charges the other partner for every category where they owe you.
  - **Pay** pays them for every category where you owe them.
- **Tags** (e.g. `#hawaii2026`) each get their own Request or Pay button in the **Tag settlement** table.
- The **note** names the period from `--start`/`--end` on its first line, then each item on its own line:

  ```
  ===Oct 2026 shared expenses===

   - Groceries: $100.68
   - Home: $17.41
  ```

  A range within one month shows the month and year. A range that spans months shows the exact dates, e.g. `===Sep 15, 2026 to Oct 4, 2026 shared expenses===`.

Links use the [Venmo payment link](https://venmo.com/paymentlinks) format: `https://venmo.com/?txn=charge|pay&recipients=<username>&amount=<amount>&note=<note>`.

Notes:

- Once an item is settled by a matching `#settleup` transfer, its button is replaced by the date it was sent.
- A transaction with several tags is included in **each** tag's button. Don't press more than one of those buttons for the same transaction.
- After paying, tag the Venmo transfer `#settleup` and set its date in PocketGuard to the month it settles. The partner who sent the money tags it in their own export.

## Output

- **`settlement.csv`**: one row per category, then a categories-only `TOTAL` row, with each partner's spend, the combined total, who owes whom and the amount. `CATEGORY SETTLEMENT` rows show the category amount in each direction, and a `BALANCE DUE` row shows what's still unsettled. The tag rows come last, because they're settled separately. The `Settled on` column gives the date of the matching `#settleup` transfer.
- **`report.html`**: a self-contained page with:
  - the category balance still due, with a Venmo button or a "Sent" date for each direction
  - a **Category settlement** table (each category, the total, both category settlements and the balance due) and a separate **Tag settlement** table. Both have the same columns, and each tag shows its status (Venmo button or "Sent" date)
  - every `#settleup` transfer, and what it settled or why it was ignored
  - every shared transaction for each partner, grouped by category/tag, with all the original CSV columns, including Notes

  In the report you can switch between partners, search, flip the date order and collapse groups.

## Tests

```bash
python3 -m unittest test_settle
```

## Privacy

Your exports and the generated reports contain your financial data. `.gitignore` excludes `PocketGuard-transactions-*.csv` and `output/`. If you rename your exports, keep them out of the repo yourself.
