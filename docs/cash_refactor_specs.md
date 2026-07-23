# Dev Spec — Cash Refactor: Dated Cash Transactions + CashReport Reconciliation

## Goal

Two changes, one coherent piece of work:

1. **Stop treating cash as a synthetic position.** Cash currently rides the stock-position
   machinery (synthetic `CASH:USD` / `CASH:CAD` instruments inserted into `positions`).
   Replace it with a dedicated cash path and its own UI section.
2. **Ingest the newly-available dated `CashTransactions`** so contributions become a real
   dated series again (restoring the value-vs-contributions growth chart), with the
   `CashReport` summary retained as a reconciliation cross-check.

> **Supersedes** the earlier "CashReport summary only" spec. The new Flex export
> (`testing_v3.xml`) includes a `CashTransactions` section, so the dated series is no longer
> deferred.

---

## What the new Flex export contains

`testing_v3.xml` (account `U24872141`, `period=YearToDate`, fromDate 20260101 / toDate
20260720) now has **both**:

- `CashTransactions` → **27 `CashTransaction` rows**, all `levelOfDetail="DETAIL"` (no summary
  duplicates to filter).
- `CashReport` → 3 `CashReportCurrency` rows (BASE_SUMMARY / CAD / USD) — the summary totals.

**Reconciliation confirmed:** dated `Deposits/Withdrawals` CAD rows sum to **28,500.00**,
exactly matching CashReport `deposits = 28500` and `depositWithdrawals = 28500`. The backfill
is trustworthy.

### CashTransaction attributes available

`accountId`, `acctAlias`, `amount`, `availableForTradingDate`, `clientReference`, `currency`,
`dateTime`, `deliveryType`, `description`, `dividendType`, `fxRateToBase`, `issuer`,
`levelOfDetail`, `reportDate`, `settleDate`, `transactionID`, `type`.

### Type vocabulary observed

| IBKR `type` | Rows | Canonical mapping |
|---|---|---|
| `Deposits/Withdrawals` | 15 | `DEPOSIT` / `WITHDRAWAL` **by sign** |
| `Dividends` | 3 | `DIVIDEND` |
| `Payment In Lieu Of Dividends` | 1 | `DIVIDEND` |
| `Withholding Tax` | 4 | `FEE` |
| `Broker Interest Paid` | 4 | `FEE` (or new `INTEREST`) — **currently mis-typed** |

---

## CRITICAL parser findings (fix before trusting any number)

### 1. Offsetting pairs — sum signed amounts, NEVER filter by sign

The data contains paired reversal rows, all typed `Deposits/Withdrawals`:

```
20260501  +763.69   (txnID 3246572824)   /  20260501  -763.69   (txnID 3258528399)
20260519  +1358.13  /  20260519  -1358.13
20260529  +1977.92  /  20260529  -1977.92
20260602  +374.80   /  20260602  -374.80
```

- Signed net (correct): **28,500.00** ✅ matches CashReport
- Positive-only sum (wrong): **32,974.54** ❌ — a ~16% overstatement

**Rule:** contributions = sum of *signed* `amount`. Do not drop negatives, do not filter to
"deposits only." A `DEPOSIT`/`WITHDRAWAL` split by sign is fine for `txn_type` labelling, but
the contributions series must net them.

### 2. `_date()` cannot parse the timestamped form — must fix

Dates appear in two shapes:
- `20260323` (date only) — handled today
- `20260611;202000` (date`;`HHMMSS) — **NOT handled**; current `_date()` falls through to
  `raw[:10]` and returns `"20260611;2"` (corrupt)

**Fix:** split on `;` and take the first token before the existing `YYYYMMDD` branch.

### 3. `Broker Interest Paid` falls through `_cash_type`

Current `_cash_type` matches `DIV`, `FEE`/`WITHHOLD`/`TAX`, `WITHDRAW`, `DEPOSIT`/`EFT`/
`TRANSFER`. `"Broker Interest Paid"` matches none → hits the `amount >= 0` default → gets
mis-typed as `WITHDRAWAL` (it is negative). Add an `INTEREST` keyword mapping to `FEE`
(or introduce an `INTEREST` txn_type; if so, add it to the schema comment and any type
filters).

Note `"Withholding Tax"` is correctly caught by the `TAX` keyword, and `"Payment In Lieu Of
Dividends"` by `DIV`. Verify both still map correctly after edits.

