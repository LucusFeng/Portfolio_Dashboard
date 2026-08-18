# API Fix and Limitations 0818

## Context

After several rounds of manual and API testing, the IBKR Flex parser, database ingestion, cash
logic, and dashboard valuation path appear to be working correctly. Manual XML upload works
consistently, and the API refresh has also succeeded when triggered at the right time.

The remaining issue is not a parser issue. It is an IBKR Flex Web Service timing/rate-limit
behavior issue.

## Fixes Implemented From `flex_refresh_hardening_spec.md`

### Per-Login Refresh

Added separate dashboard controls:

- `Refresh all`
- `Refresh login1`
- `Refresh login2`

Routes:

```text
POST /refresh/transactions
POST /refresh/transactions/login1
POST /refresh/transactions/login2
```

This allows each IBKR Flex login to be tested independently, instead of always calling both
logins in one request.

### Inter-Login Delay

`Refresh all` now spaces out calls between configured Flex logins.

Config:

```env
IBKR_FLEX_INTER_LOGIN_DELAY_SECONDS=15
```

The shorter alias is also supported:

```env
IBKR_FLEX_INTER_LOGIN_DELAY_SEC=15
```

This reduces the chance of sending rapid back-to-back `SendRequest` calls to IBKR.

### Refresh Cooldown Guard

Added a cooldown guard before calling IBKR. If a transaction refresh was attempted too recently,
the app records a skipped run instead of hitting the Flex API again.

Config:

```env
IBKR_FLEX_REFRESH_COOLDOWN_SECONDS=60
```

The shorter alias is also supported:

```env
IBKR_FLEX_REFRESH_COOLDOWN_SEC=60
```

The cooldown applies to:

- `Refresh all`
- `Refresh login1`
- `Refresh login2`

### Throttle-Aware Errors

The Flex client now recognizes IBKR throttle-style error codes:

- `1025`
- `10010`

These now produce a clearer message:

```text
IBKR Flex rate-limited (...). Wait several minutes before retrying; avoid repeated refreshes.
```

Other errors, such as `1001`, still surface with the original IBKR diagnostic text.

### Simple Flex Client Preserved

The API call flow was intentionally kept close to the prior working version:

- one `SendRequest`
- no retry wrapper around `SendRequest`
- poll `GetStatement` up to 10 times
- 3-second sleep between polls

Earlier retry/long-poll experiments appeared to make behavior worse, so those were rolled back.

## Known Limitations

### IBKR Flex Is Not Real-Time Friendly

Testing suggests the Flex Web Service should be treated as an **EOD batch source**, not a
real-time or mid-trading-day data API.

Observed behavior:

- Manual XML generation and upload work reliably.
- API refresh can work when triggered at the right time.
- Repeated API refreshes, especially soon after a successful run, can fail with:

```text
ErrorCode=1001
Statement could not be generated at this time. Please try again shortly.
```

This appears to be IBKR-side statement generation timing or cooldown behavior.

### Reset DB Does Not Reset IBKR State

The app's **Reset DB** button clears local SQLite data only. It does not reset IBKR's server-side
statement-generation state.

So this sequence can still fail:

1. Run API refresh successfully.
2. Reset local DB.
3. Immediately run API refresh again.

From IBKR's perspective, that is still a second statement-generation request shortly after the
first one.

### Recommended Operating Model

Use the API refresh as an end-of-day batch run:

1. Run once after market close or after IBKR has finalized activity data.
2. Avoid repeated refresh attempts.
3. If a refresh succeeds, do not immediately reset DB and call the API again.
4. If local dev data needs to be rebuilt immediately, use the manually downloaded XML files.

Practical development workflow:

1. Use **Refresh login1** and **Refresh login2** separately when diagnosing API behavior.
2. Use **Refresh all** for normal batch operation.
3. Use **Manual Flex XML Upload** when IBKR returns `1001`.
4. Treat repeated `1001` responses as a reason to wait, not as proof the parser is broken.

## Current Conclusion

The hardening work appears to be effective. The app now avoids the most obvious self-inflicted
rate-limit pattern and gives better diagnostics.

The remaining limitation is IBKR Flex Web Service timing. This portfolio tool should be designed
and operated as a local EOD portfolio dashboard, not as an intraday real-time portfolio sync.
