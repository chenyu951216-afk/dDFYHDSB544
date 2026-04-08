import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from indicator_utils import chande_momentum_oscillator, hlc3, rolling_highest, rolling_lowest, ohlcv_to_series
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
from okx_scanner import scan_larry_breakout_candidates
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_larry_breakout_cmo import (
    DEFAULT_ADAPTIVE,
    DEFAULT_LENGTH,
    DEFAULT_MOMENTUM_LENGTH,
    DEFAULT_SCALING_FACTOR,
    STRATEGY_ID,
)
from strategy_runtime_state import (
    close_strategy_trade,
    get_strategy_active_position,
    set_strategy_active_position,
)


@dataclass
class LarryStrategyConfig:
    leverage: int = 10
    td_mode: str = "cross"
    margin_ccy: str = "USDT"
    margin_pct: float = DEFAULT_MARGIN_PCT
    risk_pct: float = DEFAULT_RISK_PCT
    universe_limit: int = 70
    candles: int = 140
    scan_sleep_sec: float = 0.2
    protection_verify_wait_sec: float = 1.0
    length: int = DEFAULT_LENGTH
    momentum_length: int = DEFAULT_MOMENTUM_LENGTH
    adaptive: float = DEFAULT_ADAPTIVE
    scaling_factor: float = DEFAULT_SCALING_FACTOR


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _price_tick_size(market: Dict[str, Any], reference_price: float) -> float:
    info = market.get("info") or {}
    raw_tick = info.get("tickSz") if isinstance(info, dict) else None
    if raw_tick is not None:
        tick = _safe_float(raw_tick)
        if tick > 0:
            return tick
    precision = (market.get("precision") or {}).get("price")
    if precision is not None:
        try:
            precision_value = int(precision)
            if precision_value >= 0:
                return 10 ** (-precision_value)
        except Exception:
            pass
    return max(abs(float(reference_price)) * 0.000001, 1e-8)


