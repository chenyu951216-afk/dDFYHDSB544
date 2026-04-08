import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from okx_force_order import (
    DEFAULT_MARGIN_PCT,
    DEFAULT_RISK_PCT,
    cancel_protection_orders,
    create_okx_exchange,
    force_close_position,
    force_open_with_tp_sl,
    get_position_snapshot,
)
from okx_scanner import scan_mean_reversion_atr_candidates
from strategy_mean_reversion_atr_2h_daily import (
    ENTRY_TIMEFRAME,
    STRATEGY_ID,
    build_live_management_snapshot,
)
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_runtime_state import (
    close_strategy_trade,
    get_strategy_active_position,
    set_strategy_active_position,
)


@dataclass
class MeanReversionAtrStrategyConfig:
    leverage: int = 10
    td_mode: str = "cross"
    margin_ccy: str = "USDT"
    margin_pct: float = DEFAULT_MARGIN_PCT
    risk_pct: float = DEFAULT_RISK_PCT
    universe_limit: int = 70
    two_h_candles: int = 160
    daily_candles: int = 60
    scan_sleep_sec: float = 0.2
    protection_verify_wait_sec: float = 1.0


def _compute_live_snapshot(exchange, symbol: str, config: MeanReversionAtrStrategyConfig) -> Dict[str, Any]:
    two_h_ohlcv = exchange.fetch_ohlcv(symbol, timeframe=ENTRY_TIMEFRAME, limit=config.two_h_candles)
    daily_ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=config.daily_candles)
    return build_live_management_snapshot(symbol=symbol, two_h_ohlcv=two_h_ohlcv, daily_ohlcv=daily_ohlcv)


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


def _reverse_position(
    exchange,
    active: Dict[str, Any],
    opposite_signal: Dict[str, Any],
    config: MeanReversionAtrStrategyConfig,
    live: Dict[str, Any],
) -> Dict[str, Any]:
    symbol = str(active["symbol"])
    old_side = str(active["side"])
    qty = float(active["contracts"])

    cancel_protection_orders(exchange=exchange, symbol=symbol, side=old_side, order_kind="all")
    close_result = force_close_position(
        exchange=exchange,
        symbol=symbol,
        side=old_side,
        qty=qty,
        td_mode=config.td_mode,
        margin_ccy=config.margin_ccy,
    )
    closed_trade = close_strategy_trade(
        STRATEGY_ID,
        {
            "exit_reason": "reverse_signal",
            "close_result": close_result,
            "live_snapshot": live,
            "auto_reversed_to_side": str(opposite_signal["side"]),
        },
    )

    capital_state = get_per_strategy_allocated_equity(exchange=exchange)
    execution = force_open_with_tp_sl(
        exchange=exchange,
        symbol=symbol,
        side=str(opposite_signal["side"]),
        qty=None,
        stop_loss_price=float(opposite_signal["stop_loss_price"]),
        take_profit_price=float(opposite_signal["take_profit_price"]),
        leverage=config.leverage,
        td_mode=config.td_mode,
        margin_ccy=config.margin_ccy,
        verify_wait_sec=config.protection_verify_wait_sec,
        client_order_id=f"{STRATEGY_ID}-{int(time.time())}",
        equity=float(capital_state["allocated_equity_usdt"]),
        entry_price=float(live["current_open"]),
        margin_pct=config.margin_pct,
        risk_pct=config.risk_pct,
        require_stop_loss=True,
    )

    position_state = {
        "strategy_id": STRATEGY_ID,
        "symbol": symbol,
        "side": str(opposite_signal["side"]),
        "timeframe": ENTRY_TIMEFRAME,
        "entry_price": float(
            ((execution.get("order") or {}).get("average"))
            or ((execution.get("order") or {}).get("price"))
            or ((execution.get("plan") or {}).get("entry_price"))
            or float(live["current_open"])
        ),
        "contracts": float(((execution.get("plan") or {}).get("qty")) or 0.0),
        "stop_loss_price": float(opposite_signal["stop_loss_price"]),
        "take_profit_price": float(opposite_signal["take_profit_price"]),
        "atr_value": float(opposite_signal["atr_value"]),
        "rr_ratio": float(opposite_signal.get("rr_ratio") or 3.0),
        "win_rate": float(opposite_signal.get("win_rate") or 0.0),
        "entry_timestamp_ms": float(opposite_signal["entry_timestamp_ms"]),
        "capital_state": dict(capital_state),
        "scan_candidate": dict(opposite_signal),
        "execution": {
            "plan": execution.get("plan"),
            "order": execution.get("order"),
            "protection": execution.get("protection"),
        },
        "reversed_from_side": old_side,
        "opened_at_ms": int(time.time() * 1000),
        "last_live_snapshot": dict(live),
    }
    position_state = set_strategy_active_position(STRATEGY_ID, position_state)
    return {
        "status": "position_reversed",
        "symbol": symbol,
        "old_side": old_side,
        "new_side": str(opposite_signal["side"]),
        "close_result": close_result,
        "closed_trade": closed_trade,
        "execution": execution,
        "position": position_state,
        "live": live,
    }


