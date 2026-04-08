import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "ai_learning.sqlite3")


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_learning_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS open_trade_records (
                trade_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                source_strategy_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                timeframe TEXT,
                entry_timestamp_ms INTEGER,
                entry_price REAL,
                leverage REAL,
                used_margin_usdt REAL,
                fee_entry_usdt REAL,
                rr_ratio REAL,
                raw_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS closed_trades (
                trade_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                source_strategy_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                timeframe TEXT,
                entry_timestamp_ms INTEGER,
                exit_timestamp_ms INTEGER,
                entry_price REAL,
                exit_price REAL,
                leverage REAL,
                used_margin_usdt REAL,
                fees_usdt REAL,
                gross_pnl_usdt REAL,
                net_pnl_usdt REAL,
                hold_minutes REAL,
                exit_reason TEXT,
                auto_reversed_to_side TEXT,
                raw_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_rollups (
                strategy_id TEXT PRIMARY KEY,
                raw_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS symbol_stats (
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (strategy_id, symbol)
            );

            CREATE TABLE IF NOT EXISTS weekly_reviews (
                week_key TEXT PRIMARY KEY,
                requested_at_utc TEXT,
                raw_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_strategy_profiles (
                strategy_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                profile_version INTEGER,
                raw_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning_meta (
                meta_key TEXT PRIMARY KEY,
                meta_value TEXT,
                updated_at_ms INTEGER NOT NULL
            );
            """
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


def upsert_open_trade_record(entry: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO open_trade_records (
                trade_id, strategy_id, source_strategy_id, symbol, side, timeframe,
                entry_timestamp_ms, entry_price, leverage, used_margin_usdt,
                fee_entry_usdt, rr_ratio, raw_json, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                strategy_id=excluded.strategy_id,
                source_strategy_id=excluded.source_strategy_id,
                symbol=excluded.symbol,
                side=excluded.side,
                timeframe=excluded.timeframe,
                entry_timestamp_ms=excluded.entry_timestamp_ms,
                entry_price=excluded.entry_price,
                leverage=excluded.leverage,
                used_margin_usdt=excluded.used_margin_usdt,
                fee_entry_usdt=excluded.fee_entry_usdt,
                rr_ratio=excluded.rr_ratio,
                raw_json=excluded.raw_json,
                updated_at_ms=excluded.updated_at_ms
            """,
            (
                str(entry.get("trade_id") or ""),
                str(entry.get("strategy_id") or ""),
                str(entry.get("source_strategy_id") or ""),
                str(entry.get("symbol") or ""),
                str(entry.get("side") or ""),
                str(entry.get("timeframe") or ""),
                int(entry.get("entry_timestamp_ms") or 0),
                float(entry.get("entry_price") or 0.0),
                float(entry.get("leverage") or 0.0),
                float(entry.get("used_margin_usdt") or 0.0),
                float(entry.get("fee_entry_usdt") or 0.0),
                float(entry.get("rr_ratio") or 0.0),
                json.dumps(entry, ensure_ascii=False),
                _now_ms(),
            ),
        )


def delete_open_trade_record(trade_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM open_trade_records WHERE trade_id = ?", (str(trade_id or ""),))


def upsert_closed_trade_record(entry: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO closed_trades (
                trade_id, strategy_id, source_strategy_id, symbol, side, timeframe,
                entry_timestamp_ms, exit_timestamp_ms, entry_price, exit_price, leverage,
                used_margin_usdt, fees_usdt, gross_pnl_usdt, net_pnl_usdt, hold_minutes,
                exit_reason, auto_reversed_to_side, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                strategy_id=excluded.strategy_id,
                source_strategy_id=excluded.source_strategy_id,
                symbol=excluded.symbol,
                side=excluded.side,
                timeframe=excluded.timeframe,
                entry_timestamp_ms=excluded.entry_timestamp_ms,
                exit_timestamp_ms=excluded.exit_timestamp_ms,
                entry_price=excluded.entry_price,
                exit_price=excluded.exit_price,
                leverage=excluded.leverage,
                used_margin_usdt=excluded.used_margin_usdt,
                fees_usdt=excluded.fees_usdt,
                gross_pnl_usdt=excluded.gross_pnl_usdt,
                net_pnl_usdt=excluded.net_pnl_usdt,
                hold_minutes=excluded.hold_minutes,
                exit_reason=excluded.exit_reason,
                auto_reversed_to_side=excluded.auto_reversed_to_side,
                raw_json=excluded.raw_json
            """,
            (
                str(entry.get("trade_id") or ""),
                str(entry.get("strategy_id") or ""),
                str(entry.get("source_strategy_id") or ""),
                str(entry.get("symbol") or ""),
                str(entry.get("side") or ""),
                str(entry.get("timeframe") or ""),
                int(entry.get("entry_timestamp_ms") or 0),
                int(entry.get("exit_timestamp_ms") or 0),
                float(entry.get("entry_price") or 0.0),
                float(entry.get("exit_price") or 0.0),
                float(entry.get("leverage") or 0.0),
                float(entry.get("used_margin_usdt") or 0.0),
                float(entry.get("fees_usdt") or 0.0),
                float(entry.get("gross_pnl_usdt") or 0.0),
                float(entry.get("net_pnl_usdt") or 0.0),
                float(entry.get("hold_minutes") or 0.0),
                str(entry.get("exit_reason") or ""),
                str(entry.get("auto_reversed_to_side") or ""),
                json.dumps(entry, ensure_ascii=False),
            ),
        )


def upsert_strategy_rollup_record(strategy_id: str, payload: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO strategy_rollups (strategy_id, raw_json, updated_at_ms)
            VALUES (?, ?, ?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                raw_json=excluded.raw_json,
                updated_at_ms=excluded.updated_at_ms
            """,
            (str(strategy_id or ""), json.dumps(payload, ensure_ascii=False), _now_ms()),
        )


def upsert_symbol_stat_record(strategy_id: str, symbol: str, payload: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO symbol_stats (strategy_id, symbol, raw_json, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(strategy_id, symbol) DO UPDATE SET
                raw_json=excluded.raw_json,
                updated_at_ms=excluded.updated_at_ms
            """,
            (str(strategy_id or ""), str(symbol or ""), json.dumps(payload, ensure_ascii=False), _now_ms()),
        )


def upsert_weekly_review_record(week_key: str, payload: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO weekly_reviews (week_key, requested_at_utc, raw_json, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(week_key) DO UPDATE SET
                requested_at_utc=excluded.requested_at_utc,
                raw_json=excluded.raw_json,
                updated_at_ms=excluded.updated_at_ms
            """,
            (
                str(week_key or ""),
                str(payload.get("requested_at_utc") or ""),
                json.dumps(payload, ensure_ascii=False),
                _now_ms(),
            ),
        )


def upsert_ai_strategy_profile_record(profile: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_strategy_profiles (strategy_id, enabled, profile_version, raw_json, updated_at_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                enabled=excluded.enabled,
                profile_version=excluded.profile_version,
                raw_json=excluded.raw_json,
                updated_at_ms=excluded.updated_at_ms
            """,
            (
                str(profile.get("strategy_id") or "ai_generated_meta_v1"),
                1 if bool(profile.get("enabled")) else 0,
                int(profile.get("profile_version") or 0),
                json.dumps(profile, ensure_ascii=False),
                _now_ms(),
            ),
        )


def set_meta_value(key: str, value: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO learning_meta (meta_key, meta_value, updated_at_ms)
            VALUES (?, ?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET
                meta_value=excluded.meta_value,
                updated_at_ms=excluded.updated_at_ms
            """,
            (str(key or ""), str(value or ""), _now_ms()),
        )


def fetch_learning_overview() -> Dict[str, Any]:
    with _connect() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS trade_count,
                COALESCE(SUM(net_pnl_usdt), 0) AS total_net_pnl_usdt,
                COALESCE(SUM(gross_pnl_usdt), 0) AS total_gross_pnl_usdt,
                COALESCE(SUM(fees_usdt), 0) AS total_fees_usdt
            FROM closed_trades
            """
        ).fetchone()
        strategies = conn.execute(
            """
            SELECT strategy_id, COUNT(*) AS trades, COALESCE(SUM(net_pnl_usdt), 0) AS net_pnl_usdt
            FROM closed_trades
            GROUP BY strategy_id
            ORDER BY net_pnl_usdt DESC
            """
        ).fetchall()
        return {
            "trade_count": int(totals["trade_count"] or 0),
            "total_net_pnl_usdt": float(totals["total_net_pnl_usdt"] or 0.0),
            "total_gross_pnl_usdt": float(totals["total_gross_pnl_usdt"] or 0.0),
            "total_fees_usdt": float(totals["total_fees_usdt"] or 0.0),
            "strategies": [dict(row) for row in strategies],
        }


def fetch_closed_trades(strategy_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    sql = "SELECT raw_json FROM closed_trades"
    params: List[Any] = []
    if strategy_id:
        sql += " WHERE strategy_id = ?"
        params.append(str(strategy_id))
    sql += " ORDER BY exit_timestamp_ms DESC LIMIT ?"
    params.append(max(int(limit or 1), 1))
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [json.loads(row["raw_json"]) for row in rows]


def fetch_strategy_rollups() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT raw_json FROM strategy_rollups ORDER BY strategy_id").fetchall()
    return [json.loads(row["raw_json"]) for row in rows]


def fetch_symbol_stats(strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT strategy_id, symbol, raw_json FROM symbol_stats"
    params: List[Any] = []
    if strategy_id:
        sql += " WHERE strategy_id = ?"
        params.append(str(strategy_id))
    sql += " ORDER BY strategy_id, symbol"
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["raw_json"])
        items.append(
            {
                "strategy_id": row["strategy_id"],
                "symbol": row["symbol"],
                "stats": payload,
            }
        )
    return items


def fetch_weekly_reviews(limit: int = 20) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT raw_json FROM weekly_reviews ORDER BY week_key DESC LIMIT ?",
            (max(int(limit or 1), 1),),
        ).fetchall()
    return [json.loads(row["raw_json"]) for row in rows]


def fetch_ai_profile() -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT raw_json FROM ai_strategy_profiles ORDER BY updated_at_ms DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return json.loads(row["raw_json"])
