import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from indicator_utils import ohlcv_to_series, rolling_stddev
from okx_force_order import (
    DEFAULT_MARGIN_PCT,
    DEFAULT_RISK_PCT,
    create_okx_exchange,
    force_open_with_tp_sl,
    get_position_snapshot,
    replace_protection_orders,
    verify_protection_orders,
)
from okx_scanner import scan_trend_hma_std_candidates
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_runtime_state import (
    close_strategy_trade,
    get_strategy_active_position,
    set_strategy_active_position,
)
from strategy_trend_hma_std import (
    STDDEV_LENGTH,
    STRATEGY_ID,
    TIMEFRAME,
    update_dynamic_take_profit,
)


@dataclass
class TrendStrategyConfig:
    leverage: int = 10
    td_mode: str = "cross"
    margin_ccy: str = "USDT"
    margin_pct: float = DEFAULT_MARGIN_PCT
    risk_pct: float = DEFAULT_RISK_PCT
    universe_limit: int = 70
    candles: int = 120
    scan_sleep_sec: float = 0.2
    protection_verify_wait_sec: float = 1.0


def _latest_closed_stddev_snapshot(exchange, symbol: str, candles: int) -> Dict[str, Any]:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=candles)
    if len(ohlcv) < STDDEV_LENGTH + 2:
        raise RuntimeError(f"not enough candles to manage {symbol}")

    series = ohlcv_to_series(ohlcv)
    closes = series["close"]
    timestamps = series["timestamp"]
    std_values = rolling_stddev(closes, STDDEV_LENGTH)
    closed_index = len(closes) - 2
    std_value = std_values[closed_index]
    if std_value is None:
        raise RuntimeError(f"stddev warmup incomplete for {symbol}")
    return {
        "timestamp_ms": float(timestamps[closed_index]),
        "stddev": float(std_value),
    }


def sync_strategy_position_state(exchange) -> Dict[str, Any]:
    active = get_strategy_active_position(STRATEGY_ID)
    if not active:
        return {"status": "no_active_position"}

    symbol = str(active.get("symbol") or "")
    side = str(active.get("side") or "")
    if not symbol or not side:
        cleared = close_strategy_trade(STRATEGY_ID, {"exit_reason": "cleared_invalid_state"})
        return {"status": "cleared_invalid_state", "cleared": cleared}

    exchange_position = get_position_snapshot(exchange=exchange, symbol=symbol, side=side)
    if not exchange_position:
        cleared = close_strategy_trade(
            STRATEGY_ID,
            {
                "exit_reason": "position_closed_on_exchange",
                "exit_price": active.get("mark_price") or active.get("entry_price"),
            },
        )
        return {
            "status": "position_closed",
            "symbol": symbol,
            "side": side,
            "cleared": cleared,
        }

    updated = dict(active)
    updated["contracts"] = exchange_position["contracts"]
    updated["entry_price"] = (
        exchange_position["entry_price"] if exchange_position["entry_price"] > 0 else active.get("entry_price")
    )
    updated["mark_price"] = exchange_position["mark_price"]
    updated["exchange_leverage"] = exchange_position["leverage"]
    set_strategy_active_position(STRATEGY_ID, updated)
    return {"status": "active_position_synced", "position": updated}


def manage_active_position(exchange, config: TrendStrategyConfig) -> Dict[str, Any]:
    sync_result = sync_strategy_position_state(exchange=exchange)
    if sync_result.get("status") != "active_position_synced":
        return sync_result

    active = dict(sync_result["position"])
    symbol = str(active["symbol"])
    side = str(active["side"])
    entry_price = float(active["entry_price"])
    qty = float(active["contracts"])
    stop_loss_price = float(active["stop_loss_price"])
    previous_tp = float(active.get("take_profit_price", 0) or 0)

    latest = _latest_closed_stddev_snapshot(exchange=exchange, symbol=symbol, candles=config.candles)
    latest_candle_ts = float(latest["timestamp_ms"])
    latest_stddev = float(latest["stddev"])
    target_tp = update_dynamic_take_profit(
        side=side,
        entry_price=entry_price,
        latest_closed_stddev=latest_stddev,
    )

    verify = verify_protection_orders(
        exchange=exchange,
        symbol=symbol,
        side=side,
        sl_price=stop_loss_price,
        tp_price=previous_tp if previous_tp > 0 else target_tp,
    )
    sl_ok, tp_ok = bool(verify[0]), bool(verify[1])

    should_update_tp = latest_candle_ts > float(active.get("last_tp_update_candle_timestamp_ms", 0) or 0)
    tp_changed = abs(float(target_tp) - float(previous_tp)) > max(abs(float(target_tp)) * 0.0000001, 1e-8)
    need_repair = not sl_ok or not tp_ok

    if should_update_tp or tp_changed or need_repair:
        protection = replace_protection_orders(
            exchange=exchange,
            symbol=symbol,
            side=side,
            qty=qty,
            sl_price=stop_loss_price,
            tp_price=target_tp,
            td_mode=config.td_mode,
            verify_wait_sec=config.protection_verify_wait_sec,
        )
        active["take_profit_price"] = target_tp
        active["latest_stddev"] = latest_stddev
        active["last_tp_update_candle_timestamp_ms"] = latest_candle_ts
        active["last_management_at_ms"] = int(time.time() * 1000)
        active["last_protection_sync"] = dict(protection)
        set_strategy_active_position(STRATEGY_ID, active)
        return {
            "status": "active_position_managed",
            "symbol": symbol,
            "side": side,
            "updated_tp": target_tp,
            "previous_tp": previous_tp,
            "latest_stddev": latest_stddev,
            "protection": protection,
        }

    return {
        "status": "active_position_unchanged",
        "symbol": symbol,
        "side": side,
        "take_profit_price": previous_tp,
        "latest_stddev": latest_stddev,
    }


