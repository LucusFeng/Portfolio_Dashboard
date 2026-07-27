import sqlite3


def latest_position_marks(conn: sqlite3.Connection):
    return conn.execute(
        """
        WITH latest_date AS (
            SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
            FROM positions
            GROUP BY account_id, instrument_id
        ),
        latest_position AS (
            SELECT pos.*
            FROM positions pos
            JOIN latest_date d
              ON d.account_id = pos.account_id
             AND d.instrument_id = pos.instrument_id
             AND d.snapshot_date = pos.snapshot_date
        ),
        latest_value_date AS (
            SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
            FROM position_values
            GROUP BY account_id, instrument_id
        ),
        latest_value AS (
            SELECT pv.*
            FROM position_values pv
            JOIN latest_value_date d
              ON d.account_id = pv.account_id
             AND d.instrument_id = pv.instrument_id
             AND d.snapshot_date = pv.snapshot_date
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
            a.broker AS account_broker,
            i.id AS instrument_id,
            i.symbol,
            i.name,
            i.asset_class,
            i.currency AS instrument_currency,
            pv.quantity,
            pos.quantity AS derived_quantity,
            pos.avg_cost,
            pos.cost_currency,
            CASE
                WHEN ABS(pv.quantity) > 1e-9 THEN pv.value_native / pv.quantity
                ELSE NULL
            END AS price,
            pv.native_currency AS price_currency,
            pv.value_native AS flex_value_native,
            pv.value_base AS flex_value_base,
            pv.native_currency AS flex_native_currency,
            'IBKR Flex' AS value_source
        FROM latest_value pv
        JOIN accounts a ON a.id = pv.account_id
        JOIN instruments i ON i.id = pv.instrument_id
        LEFT JOIN latest_position pos
          ON pos.account_id = pv.account_id
         AND pos.instrument_id = pv.instrument_id

        UNION ALL

        SELECT
            a.label AS account_label,
            a.broker AS account_broker,
            i.id AS instrument_id,
            i.symbol,
            i.name,
            i.asset_class,
            i.currency AS instrument_currency,
            pos.quantity,
            pos.quantity AS derived_quantity,
            pos.avg_cost,
            pos.cost_currency,
            pr.price,
            pr.currency AS price_currency,
            NULL AS flex_value_native,
            NULL AS flex_value_base,
            NULL AS flex_native_currency,
            'Price' AS value_source
        FROM latest_position pos
        JOIN accounts a ON a.id = pos.account_id
        JOIN instruments i ON i.id = pos.instrument_id
        LEFT JOIN latest_price pr ON pr.instrument_id = i.id
        WHERE a.broker != 'IBKR'

        UNION ALL

        SELECT
            a.label AS account_label,
            a.broker AS account_broker,
            i.id AS instrument_id,
            i.symbol,
            i.name,
            i.asset_class,
            i.currency AS instrument_currency,
            pos.quantity,
            pos.quantity AS derived_quantity,
            pos.avg_cost,
            pos.cost_currency,
            NULL AS price,
            NULL AS price_currency,
            NULL AS flex_value_native,
            NULL AS flex_value_base,
            NULL AS flex_native_currency,
            'IBKR Flex' AS value_source
        FROM latest_position pos
        JOIN accounts a ON a.id = pos.account_id
        JOIN instruments i ON i.id = pos.instrument_id
        LEFT JOIN latest_value pv
          ON pv.account_id = pos.account_id
         AND pv.instrument_id = pos.instrument_id
        WHERE a.broker = 'IBKR'
          AND pv.id IS NULL

        ORDER BY account_label, symbol
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
        ),
        latest_value_date AS (
            SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
            FROM position_values
            GROUP BY account_id, instrument_id
        ),
        latest_value AS (
            SELECT pv.*
            FROM position_values pv
            JOIN latest_value_date d
              ON d.account_id = pv.account_id
             AND d.instrument_id = pv.instrument_id
             AND d.snapshot_date = pv.snapshot_date
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
            CASE
                WHEN pv.value_native IS NOT NULL AND ABS(pv.quantity) > 1e-9
                THEN pv.value_native / pv.quantity
                ELSE pr.price
            END AS price,
            COALESCE(pv.native_currency, pr.currency) AS price_currency
        FROM lots l
        JOIN accounts a ON a.id = l.account_id
        JOIN instruments i ON i.id = l.instrument_id
        LEFT JOIN latest_price pr ON pr.instrument_id = i.id
        LEFT JOIN latest_value pv
          ON pv.account_id = l.account_id
         AND pv.instrument_id = l.instrument_id
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
