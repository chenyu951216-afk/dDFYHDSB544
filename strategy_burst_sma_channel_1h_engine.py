import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from okx_force_order import (
    DEFAULT_MARGIN_PCT,
    DEFAULT_RISK_PCT,
    cancel_protection_orders,
    create_okx_exchange,
    force_close_position,
    force_open_with_sl_only,
    get_position_snapshot,
    replace_stop_loss_only,
)
from okx_scanner import scan_burst_sma_channel_candidates
from strategy_burst_sma_channel_1h import (
    STRATEGY_ID,
    TIMEFRAME,
    build_live_management_snapshot,
)
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_runtime_state import (
    close_strategy_trade,
    get_strategy_active_position,
    set_strategy_active_position,
)


@dataclass
class BurstSMAChannelStrategyConfig:
    leverage: int = 10
    td_mode: str = "cross"
    margin_ccy: str = "USDT"
    margin_pct: float = DEFAULT_MARGIN_PCT
    risk_pct: float = DEFAULT_RISK_PCT
    universe_limit: int = 70
    candles: int = 360
    scan_sleep_sec: float = 0.2
    protection_verify_wait_sec: float = 1.0


def _compute_live_snapshot(exchange, symbol: str, candles: int) -> Dict[str, Any]:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=candles)
    return build_live_management_snapshot(symbol=symbol, ohlcv=ohlcv)


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
        return {"status": "position_closed", "symbol": symbol, "side": side, "cleared": cleared}

    updated = dict(active)
    updated["contracts"] = exchange_position["contracts"]
    updated["entry_price"] = (
        exchange_position["entry_price"] if exchange_position["entry_price"] > 0 else active.get("entry_price")
    )
    updated["mark_price"] = exchange_position["mark_price"]
    updated["exchange_leverage"] = exchange_position["leverage"]
    set_strategy_active_position(STRATEGY_ID, updated)
    return {"status": "active_position_synced", "position": updated}


def manage_active_position(exchange, config: BurstSMAChannelStrategyConfig) -> Dict[str, Any]:
    sync_result = sync_strategy_position_state(exchange=exchange)
    if sync_result.get("status") != "active_position_synced":
        return sync_result

    active = dict(sync_result["position"])
    symbol = str(active["symbol"])
    side = str(active["side"])
    qty = float(active["contracts"])
    live = _compute_live_snapshot(exchange=exchange, symbol=symbol, candles=config.candles)
    if live.get("status") != "ok":
        return {"status": "live_snapshot_error", "symbol": symbol, "side": side, "live": live}

    fixed_stop = float(active.get("fixed_stop_loss_price") or active.get("stop_loss_price") or 0.0)
    dynamic_take = float(live["take_high_current"] if side == "buy" else live["take_low_current"])

    if side == "buy":
        stop_hit = float(live["current_low"]) <= fixed_stop
        take_breakout_confirmed = float(live["prev_close"]) > float(live["take_high_prev"])
    else:
        stop_hit = float(live["current_high"]) >= fixed_stop
        take_breakout_confirmed = float(live["prev_close"]) < float(live["take_low_prev"])

    if stop_hit:
        cancel_protection_orders(exchange=exchange, symbol=symbol, side=side, order_kind="all")
        close_result = force_close_position(
            exchange=exchange,
            symbol=symbol,
            side=side,
            qty=qty,
            td_mode=config.td_mode,
            margin_ccy=config.margin_ccy,
        )
        cleared = close_strategy_trade(
            STRATEGY_ID,
            {
                "exit_reason": "fixed_stop_loss_hit",
                "close_result": close_result,
                "live_snapshot": live,
            },
        )
        return {
            "status": "position_closed_by_strategy",
            "symbol": symbol,
            "side": side,
            "reason": "fixed_stop_loss_hit",
            "close_result": close_result,
            "cleared": cleared,
            "live": live,
        }

    if take_breakout_confirmed:
        cancel_protection_orders(exchange=exchange, symbol=symbol, side=side, order_kind="all")
        close_result = force_close_position(
            exchange=exchange,
            symbol=symbol,
            side=side,
            qty=qty,
            td_mode=config.td_mode,
            margin_ccy=config.margin_ccy,
        )
        cleared = close_strategy_trade(
            STRATEGY_ID,
            {
                "exit_reason": "dynamic_take_profit_next_open_exit",
                "close_result": close_result,
                "live_snapshot": live,
            },
        )
        return {
            "status": "position_closed_by_strategy",
            "symbol": symbol,
            "side": side,
            "reason": "dynamic_take_profit_next_open_exit",
            "close_result": close_result,
            "cleared": cleared,
            "live": live,
        }

    previous_dynamic_take = float(active.get("take_profit_price") or 0.0)
    if abs(previous_dynamic_take - dynamic_take) > max(abs(dynamic_take) * 0.0000001, 1e-8):
        active["take_profit_price"] = dynamic_take
        active["last_live_snapshot"] = dict(live)
        active["last_management_at_ms"] = int(time.time() * 1000)
        set_strategy_active_position(STRATEGY_ID, active)
        return {
            "status": "active_position_managed",
            "symbol": symbol,
            "side": side,
            "fixed_stop_loss": fixed_stop,
            "updated_take_profit": dynamic_take,
            "live": live,
        }

    active["last_live_snapshot"] = dict(live)
    active["last_management_at_ms"] = int(time.time() * 1000)
    set_strategy_active_position(STRATEGY_ID, active)
    return {
        "status": "active_position_unchanged",
        "symbol": symbol,
        "side": side,
        "fixed_stop_loss": fixed_stop,
        "take_profit_price": dynamic_take,
        "live": live,
    }