def open_new_position(exchange, config: TrendStrategyConfig) -> Dict[str, Any]:
    active = get_strategy_active_position(STRATEGY_ID)
    if active:
        return {"status": "blocked_active_position", "position": active}

    capital_state = get_per_strategy_allocated_equity(exchange=exchange)

    scan_result = scan_trend_hma_std_candidates(
        exchange=exchange,
        limit=config.universe_limit,
        candles=config.candles,
        sleep_sec=config.scan_sleep_sec,
    )
    candidate = scan_result.get("best_candidate")
    if not candidate:
        return {"status": "no_candidate", "scan": scan_result}

    execution = force_open_with_tp_sl(
        exchange=exchange,
        symbol=str(candidate["symbol"]),
        side=str(candidate["side"]),
        qty=None,
        stop_loss_price=float(candidate["stop_loss_price"]),
        take_profit_price=float(candidate["take_profit_price"]),
        leverage=config.leverage,
        td_mode=config.td_mode,
        margin_ccy=config.margin_ccy,
        verify_wait_sec=config.protection_verify_wait_sec,
        client_order_id=f"{STRATEGY_ID}-{int(time.time())}",
        equity=float(capital_state["allocated_equity_usdt"]),
        margin_pct=config.margin_pct,
        risk_pct=config.risk_pct,
        require_stop_loss=True,
    )

    position_state = {
        "strategy_id": STRATEGY_ID,
        "symbol": candidate["symbol"],
        "side": candidate["side"],
        "timeframe": candidate["timeframe"],
        "entry_price": float(
            ((execution.get("order") or {}).get("average"))
            or ((execution.get("order") or {}).get("price"))
            or ((execution.get("plan") or {}).get("entry_price"))
            or candidate["entry_price"]
        ),
        "contracts": float(((execution.get("plan") or {}).get("qty")) or 0.0),
        "stop_loss_price": float(candidate["stop_loss_price"]),
        "take_profit_price": float(candidate["take_profit_price"]),
        "key_stddev": float(candidate["key_stddev"]),
        "latest_stddev": float(candidate["latest_stddev"]),
        "rr_ratio": float(candidate["rr_ratio"]),
        "win_rate": float(candidate["win_rate"]),
        "key_candle_timestamp_ms": float(candidate["key_candle_timestamp_ms"]),
        "entry_timestamp_ms": float(candidate["entry_timestamp_ms"]),
        "last_tp_update_candle_timestamp_ms": float(candidate["key_candle_timestamp_ms"]),
        "leverage": config.leverage,
        "td_mode": config.td_mode,
        "margin_pct": config.margin_pct,
        "risk_pct": config.risk_pct,
        "capital_state": dict(capital_state),
        "scan_candidate": dict(candidate),
        "execution": {
            "plan": execution.get("plan"),
            "order": execution.get("order"),
            "protection": execution.get("protection"),
        },
        "opened_at_ms": int(time.time() * 1000),
    }
    position_state = set_strategy_active_position(STRATEGY_ID, position_state)
    return {
        "status": "opened_position",
        "candidate": candidate,
        "capital_state": capital_state,
        "execution": execution,
        "position": position_state,
    }


def run_cycle(exchange=None, config: Optional[TrendStrategyConfig] = None) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or TrendStrategyConfig()

    management = manage_active_position(exchange=exchange, config=config)
    if management.get("status") in ("active_position_managed", "active_position_unchanged"):
        return {"phase": "manage", "result": management}

    opening = open_new_position(exchange=exchange, config=config)
    return {"phase": "open", "result": opening, "precheck": management}


if __name__ == "__main__":
    result = run_cycle()
    print(result)