def manage_active_position(exchange, config: MeanReversionAtrStrategyConfig) -> Dict[str, Any]:
    sync_result = sync_strategy_position_state(exchange=exchange)
    if sync_result.get("status") != "active_position_synced":
        return sync_result

    active = dict(sync_result["position"])
    symbol = str(active["symbol"])
    side = str(active["side"])
    qty = float(active["contracts"])
    live = _compute_live_snapshot(exchange=exchange, symbol=symbol, config=config)
    if live.get("status") != "ok":
        return {"status": "live_snapshot_error", "symbol": symbol, "side": side, "live": live}

    opposite_signal = dict(live.get("opposite_signal") or {})
    if opposite_signal and str(opposite_signal.get("side") or "") != side:
        return _reverse_position(
            exchange=exchange,
            active=active,
            opposite_signal=opposite_signal,
            config=config,
            live=live,
        )

    stop_price = float(active.get("stop_loss_price") or 0.0)
    take_price = float(active.get("take_profit_price") or 0.0)
    if side == "buy":
        stop_hit = float(live["current_low"]) <= stop_price
        take_hit = float(live["current_high"]) >= take_price
    else:
        stop_hit = float(live["current_high"]) >= stop_price
        take_hit = float(live["current_low"]) <= take_price

    if stop_hit or take_hit:
        reason = "stop_loss_hit" if stop_hit else "take_profit_hit"
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
                "exit_reason": reason,
                "close_result": close_result,
                "live_snapshot": live,
            },
        )
        return {
            "status": "position_closed_by_strategy",
            "symbol": symbol,
            "side": side,
            "reason": reason,
            "close_result": close_result,
            "cleared": cleared,
            "live": live,
        }

    active["last_live_snapshot"] = dict(live)
    active["last_management_at_ms"] = int(time.time() * 1000)
    set_strategy_active_position(STRATEGY_ID, active)
    return {
        "status": "active_position_unchanged",
        "symbol": symbol,
        "side": side,
        "stop_loss_price": stop_price,
        "take_profit_price": take_price,
        "live": live,
    }


def open_new_position(exchange, config: MeanReversionAtrStrategyConfig) -> Dict[str, Any]:
    active = get_strategy_active_position(STRATEGY_ID)
    if active:
        return {"status": "blocked_active_position", "position": active}

    capital_state = get_per_strategy_allocated_equity(exchange=exchange)
    scan_result = scan_mean_reversion_atr_candidates(
        exchange=exchange,
        limit=config.universe_limit,
        two_h_candles=config.two_h_candles,
        daily_candles=config.daily_candles,
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
        "take_profit_price": float(candidate["take_profit_price"]),
        "atr_value": float(candidate["atr_value"]),
        "rr_ratio": float(candidate["rr_ratio"]),
        "win_rate": float(candidate["win_rate"]),
        "entry_timestamp_ms": float(candidate["entry_timestamp_ms"]),
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


def run_cycle(exchange=None, config: Optional[MeanReversionAtrStrategyConfig] = None) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or MeanReversionAtrStrategyConfig()

    management = manage_active_position(exchange=exchange, config=config)
    if management.get("status") in ("active_position_unchanged", "position_reversed"):
        return {"phase": "manage", "result": management}

    opening = open_new_position(exchange=exchange, config=config)
    return {"phase": "open", "result": opening, "precheck": management}


if __name__ == "__main__":
    result = run_cycle()
    print(result)
