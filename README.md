# Portfolio Consolidation Dashboard

A local-first web app that pulls holdings from multiple brokerage accounts into a single
view, so I can see aggregate positions, market value, and PnL across everything I own —
in one place, normalized to CAD.

---

## The Problem

My investments are spread across four siloed brokerage accounts:

| Account | Broker | Login | Tax type |
|---|---|---|---|
| TFSA | CIBC Investor's Edge | — (manual) | TFSA |
| RRSP | IBKR | login1 | Registered |
| Margin | IBKR | login1 | Taxable |
| Corporate Investment | IBKR | login2 | Corporate |

Because each account lives behind its own login and interface, I can't see my **overall
exposure** at a glance. There's no single screen that answers "what do I actually hold,
what's it worth, and how am I doing?" across all four. That makes portfolio management and
decision-making slower and more error-prone — I can't easily spot overlap (e.g. the same
ETF held in two accounts), currency exposure, or my true total PnL.

This app fixes that by ingesting all four accounts, normalizing them into one canonical
model, and presenting a unified dashboard.

This is also a deliberate learning project — a hands-on way to work with real financial
APIs, data modeling, and a Python web backend.

---

## Scope (V1)

**In scope:** A consolidated ledger — pull everything in, normalize it, and show one view
of all positions across all four accounts with per-account and total market value and
unrealized PnL, plus a CAD-normalized grand total.

**Designed-for-but-not-built-yet:** The data model is built from day one so that exposure
analysis (allocation by asset class / sector / geography / currency, overlap detection)
and tax-aware insights can drop in later without a rewrite.

**Explicitly out of scope for now:** Real-time intraday streaming, trade execution,
multi-user support, cloud hosting.

---

## Key Design Principles

1. **Local-first.** This aggregates among the most sensitive data I own. It runs entirely
   on `localhost` — no cloud server, no public attack surface, no tokens sitting on a
   remote host. The "web app" experience is fully satisfied by a browser pointed at a
   local port.