def open_new_position(exchange, config: BurstSMAChannelStrategyConfig) -> Dict[str, Any]:
    active = get_strategy_active_position(STRATEGY_ID)
    if active:
        return {"status": "blocked_active_position", "position": active}

    capital_state = get_per_strategy_allocated_equity(exchange=exchange)
    scan_result = scan_burst_sma_channel_candidates(
        exchange=exchange,
        limit=config.universe_limit,
        candles=config.candles,
        sleep_sec=config.scan_sleep_sec,
    )
    candidate = scan_result.get("best_candidate")
    if not candidate:
        return {"status": "no_candidate", "scan": scan_result}

    execution = force_open_with_sl_only(
        exchange=exchange,
        symbol=str(candidate["symbol"]),
        side=str(candidate["side"]),
        qty=None,
        stop_loss_price=float(candidate["stop_loss_price"]),
        leverage=config.leverage,
        td_mode=config.td_mode,
        margin_ccy=config.margin_ccy,
        verify_wait_sec=config.protection_verify_wait_sec,
        client_order_id=f"{STRATEGY_ID}-{int(time.time())}",
        equity=float(capital_state["allocated_equity_usdt"]),
        entry_price=float(candidate["entry_price"]),
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
        "fixed_stop_loss_price": float(candidate["stop_loss_price"]),
        "take_profit_price": float(candidate["take_profit_price"]),
        "rr_ratio": float(candidate["rr_ratio"]),
        "win_rate": float(candidate["win_rate"]),
        "key_candle_timestamp_ms": float(candidate["key_candle_timestamp_ms"]),
        "entry_timestamp_ms": float(candidate["entry_timestamp_ms"]),
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


def run_cycle(exchange=None, config: Optional[BurstSMAChannelStrategyConfig] = None) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or BurstSMAChannelStrategyConfig()

    management = manage_active_position(exchange=exchange, config=config)
    if management.get("status") in ("active_position_managed", "active_position_unchanged"):
        return {"phase": "manage", "result": management}

    opening = open_new_position(exchange=exchange, config=config)
    return {"phase": "open", "result": opening, "precheck": management}


if __name__ == "__main__":
    result = run_cycle()
    print(result)