### 4. `acctAlias` is empty — fall back to `accountId`

All rows have `acctAlias=""`. Account labelling must fall back to `accountId`
(`U24872141`). The existing `_account()` helper already does this — confirm it isn't
bypassed.

### 5. Dedup key is sound

All 27 rows have a unique `transactionID`. The existing `(source, external_id)` unique index
works as-is. No change needed.

### 6. Query period is YearToDate — NOT full inception history

Statement is `fromDate=20260101`, earliest cash txn `20260323`. For a true contributions
backfill, the Flex query's date range must be widened to account inception. The CashReport
`deposits` total is the check: if summed dated deposits ≠ CashReport `deposits`, the window is
too short.

---

## Decisions (locked)

1. Cash leaves `positions` entirely. No synthetic `CASH:*` instruments anywhere.
2. **Cash balance** = CashReport `endingCash`, per account + currency (authoritative broker
   figure).
3. **Contributions** = dated series from `CashTransactions` `Deposits/Withdrawals`, **signed
   net**, cumulative over time. Contributions are CAD.
4. **Growth chart restored:** value-vs-contributions is back in scope, using the dated
   series. (Portfolio *value* history still only accumulates forward — see Notes.)
5. Track CAD and USD separately per account; display native + USD→CAD at latest FX; totals in
   CAD are **signed sums** (`endingCash` can be negative — USD margin debit of −9,657.98 in
   the sample).
6. Grand total shows both positions-only CAD and total CAD **including cash**.
7. **Reconciliation, two checks:**
   - Cash balance: derived/stored cash vs CashReport `endingCash`, per account+currency,
     native currency, warn when `abs(diff) > 1.0`.
   - Contributions: summed dated `Deposits/Withdrawals` vs CashReport `deposits` +
     `withdrawals`, per account+currency, warn when `abs(diff) > 1.0`. **This is the
     date-range-too-short detector.**
8. Storage: new `cash_balances` + `cash_reconciliations` tables. Schema version bump.

---

## Changes by layer

### 1. Ingestion — `app/ingestion/ibkr_flex.py`

- **Fix `_date()`**: split on `;` first (`raw.split(';')[0]`), then existing branches. Must
  turn `20260611;202000` → `2026-06-11`.
- **Fix `_cash_type()`**: add `INTEREST` → `FEE` (check before the sign-based default).
  Preserve existing `DIV` / `TAX` / `WITHHOLD` / `WITHDRAW` / `DEPOSIT` handling.
- **`parse_flex_transactions`**: already iterates `CashTransaction` nodes — verify it picks up
  all 27 rows with the fixed date/type helpers. Confirm `external_id` reads `transactionID`.
  Keep `txn_type` sign-split (`DEPOSIT` if `amount >= 0` else `WITHDRAWAL`) for
  `Deposits/Withdrawals`.
- **Remove cash from `parse_flex_positions`**: delete the `CashReport` → synthetic `CASH`
  `ParsedPosition` block. It returns only real EQUITY/ETF positions.
- **Add `parse_flex_cash_report(xml_text)`**: one record per account+currency from
  `CashReportCurrency`, **keeping only `levelOfDetail == "Currency"`** (skip
  `BASE_SUMMARY` / `BaseCurrency` to avoid double counting). Fields: `account_external_id`,
  `account_label` (acctAlias → fallback accountId), `currency`, `ending_cash`, `deposits`,
  `withdrawals`, `dividends`, `from_date`, `to_date`.
- Add `ParsedCashReport` dataclass to `app/models.py`.

### 2. Repository — `app/repository/positions.py`

- **Delete the cash-synthesis second pass** in `rebuild_positions` (the `cash_rows` query
  summing `transactions.amount` by account/currency and upserting `CASH:%s` instruments).
  `rebuild_positions` produces only real positions afterward.

### 3. Repository — `app/repository/db.py`

- Bump `SCHEMA_VERSION` `2` → `3` (wipes local dev DB — expected on schema change).
- Add both tables to `SCHEMA` and to the `TABLES` drop list:

```sql
CREATE TABLE IF NOT EXISTS cash_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    currency TEXT NOT NULL,
    ending_cash REAL NOT NULL,
    deposits REAL NOT NULL DEFAULT 0,
    withdrawals REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_date, account_id, currency)
);

CREATE TABLE IF NOT EXISTS cash_reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    currency TEXT NOT NULL,
    check_type TEXT NOT NULL,          -- 'balance' | 'contributions'
    broker_value REAL NOT NULL,
    derived_value REAL NOT NULL,
    difference REAL NOT NULL,
    status TEXT NOT NULL,              -- 'ok' | 'mismatch'
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 4. Repository — new `app/repository/cash.py`

- `upsert_cash_balances(conn, cash_reports, snapshot_date, source)` — `upsert_account`, then
  upsert a `cash_balances` row per account+currency+snapshot. Returns count.
- `record_cash_reconciliation(conn, cash_reports, snapshot_date, source)` — writes **two**
  check rows per account+currency:
  - `check_type='balance'`: broker `ending_cash` vs derived cash.
  - `check_type='contributions'`: broker (`deposits + withdrawals`) vs **signed sum** of
    `transactions.amount` where `txn_type IN ('DEPOSIT','WITHDRAWAL')` for that
    account+currency.
  - status `ok` if `abs(difference) <= 1.0` else `mismatch`.
- `latest_cash_balances(conn)` — latest snapshot per account+currency, joined to `accounts`.
- `latest_cash_reconciliation_warnings(conn)` — `status != 'ok'`, joined to accounts, include
  `check_type`; `ORDER BY created_at DESC LIMIT 20`.

### 5. Repository — `app/repository/portfolio.py`

- `contribution_cashflows` already groups `DEPOSIT`/`WITHDRAWAL` by date+currency with
  `SUM(amount)` — this is **already signed-correct**; keep it. Confirm ordering by `txn_date`
  for the cumulative series.

### 6. Service — new `app/services/cash.py`

- `get_cash(conn)` returns:
  - per-account rows: CAD cash (native), USD cash (native), USD→CAD equivalent, account cash
    total CAD (signed sum),
  - `cash_total_cad` (signed grand total),
  - `contributions_total_cad`,
  - `warnings` (from `latest_cash_reconciliation_warnings`, carrying `check_type`).
- Reuse `to_cad` (`app/services/portfolio.py`) and `latest_fx_rate`
  (`app/repository/observations.py`). Missing FX → `None` + flag, mirroring existing
  "missing FX" handling. Frozen dataclasses in the `HoldingRow`/`AccountSummary` style.

### 7. Service — `app/services/portfolio.py`

- Remove the dead cash special-case in `_holding`
  (`if row["asset_class"] == "CASH" and price is None: price = 1.0`). Account summaries become
  positions-only.

### 8. Service — `app/services/growth.py`

- **Keep `get_value_vs_contributions`** — the dated series is back. It already accumulates
  signed amounts via `contribution_cashflows`, which is correct for the offsetting pairs.
- Verify the cumulative total is a running signed sum (a `-763.69` reversal must step the line
  *down*), and that same-day `+X` / `-X` pairs net to zero on that date.

### 9. Service — `app/services/valuation.py`

- Extend `DashboardData` with `cash`, `positions_total_cad`, `total_cad`
  (= positions + cash), `contributions_total_cad`. Keep `growth_points`.
- Populate via `get_cash(conn)` in `build_dashboard_data`.

### 10. Route — `app/routes/dashboard.py`

- In `refresh_transactions`, per login: after `parse_flex_transactions` /
  `parse_flex_positions`, also call `parse_flex_cash_report`, then `upsert_cash_balances` and
  `record_cash_reconciliation`. Fold counts into the run status message.
- Extend `summarize_flex_xml` counts to report `CashTransaction` rows so the status line shows
  whether the section came back populated.

### 11. UI — `templates/dashboard.html`

- **Stats:** split into **Positions CAD** (`positions_total_cad`) and **Total CAD (incl.
  cash)** (`total_cad`); add **Contributions (CAD)**. Keep USDCAD. Holdings count now excludes
  cash (intended).
- **New "Cash" section:** per account — Account, CAD cash, USD cash (native), USD→CAD, account
  cash total (CAD); footer = grand total. Render negatives plainly; handle missing FX.
- **New "Cash Reconciliation" section** (only if warnings): Account, Currency, Check
  (balance/contributions), Broker value, Derived value, Difference. Note: a *contributions*
  mismatch usually means the Flex query date range doesn't reach account inception.
- **Keep the Contributions/growth table** (dated cumulative series).
- Cash no longer appears in Consolidated Holdings or Account Drilldown.

---

## Tests

### `tests/test_flex.py`
- `_date()`: `"20260611;202000"` → `"2026-06-11"`; `"20260323"` → `"2026-03-23"`;
  `"2026-06-11"` → unchanged.
- `_cash_type()`: `"Broker Interest Paid"` (negative) → `FEE`/`INTEREST`, **not**
  `WITHDRAWAL`. `"Withholding Tax"` → `FEE`. `"Payment In Lieu Of Dividends"` → `DIVIDEND`.
  `"Deposits/Withdrawals"` → `DEPOSIT` when positive, `WITHDRAWAL` when negative.
- `parse_flex_transactions` against a fixture containing the offsetting pairs: signed sum of
  `DEPOSIT`+`WITHDRAWAL` amounts = expected net (**not** the positive-only figure).
- `parse_flex_cash_report`: returns CAD + USD rows, **skips BASE_SUMMARY**; USD `ending_cash`
  negative.
- `parse_flex_positions`: no longer emits `CASH:*`.
- **Fixture:** add a `CashTransactions` fixture modelled on `testing_v3.xml` — include at
  least one offsetting `+X` / `-X` same-day pair, one `Broker Interest Paid`, one timestamped
  `dateTime` (`;HHMMSS`) and one date-only, and empty `acctAlias`.

### `tests/test_repository_and_valuation.py`
- **Rewrite** `test_cash_is_valued_at_one_and_converted_to_cad` → drive cash via
  `upsert_cash_balances` + `get_cash`; assert native + CAD-converted (e.g. USD −100 @ 1.25 →
  −125 CAD).
- **Fix** `test_transactions_dedup_lots_positions_and_dashboard_values`: cash is no longer in
  `data.holdings`; remove the `CASH:USD` lookup and `cash.market_value_cad == 1620` assertion;
  re-express via `data.cash`. Stock assertions (AAPL qty 6, mv 1200, mv_cad 1620, pnl 300) and
  the contributions assertion stay.
- **Add:**
  - Contributions series nets offsetting same-day pairs to zero on that date.
  - Cumulative contributions equals the signed net (regression guard for the 28,500 vs
    32,974.54 bug).
  - Cash balance upsert + latest read per account+currency; signed CAD total across CAD+USD.
  - Recon `balance` check: within tolerance → no warning; `>1.0` → one warning.
  - Recon `contributions` check: dated sum vs CashReport `deposits` — matching → ok;
    short-window (drop a deposit) → mismatch warning.

---

## Verification

- `python -m pytest -q` — all green.
- Manual: `rm -f data/portfolio.sqlite3`, start app, refresh transactions against a Flex query
  with **both** Cash Transactions and Cash Report enabled. Confirm:
  - Contributions cumulative total = **28,500** for the sample account (not 32,974.54),
  - Cash section shows CAD/USD `endingCash` including negatives,
  - Total CAD (incl. cash) = positions + cash,
  - Growth/contributions table shows dated steps with reversals stepping down,
  - Consolidated Holdings / Drilldown contain no `CASH:*`,
  - Cash Reconciliation appears only on >$1 mismatches.

---

## Notes / future

- **Widen the Flex date range.** Current query is `YearToDate`; earliest cash txn is
  20260323. For true contributions backfill, set the range to account inception. The
  `contributions` recon check will flag when it's too short.
- **Offsetting-pair stability unknown.** In this sample all pairs are same-day and net exactly.
  If a pair ever straddles a date boundary, the *dated* cumulative line will show a spurious
  spike-then-drop even though the total is right. Watch for this once multiple accounts are
  pulled; if it appears, consider netting by `(date, amount)` pair-matching before charting.
- **Value history still only fills forward.** Contributions backfill from Flex; portfolio
  *value* per day requires the EOD scheduler and accumulates from when it starts. The two
  lines of the growth chart will have unequal history initially.
- **Multi-account:** sample is one `FlexStatement`. Production spans multiple IBKR accounts
  across two logins plus CIBC — cash keys on (account, currency) across all statements.

## Out of scope

- FX inside recon comparisons (native currency only).
- Money-weighted / time-weighted returns.
- The EOD scheduler itself (separate work item).