def _compute_live_line_snapshot(exchange, symbol: str, timeframe: str, config: LarryStrategyConfig) -> Dict[str, Any]:
    market = exchange.load_markets().get(symbol) or {}
    ticker = exchange.fetch_ticker(symbol)
    current_price = _safe_float(ticker.get("last"))
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=config.candles)
    series = ohlcv_to_series(ohlcv)
    highs = series["high"]
    lows = series["low"]
    closes = series["close"]
    timestamps = series["timestamp"]
    current_index = len(closes) - 1
    prev_index = len(closes) - 2

    tick_size = _price_tick_size(market=market, reference_price=current_price or closes[current_index])
    typical = hlc3(highs, lows, closes)
    ph = [2.0 * t - float(low) for t, low in zip(typical, lows)]
    pl = [2.0 * t - float(high) for t, high in zip(typical, highs)]
    green = rolling_highest(ph, config.length)
    red = rolling_lowest(pl, config.length)
    green_current = float(green[current_index] or 0) + tick_size
    red_current = float(red[current_index] or 0) - tick_size

    cmo = chande_momentum_oscillator(closes, config.momentum_length)
    cmo_closed = float(cmo[prev_index] or 0.0)

    current_open = float(series["open"][current_index])
    current_high = float(max(highs[current_index], current_price))
    current_low = float(min(lows[current_index], current_price))

    return {
        "current_bar_timestamp_ms": float(timestamps[current_index]),
        "previous_bar_timestamp_ms": float(timestamps[prev_index]),
        "current_open": current_open,
        "current_high": current_high,
        "current_low": current_low,
        "current_last": float(current_price or closes[current_index]),
        "green_current": green_current,
        "red_current": red_current,
        "cmo_closed": cmo_closed,
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


def _line_touch_exit(active: Dict[str, Any], live: Dict[str, Any]) -> Optional[str]:
    side = str(active.get("side") or "")
    if side == "buy":
        if float(live["current_low"]) <= float(live["red_current"]):
            return "red_line_touch_exit"
    else:
        if float(live["current_high"]) >= float(live["green_current"]):
            return "green_line_touch_exit"
    return None


def _momentum_exit(active: Dict[str, Any], live: Dict[str, Any]) -> Optional[str]:
    side = str(active.get("side") or "")
    closed_cmo = float(live["cmo_closed"])
    if side == "buy" and closed_cmo < 0:
        return "cmo_below_zero_next_open_exit"
    if side == "sell" and closed_cmo > 0:
        return "cmo_above_zero_next_open_exit"
    return None


def manage_active_position(exchange, config: LarryStrategyConfig) -> Dict[str, Any]:
    sync_result = sync_strategy_position_state(exchange=exchange)
    if sync_result.get("status") != "active_position_synced":
        return sync_result

    active = dict(sync_result["position"])
    symbol = str(active["symbol"])
    side = str(active["side"])
    timeframe = str(active["timeframe"])
    qty = float(active["contracts"])
    live = _compute_live_line_snapshot(exchange=exchange, symbol=symbol, timeframe=timeframe, config=config)

    momentum_reason = None
    if float(live["current_bar_timestamp_ms"]) > float(active.get("last_momentum_checked_bar_timestamp_ms", 0) or 0):
        momentum_reason = _momentum_exit(active=active, live=live)

    line_reason = _line_touch_exit(active=active, live=live)
    if line_reason or momentum_reason:
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
                "exit_reason": line_reason or momentum_reason,
                "close_result": close_result,
                "live_snapshot": live,
            },
        )
        return {
            "status": "position_closed_by_strategy",
            "symbol": symbol,
            "side": side,
            "reason": line_reason or momentum_reason,
            "close_result": close_result,
            "cleared": cleared,
        }

    previous_stop = float(active.get("stop_loss_price") or 0)
    target_stop = float(live["red_current"]) if side == "buy" else float(live["green_current"])
    if side == "buy":
        target_stop = max(previous_stop, target_stop)
    else:
        target_stop = min(previous_stop, target_stop) if previous_stop > 0 else target_stop

    should_update_stop = abs(target_stop - previous_stop) > max(abs(target_stop) * 0.0000001, 1e-8)
    if should_update_stop:
        protection = replace_stop_loss_only(
            exchange=exchange,
            symbol=symbol,
            side=side,
            qty=qty,
            sl_price=target_stop,
            td_mode=config.td_mode,
            verify_wait_sec=config.protection_verify_wait_sec,
        )
        active["stop_loss_price"] = target_stop
        active["last_live_lines"] = dict(live)
        active["last_momentum_checked_bar_timestamp_ms"] = float(live["current_bar_timestamp_ms"])
        active["last_protection_sync"] = dict(protection)
        active["last_management_at_ms"] = int(time.time() * 1000)
        set_strategy_active_position(STRATEGY_ID, active)
        return {
            "status": "active_position_managed",
            "symbol": symbol,
            "side": side,
            "updated_stop_loss": target_stop,
            "previous_stop_loss": previous_stop,
            "live": live,
            "protection": protection,
        }

    active["last_live_lines"] = dict(live)
    active["last_momentum_checked_bar_timestamp_ms"] = float(live["current_bar_timestamp_ms"])
    active["last_management_at_ms"] = int(time.time() * 1000)
    set_strategy_active_position(STRATEGY_ID, active)
    return {
        "status": "active_position_unchanged",
        "symbol": symbol,
        "side": side,
        "stop_loss_price": previous_stop,
        "live": live,
    }


def open_new_position(exchange, config: LarryStrategyConfig) -> Dict[str, Any]:
    active = get_strategy_active_position(STRATEGY_ID)
    if active:
        return {"status": "blocked_active_position", "position": active}

    capital_state = get_per_strategy_allocated_equity(exchange=exchange)

    scan_result = scan_larry_breakout_candidates(
        exchange=exchange,
        limit=config.universe_limit,
        candles=config.candles,
        sleep_sec=config.scan_sleep_sec,
        length=config.length,
        momentum_length=config.momentum_length,
        adaptive=config.adaptive,
        scaling_factor=config.scaling_factor,
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
        "trigger_price": float(candidate["trigger_price"]),
        "rr_ratio": float(candidate["rr_ratio"]),
        "win_rate": float(candidate["win_rate"]),
        "length": config.length,
        "momentum_length": config.momentum_length,
        "adaptive": config.adaptive,
        "scaling_factor": config.scaling_factor,
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


def run_cycle(exchange=None, config: Optional[LarryStrategyConfig] = None) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or LarryStrategyConfig()

    management = manage_active_position(exchange=exchange, config=config)
    if management.get("status") in ("active_position_managed", "active_position_unchanged"):
        return {"phase": "manage", "result": management}

    opening = open_new_position(exchange=exchange, config=config)
    return {"phase": "open", "result": opening, "precheck": management}


if __name__ == "__main__":
    result = run_cycle()
    print(result)
