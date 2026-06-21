import sqlite3


def latest_position_marks(conn: sqlite3.Connection):
    return conn.execute(
        """
        WITH latest_date AS (
            SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
            FROM positions
            GROUP BY account_id, instrument_id
        ),
        latest_price AS (
            SELECT p.instrument_id, p.price, p.currency, p.as_of
            FROM prices p
            JOIN (
                SELECT instrument_id, MAX(as_of) AS as_of
                FROM prices
                GROUP BY instrument_id
            ) latest
              ON latest.instrument_id = p.instrument_id
             AND latest.as_of = p.as_of
        )
        SELECT
            a.label AS account_label,
            i.id AS instrument_id,
            i.symbol,
            i.name,
            i.asset_class,
            i.currency AS instrument_currency,
            pos.quantity,
            pos.avg_cost,
            pos.cost_currency,
            pr.price,
            pr.currency AS price_currency
        FROM positions pos
        JOIN latest_date d
          ON d.account_id = pos.account_id
         AND d.instrument_id = pos.instrument_id
         AND d.snapshot_date = pos.snapshot_date
        JOIN accounts a ON a.id = pos.account_id
        JOIN instruments i ON i.id = pos.instrument_id
        LEFT JOIN latest_price pr ON pr.instrument_id = i.id
        ORDER BY a.label, i.symbol
        """
    ).fetchall()


def open_lot_marks(conn: sqlite3.Connection):
    return conn.execute(
        """
        WITH latest_price AS (
            SELECT p.instrument_id, p.price, p.currency, p.as_of
            FROM prices p
            JOIN (
                SELECT instrument_id, MAX(as_of) AS as_of
                FROM prices
                GROUP BY instrument_id
            ) latest
              ON latest.instrument_id = p.instrument_id
             AND latest.as_of = p.as_of
        )
        SELECT
            a.label AS account_label,
            i.symbol,
            i.name,
            i.asset_class,
            l.open_date,
            l.open_quantity,
            l.remaining_qty,
            l.cost_per_unit,
            l.cost_currency,
            pr.price,
            pr.currency AS price_currency
        FROM lots l
        JOIN accounts a ON a.id = l.account_id
        JOIN instruments i ON i.id = l.instrument_id
        LEFT JOIN latest_price pr ON pr.instrument_id = i.id
        WHERE l.remaining_qty > 1e-9
        ORDER BY a.label, i.symbol, l.open_date, l.id
        """
    ).fetchall()


def contribution_cashflows(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT txn_date, currency, SUM(amount) AS amount
        FROM transactions
        WHERE txn_type IN ('DEPOSIT', 'WITHDRAWAL')
        GROUP BY txn_date, currency
        ORDER BY txn_date
        """
    ).fetchall()


def latest_reconciliation_warnings(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT a.label AS account_label, i.symbol, r.broker_quantity,
               r.derived_quantity, r.difference, r.status
        FROM reconciliations r
        JOIN accounts a ON a.id = r.account_id
        JOIN instruments i ON i.id = r.instrument_id
        WHERE r.status != 'ok'
        ORDER BY r.created_at DESC, a.label, i.symbol
        LIMIT 20
        """
    ).fetchall()
