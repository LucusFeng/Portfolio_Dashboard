# Dev Spec — Simplify Cash: Drop Reconciliation, Add Net Cash CAD

## Goal

Simplify cash handling. Make `CashReport` the single source of truth, remove cash
reconciliation entirely, and add a **Net Cash CAD** column that converts USD cash using
`fxRateToBase` from the Flex position data — which also eliminates the current
"missing FX / $0.00 total" problem without needing the gateway FX path.

## Decisions (locked)

1. **Drop cash reconciliation entirely** — remove both the `balance` and `contributions`
   checks, the `cash_reconciliations` table, the repository functions, and the "Cash
   Reconciliation" UI section. (The old balance check compared IBKR's authoritative
   `endingCash` against a broken derived deposit-sum; it only ever produced false alarms.)
2. **Cash section stays**, sourced purely from `CashReport` `endingCash` per account+currency.
3. **New column: Net Cash CAD** = `CAD balance + USD balance × fxRateToBase`.
   - `fxRateToBase` comes from **OpenPosition** rows (not CashReport, which has no usable FX).
   - Use a **portfolio-wide** rate: the `fxRateToBase` from the **most recent** snapshot across
     all accounts (they're identical per statement date; "most recent" handles the case where
     accounts were uploaded on different days — don't let a stale earlier upload win).
   - USD balance is **signed** — negative margin debits reduce Net Cash CAD.
4. **Fallback:** if NO account anywhere has a USD position (so no `fxRateToBase` exists) but
   USD cash is present, show Net Cash CAD as **"— / needs FX"** for that account and exclude it
   from the total (flagged).
5. **Grand total includes signed net cash:** `Total CAD incl. cash = positions_total_cad +
   Σ net_cash_cad`. Negative net cash (margin) reduces the headline — expected, not a
   regression.

## Verified test values (from `testing_v3__1_.xml` + `rrsp_v1_based_ccy.xml`, fx=1.4017)

| Account | CAD cash | USD cash | Net Cash CAD |
|---|---|---|---|
| U24872141 | 12122.97 | −9657.98 | **−1414.61** |
| U24081754 | 150.22 | 198.86 | **+428.96** |
| **Total net cash** | | | **−985.65** |

Grand total = positions 67185.21 + net cash (−985.65) = **66199.56**.

---

## Changes by layer

### 1. Repository — `app/repository/db.py`
- Remove the `cash_reconciliations` table from `SCHEMA` and from the `TABLES` drop list.
- Bump `SCHEMA_VERSION` (next integer). Dev DB resets on bump (expected).
- Keep `cash_balances` (still the CashReport store).

### 2. Repository — `app/repository/cash.py`
- **Remove** `record_cash_reconciliation` and `latest_cash_reconciliation_warnings`.
- Keep `upsert_cash_balances` and `latest_cash_balances`.
- Add a helper to fetch the portfolio-wide FX: `latest_fx_rate_to_base(conn)` →
  the `fx_rate_to_base` from `position_values` with the max `snapshot_date` (any account;
  they agree per date). Return `None` if no position values exist.

### 3. Ingestion / Route — `app/routes/dashboard.py`
- In the flex upload/refresh path, **stop calling** the cash reconciliation writer.
- Keep parsing `CashReport` → `upsert_cash_balances`.
- Update the run-status message to drop the "cash checks" count.

### 4. Service — `app/services/cash.py`
- `get_cash(conn)`:
  - Per account: `cad_cash` (CAD endingCash), `usd_cash` (USD endingCash, signed),
    `net_cash_cad = cad_cash + usd_cash × fx` where `fx = latest_fx_rate_to_base(conn)`.
  - If `fx is None` **and** `usd_cash` is nonzero → `net_cash_cad = None`, status
    `"needs FX"`. If `usd_cash` is zero, `net_cash_cad = cad_cash` (no FX needed).
  - `cash_total_cad` = sum of the non-None `net_cash_cad` across accounts (signed). If any
    account is `needs FX`, either exclude it and flag, or surface a partial-total note.
  - **Remove** the `warnings` field (no more reconciliation).
- Frozen dataclass gains `net_cash_cad` and a per-account `status` ("ok" | "needs FX").

### 5. Service — `app/services/valuation.py`
- `total_cad = positions_total_cad + cash.cash_total_cad` (signed).
- Drop any cash-reconciliation fields from `DashboardData`.

### 6. UI — `templates/dashboard.html`
- **Cash section:** columns Account, CAD Cash, USD Cash (native, signed),
  **Net Cash CAD** (new), Status. Footer: total net cash CAD. Render negatives plainly;
  "— / needs FX" when applicable.
- **Remove** the entire "Cash Reconciliation" section.
- Headline "Total CAD incl. cash" now reflects `total_cad` (will differ from Positions CAD
  by net cash — e.g. lower when margin debit dominates).
- Remove the old "USD TO CAD" column that showed "—" (superseded by Net Cash CAD), unless you
  want to keep a per-currency converted display; the single Net Cash CAD column is the intent.

---

## Tests — `tests/test_repository_and_valuation.py`
- `get_cash`: two accounts as above → net cash −1414.61 and +428.96; total −985.65.
- Signed handling: negative USD cash reduces net (regression guard).
- Zero-USD-cash account: `net_cash_cad == cad_cash`, no FX needed.
- No-position-values case (fx None) with USD cash: `net_cash_cad is None`, status "needs FX",
  excluded from total.
- `total_cad == positions_total_cad + cash_total_cad` = 66199.56 for the two-file fixture.
- Remove/replace any tests referencing `cash_reconciliations` or the reconciliation warnings.

## Verification
- `python -m pytest -q` — green.
- Manual: load both flex files. Cash section shows Net Cash CAD (login1 −1414.61,
  login2 +428.96), total net −985.65; "Total CAD incl. cash" = 66199.56; no Cash
  Reconciliation section; USDCAD "missing" no longer blocks cash (FX now from fxRateToBase).

## Notes
- This removes the dependency on the gateway `fx_rates` table for cash conversion — cash now
  self-converts from Flex position FX. The separate USDCAD stat/gateway path can remain for
  any CIBC pricing needs but no longer gates the cash display or the total.
- `fxRateToBase` is a per-statement snapshot (same across accounts on a given date), so the
  portfolio-wide "most recent" rule is well-defined and avoids stale-rate bugs across
  multi-day uploads.