2. **Separate the slow layer from the fast layer.** *Positions* (what I hold) change only
   when I trade and are captured as a **daily snapshot**. *Prices* (what it's worth) move
   continuously and are fetched independently and more often. The two have different
   sources, refresh rates, and auth models, and are never conflated.

3. **Append-only storage; compute on read.** Positions, prices, and FX rates are all
   written as immutable, timestamped rows. Market value and PnL are **never stored** —
   they're computed live from the latest snapshot × latest price × latest FX rate. This
   gives historical PnL and portfolio-over-time charts "for free" later, with no schema
   changes.

4. **Normalize to a canonical instrument.** The same security can appear under different
   tickers across brokers. A canonical `instrument` identity (with per-broker aliases and
   a cached IBKR `conid`) is what makes cross-account aggregation trustworthy.

---

## Architecture Overview

```
SLOW LAYER (daily / on-demand)              FAST LAYER (on-demand, later: every N min)
┌─────────────────────────┐                 ┌─────────────────────────┐
│ IBKR Flex (3 accts, XML)│─┐               │ IBKR Gateway price fetch │─┐
│ login1 + login2 tokens  │ │               │ (history endpoint, EOD)  │ │
└─────────────────────────┘ │               └─────────────────────────┘ │
┌─────────────────────────┐ ├──► Normalizer  ┌─────────────────────────┐ ├──► SQLite ──► FastAPI
│ CIBC CSV (manual upload)│─┘   (map to       │ FX feed (USDCAD)        │─┘   (append)   (computes
└─────────────────────────┘     instruments)  └─────────────────────────┘                value + PnL
                                                                                          on read)
```

### Data ingestion

- **IBKR positions — Flex Web Service.** Stateless token + query ID per login; returns
  positions/trades/cash as XML on request. Two tokens (login1 for RRSP + Margin, login2
  for Corporate). Used for the **daily position snapshot** only — Flex is rate-limited and
  not built for frequent polling.
- **CIBC positions — manual CSV upload.** No public API. Export holdings from the web
  portal; a parser maps CIBC's columns into the canonical position model.
- **Prices — IBKR Client Portal Gateway.** A local gateway authenticated by an interactive
  browser login. Prices are fetched **by conid**, so the same lookup marks both IBKR and
  CIBC holdings. No market-data subscription held → using **delayed end-of-day closes** via
  the history endpoint (steadier than the snapshot endpoint's prime-then-read flow, and
  sufficient for daily marks).
- **FX — USDCAD rate**, fetched alongside prices, stored append-only with timestamp.

### Data model (SQLite)

- `accounts` — one row per real account (broker, external id, label, tax type, base ccy).
- `instruments` — canonical security identity (asset class, symbol, currency, conid, ISIN).
- `instrument_aliases` — maps each broker's local ticker → canonical instrument.
- `positions` — append-only daily snapshots (snapshot_date, account, instrument, qty, avg cost).
- `prices` — append-only price marks (instrument, as_of, price, currency, source).
- `fx_rates` — append-only FX marks (pair, as_of, rate).

Market value, PnL, and the CAD total are derived at read time — never persisted.

---

## Setup

### Stack
- **Backend:** Python + FastAPI
- **Storage:** SQLite (zero-config, single-user, local)
- **Frontend:** TBD (server-rendered templates for V1, or React)
- **Scheduler (later):** APScheduler or an async loop for the periodic price refresh

### Prerequisites
- Python environment
- Java runtime (for the IBKR Client Portal Gateway)
- Two IBKR Flex tokens + query IDs (one per login), generated in IBKR Account Settings
- CIBC holdings CSV export

### Secrets handling
Flex tokens and any config live in a **local-only** file (gitignored) or environment
variables — never committed. Flex tokens rotate (~annually) and are refreshed
independently per login.

### Running the price gateway
1. Download, unzip, and start the IBKR Client Portal Gateway (`bin/run.sh root/conf.yaml`).
2. Open `https://localhost:5000`, accept the self-signed cert, and log in with IBKR
   credentials. The app never sees the password — it talks only to the authenticated local
   gateway.
3. The app checks `GET /iserver/auth/status` and `POST /tickle` before fetching; if the
   session has dropped it prompts for re-login rather than returning stale prices.

> **Note on the two logins:** the gateway authenticates one login at a time. Since prices
> are fetched by conid (account-agnostic), only one login needs to be active to cover the
> union of all held instruments. Account separation already happens in the Flex/positions
> layer.

---

## Requirements & Objectives

### Functional
- [ ] Ingest IBKR positions from both logins via Flex (daily snapshot).
- [ ] Ingest CIBC positions via CSV upload + parser.
- [ ] Normalize all holdings to canonical instruments (alias + conid resolution).
- [ ] Fetch delayed EOD prices by conid from the IBKR gateway.
- [ ] Fetch USDCAD FX rate.
- [ ] Display a consolidated dashboard: all positions, per-account and total market value,
      unrealized PnL, and a CAD-normalized grand total.
- [ ] Provide a manual "refresh" action for positions and prices.

### Non-functional
- [ ] Runs entirely locally; no data or secrets leave the machine.
- [ ] Append-only data; no destructive overwrites.
- [ ] Resilient to a dropped price session (clear re-auth prompt, no silent stale data).
- [ ] Position snapshot keeps working even if the price gateway is down.

### Stretch (V2+)
- [ ] Exposure analysis: allocation by asset class / sector / geography / currency.
- [ ] Overlap / look-through detection across accounts.
- [ ] Tax-aware insights (TFSA vs RRSP vs margin vs corporate treatment).
- [ ] Historical PnL and portfolio-value-over-time charts.
- [ ] Scheduled price refresh (every N minutes) via background scheduler.
- [ ] Intraday/live marks (requires market-data subscription).

---

## Next Steps
1. Scaffold the FastAPI project structure.
2. Build the config table for the two Flex tokens + query IDs.
3. Build the Flex fetcher and CIBC CSV parser into the normalizer.
4. Build the conid-based price fetcher (history endpoint first).
5. Wire up the read-time computation and the dashboard view.