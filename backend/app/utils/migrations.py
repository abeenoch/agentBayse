"""Lightweight, idempotent migrations run at startup.

These helpers avoid bringing in Alembic for now. They inspect the current
schema and apply minimal ALTER TABLE statements for newly introduced fields.
"""

from sqlalchemy import inspect, text


async def run_startup_migrations(conn):
    """Apply idempotent DDL migrations using a sync connection shim."""

    def _migrate(sync_conn):
        inspector = inspect(sync_conn)

        # agent_config.max_open_positions
        agent_columns = {col["name"] for col in inspector.get_columns("agent_config")}
        if "max_open_positions" not in agent_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE agent_config ADD COLUMN max_open_positions INTEGER NOT NULL DEFAULT 2"
            )
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET max_open_positions = 2 WHERE max_open_positions IS NULL"
        )
        if "min_confidence" not in agent_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE agent_config ADD COLUMN min_confidence INTEGER NOT NULL DEFAULT 50"
            )
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET min_confidence = 50 WHERE min_confidence IS NULL"
        )
        if "balance_reserve_pct" not in agent_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE agent_config ADD COLUMN balance_reserve_pct FLOAT NOT NULL DEFAULT 0.30"
            )
        # Enforce 3 max open positions and 30% reserve on existing rows
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET max_open_positions = 3 WHERE max_open_positions > 3 OR max_open_positions IS NULL"
        )
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET balance_reserve_pct = 0.30 WHERE balance_reserve_pct IS NULL"
        )

        # signals.resolution + signals.pnl
        signal_columns = {col["name"] for col in inspector.get_columns("signals")}
        if "event_id" not in signal_columns:
            sync_conn.exec_driver_sql("ALTER TABLE signals ADD COLUMN event_id VARCHAR")
        if "resolution" not in signal_columns:
            sync_conn.exec_driver_sql("ALTER TABLE signals ADD COLUMN resolution VARCHAR")
        if "pnl" not in signal_columns:
            sync_conn.exec_driver_sql("ALTER TABLE signals ADD COLUMN pnl FLOAT")

        # Clean up stale trades — run every startup to clear ghost records
        # Any EXECUTED trade with no resolution older than 2 hours is stale
        # (all markets close within 1 hour, so 2h is a safe threshold)
        sync_conn.exec_driver_sql(
            """
            UPDATE trades
            SET status = 'STALE', resolution = 'EXPIRED'
            WHERE status = 'EXECUTED'
              AND resolution IS NULL
              AND (
                bayse_order_id IS NULL
                OR bayse_order_id = 'CLOB'
                OR LENGTH(bayse_order_id) < 8
                OR created_at < NOW() - INTERVAL '2 hours'
              )
            """
        )
        # Also deduplicate: if multiple EXECUTED trades exist for the same market,
        # keep only the most recent one
        sync_conn.exec_driver_sql(
            """
            UPDATE trades
            SET status = 'STALE', resolution = 'DUPLICATE'
            WHERE status = 'EXECUTED'
              AND resolution IS NULL
              AND id NOT IN (
                SELECT DISTINCT ON (market_id) id
                FROM trades
                WHERE status = 'EXECUTED' AND resolution IS NULL
                ORDER BY market_id, created_at DESC
              )
            """
        )

    await conn.run_sync(_migrate)
