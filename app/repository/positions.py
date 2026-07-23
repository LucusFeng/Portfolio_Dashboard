import sqlite3
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from app.models import ParsedInstrument, ParsedPosition
from app.repository.instruments import upsert_account, upsert_alias, upsert_instrument


@dataclass
class OpenLot:
    account_id: int
    instrument_id: int
    open_date: str
    open_quantity: float
    remaining_qty: float
    cost_per_unit: float
    cost_currency: str
    open_txn_id: int

    def add_buy(self, quantity: float, price: float) -> None:
        total_cost = self.open_quantity * self.cost_per_unit + quantity * price
        self.open_quantity += quantity
        self.remaining_qty += quantity
        self.cost_per_unit = total_cost / self.open_quantity


def rebuild_lots(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM lots")
    rows = conn.execute(
        """
        SELECT id, txn_date, account_id, instrument_id, txn_type, quantity, price, currency
        FROM transactions
        WHERE instrument_id IS NOT NULL AND txn_type IN ('BUY', 'SELL')
        ORDER BY account_id, instrument_id, txn_date, id
        """
    ).fetchall()
    buckets: Dict[Tuple[int, int], List[OpenLot]] = {}
    for row in rows:
        key = (int(row["account_id"]), int(row["instrument_id"]))
        lots = buckets.setdefault(key, [])
        qty = abs(float(row["quantity"] or 0))
        if row["txn_type"] == "BUY":
            price = float(row["price"] or 0)
            matching_lot = next(
                (
                    lot
                    for lot in lots
                    if lot.open_date == row["txn_date"]
                    and lot.cost_currency == row["currency"]
                ),
                None,
            )
            if matching_lot is not None:
                matching_lot.add_buy(qty, price)
            else:
                lots.append(
                    OpenLot(
                        account_id=key[0],
                        instrument_id=key[1],
                        open_date=row["txn_date"],
                        open_quantity=qty,
                        remaining_qty=qty,
                        cost_per_unit=price,
                        cost_currency=row["currency"],
                        open_txn_id=int(row["id"]),
                    )
                )
            continue
        remaining_to_sell = qty
        for lot in lots:
            if remaining_to_sell <= 0:
                break
            consumed = min(lot.remaining_qty, remaining_to_sell)
            lot.remaining_qty -= consumed
            remaining_to_sell -= consumed

    inserted = 0
    for lots in buckets.values():
        for lot in lots:
            if lot.remaining_qty <= 1e-9:
                continue
            conn.execute(
                """
                INSERT INTO lots
                    (account_id, instrument_id, open_date, open_quantity, remaining_qty,
                     cost_per_unit, cost_currency, open_txn_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lot.account_id,
                    lot.instrument_id,
                    lot.open_date,
                    lot.open_quantity,
                    lot.remaining_qty,
                    lot.cost_per_unit,
                    lot.cost_currency,
                    lot.open_txn_id,
                ),
            )
            inserted += 1
    return inserted


def rebuild_positions(conn: sqlite3.Connection, snapshot_date: str) -> int:
    conn.execute("DELETE FROM positions WHERE snapshot_date = ?", (snapshot_date,))
    rows = conn.execute(
        """
        SELECT account_id, instrument_id, SUM(remaining_qty) AS quantity,
               SUM(remaining_qty * cost_per_unit) / NULLIF(SUM(remaining_qty), 0) AS avg_cost,
               cost_currency
        FROM lots
        GROUP BY account_id, instrument_id, cost_currency
        HAVING ABS(quantity) > 1e-9
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO positions
                (snapshot_date, account_id, instrument_id, quantity, avg_cost, cost_currency)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, account_id, instrument_id) DO UPDATE SET
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                cost_currency = excluded.cost_currency,
                source = 'derived_transactions'
            """,
            (
                snapshot_date,
                row["account_id"],
                row["instrument_id"],
                row["quantity"],
                row["avg_cost"],
                row["cost_currency"],
            ),
        )
        inserted += 1

    return inserted


def rebuild_derived_state(conn: sqlite3.Connection, snapshot_date: str) -> Tuple[int, int]:
    lots = rebuild_lots(conn)
    positions = rebuild_positions(conn, snapshot_date)
    return lots, positions


def record_reconciliation(
    conn: sqlite3.Connection,
    broker_positions: Iterable[ParsedPosition],
    snapshot_date: str,
    source: str,
) -> int:
    inserted = 0
    for broker_position in broker_positions:
        account_id = upsert_account(conn, "IBKR", broker_position.account_external_id, broker_position.account_label)
        instrument_id = upsert_instrument(
            conn,
            ParsedInstrument(
                asset_class=broker_position.asset_class,
                symbol=broker_position.symbol,
                name=broker_position.name,
                currency=broker_position.currency,
                conid=broker_position.conid,
            ),
        )
        upsert_alias(conn, "IBKR", broker_position.symbol, instrument_id)
        derived = conn.execute(
            """
            SELECT quantity FROM positions
            WHERE snapshot_date = ? AND account_id = ? AND instrument_id = ?
            """,
            (snapshot_date, account_id, instrument_id),
        ).fetchone()
        derived_quantity = float(derived["quantity"]) if derived else 0.0
        difference = float(broker_position.quantity) - derived_quantity
        conn.execute(
            """
            INSERT INTO reconciliations
                (snapshot_date, account_id, instrument_id, broker_quantity, derived_quantity,
                 difference, status, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                account_id,
                instrument_id,
                broker_position.quantity,
                derived_quantity,
                difference,
                "ok" if abs(difference) < 1e-6 else "mismatch",
                source,
            ),
        )
        inserted += 1
    return inserted

