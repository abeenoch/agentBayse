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
        if "bayes_live_decision_mode" not in agent_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE agent_config ADD COLUMN bayes_live_decision_mode BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if "bayes_state_key" not in agent_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE agent_config ADD COLUMN bayes_state_key VARCHAR NOT NULL DEFAULT 'default'"
            )
        # Enforce 3 max open positions and 30% reserve on existing rows
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET max_open_positions = 3 WHERE max_open_positions > 3 OR max_open_positions IS NULL"
        )
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET balance_reserve_pct = 0.30 WHERE balance_reserve_pct IS NULL"
        )
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET bayes_live_decision_mode = TRUE WHERE bayes_live_decision_mode IS NULL"
        )
        sync_conn.exec_driver_sql(
            "UPDATE agent_config SET bayes_state_key = 'default' WHERE bayes_state_key IS NULL OR bayes_state_key = ''"
        )

        # signals.resolution + signals.pnl
        signal_columns = {col["name"] for col in inspector.get_columns("signals")}
        if "event_id" not in signal_columns:
            sync_conn.exec_driver_sql("ALTER TABLE signals ADD COLUMN event_id VARCHAR")
        if "resolution" not in signal_columns:
            sync_conn.exec_driver_sql("ALTER TABLE signals ADD COLUMN resolution VARCHAR")
        if "pnl" not in signal_columns:
            sync_conn.exec_driver_sql("ALTER TABLE signals ADD COLUMN pnl FLOAT")
        if "bayes_state_key" not in signal_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE signals ADD COLUMN bayes_state_key VARCHAR NOT NULL DEFAULT 'default'"
            )
        sync_conn.exec_driver_sql(
            "UPDATE signals SET bayes_state_key = 'default' WHERE bayes_state_key IS NULL OR bayes_state_key = ''"
        )
        if "direction_correct" not in signal_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE signals ADD COLUMN direction_correct INTEGER"  # 1=correct, 0=wrong, NULL=unresolved
            )

        trade_columns = {col["name"] for col in inspector.get_columns("trades")}
        if "bayes_state_key" not in trade_columns:
            sync_conn.exec_driver_sql(
                "ALTER TABLE trades ADD COLUMN bayes_state_key VARCHAR NOT NULL DEFAULT 'default'"
            )
        sync_conn.exec_driver_sql(
            "UPDATE trades SET bayes_state_key = 'default' WHERE bayes_state_key IS NULL OR bayes_state_key = ''"
        )

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
        sync_conn.exec_driver_sql(
            """
            UPDATE trades
            SET status = 'STALE'
            WHERE status = 'EXECUTED'
              AND resolution = 'EXPIRED'
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

        if not inspector.has_table("bayes_backtest_snapshots"):
            sync_conn.exec_driver_sql(
                """
                CREATE TABLE bayes_backtest_snapshots (
                    id UUID PRIMARY KEY,
                    state_key VARCHAR NOT NULL,
                    period_kind VARCHAR NOT NULL,
                    period_key VARCHAR NOT NULL,
                    rows_scored INTEGER NOT NULL DEFAULT 0,
                    generated_at TIMESTAMP NOT NULL,
                    summary_json JSON NOT NULL,
                    CONSTRAINT uq_bayes_backtest_snapshot_scope UNIQUE (state_key, period_kind, period_key)
                )
                """
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_bayes_backtest_snapshots_state_key ON bayes_backtest_snapshots (state_key)"
            )

        if not inspector.has_table("bayes_training_runs"):
            sync_conn.exec_driver_sql(
                """
                CREATE TABLE bayes_training_runs (
                    id UUID PRIMARY KEY,
                    state_key VARCHAR NOT NULL,
                    model_version VARCHAR NOT NULL DEFAULT 'logreg_v1',
                    sample_size INTEGER NOT NULL DEFAULT 0,
                    train_size INTEGER NOT NULL DEFAULT 0,
                    test_size INTEGER NOT NULL DEFAULT 0,
                    positive_rate FLOAT NOT NULL DEFAULT 0,
                    feature_names JSON NOT NULL,
                    coefficients JSON NOT NULL,
                    metrics_json JSON NOT NULL,
                    calibration_json JSON NOT NULL,
                    trained_at TIMESTAMP NOT NULL
                )
                """
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_bayes_training_runs_state_key ON bayes_training_runs (state_key)"
            )

    await conn.run_sync(_migrate)
