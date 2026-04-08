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
from okx_scanner import scan_ma_breakout_candidates
from strategy_ma_breakout_4h import STRATEGY_ID, TIMEFRAME, build_live_snapshot
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_runtime_state import (
    close_strategy_trade,
    clear_strategy_pending_entry,
    get_strategy_active_position,
    get_strategy_pending_entry,
    set_strategy_active_position,
    set_strategy_pending_entry,
)


@dataclass
class MABreakoutStrategyConfig:
    leverage: int = 10
    td_mode: str = "cross"
    margin_ccy: str = "USDT"
    margin_pct: float = DEFAULT_MARGIN_PCT
    risk_pct: float = DEFAULT_RISK_PCT
    universe_limit: int = 70
    candles: int = 120
    scan_sleep_sec: float = 0.2
    protection_verify_wait_sec: float = 1.0


def _live_with_ticker(exchange, symbol: str, candles: int) -> Dict[str, Any]:
    ticker = exchange.fetch_ticker(symbol)
    current_price = float(ticker.get("last") or 0.0)
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=candles)
    return build_live_snapshot(symbol=symbol, ohlcv=ohlcv, current_price=current_price)


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


def manage_active_position(exchange, config: MABreakoutStrategyConfig) -> Dict[str, Any]:
    sync_result = sync_strategy_position_state(exchange=exchange)
    if sync_result.get("status") != "active_position_synced":
        return sync_result

    active = dict(sync_result["position"])
    symbol = str(active["symbol"])
    side = str(active["side"])
    qty = float(active["contracts"])
    live = _live_with_ticker(exchange=exchange, symbol=symbol, candles=config.candles)
    if live.get("status") != "ok":
        return {
            "status": "live_snapshot_error",
            "symbol": symbol,
            "side": side,
            "live": live,
        }

    stop_price = float(live["exit_long_price"] if side == "buy" else live["exit_short_price"])
    if side == "buy":
        stop_hit = float(live["current_low"]) <= stop_price
    else:
        stop_hit = float(live["current_high"]) >= stop_price

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
                "exit_reason": "reverse_16_bar_breakout_exit",
                "close_result": close_result,
                "live_snapshot": live,
            },
        )
        return {
            "status": "position_closed_by_strategy",
            "symbol": symbol,
            "side": side,
            "reason": "reverse_16_bar_breakout_exit",
            "close_result": close_result,
            "cleared": cleared,
            "live": live,
        }

    previous_stop = float(active.get("stop_loss_price") or 0.0)
    if abs(previous_stop - stop_price) > max(abs(stop_price) * 0.0000001, 1e-8):
        protection = replace_stop_loss_only(
            exchange=exchange,
            symbol=symbol,
            side=side,
            qty=qty,
            sl_price=stop_price,
            td_mode=config.td_mode,
            verify_wait_sec=config.protection_verify_wait_sec,
        )
        active["stop_loss_price"] = stop_price
        active["exit_trigger_price"] = stop_price
        active["last_live_snapshot"] = dict(live)
        active["last_management_at_ms"] = int(time.time() * 1000)
        active["last_protection_sync"] = dict(protection)
        set_strategy_active_position(STRATEGY_ID, active)
        return {
            "status": "active_position_managed",
            "symbol": symbol,
            "side": side,
            "updated_stop_loss": stop_price,
            "live": live,
            "protection": protection,
        }

    active["exit_trigger_price"] = stop_price
    active["last_live_snapshot"] = dict(live)
    active["last_management_at_ms"] = int(time.time() * 1000)
    set_strategy_active_position(STRATEGY_ID, active)
    return {
        "status": "active_position_unchanged",
        "symbol": symbol,
        "side": side,
        "stop_loss_price": stop_price,
        "live": live,
    }


def manage_pending_entry(exchange, config: MABreakoutStrategyConfig) -> Dict[str, Any]:
    pending = get_strategy_pending_entry(STRATEGY_ID)
    if not pending:
        return {"status": "no_pending_entry"}

    symbol = str(pending.get("symbol") or "")
    side = str(pending.get("side") or "")
    trigger_price = float(pending.get("trigger_price") or 0.0)
    if not symbol or not side or trigger_price <= 0:
        cleared = clear_strategy_pending_entry(STRATEGY_ID)
        return {"status": "cleared_invalid_pending", "cleared": cleared}

    live = _live_with_ticker(exchange=exchange, symbol=symbol, candles=config.candles)
    if live.get("status") != "ok":
        return {"status": "pending_live_snapshot_error", "symbol": symbol, "side": side, "live": live}

    trigger_hit = (
        float(live["current_high"]) >= trigger_price
        if side == "buy"
        else float(live["current_low"]) <= trigger_price
    )
    invalidated = (
        float(live["short_ma_current"]) > float(live["long_ma_current"])
        if side == "buy"
        else float(live["short_ma_current"]) < float(live["long_ma_current"])
    )

    if trigger_hit:
        stop_loss_price = float(live["exit_long_price"] if side == "buy" else live["exit_short_price"])
        capital_state = get_per_strategy_allocated_equity(exchange=exchange)
        execution = force_open_with_sl_only(
            exchange=exchange,
            symbol=symbol,
            side=side,
            qty=None,
            stop_loss_price=stop_loss_price,
            leverage=config.leverage,
            td_mode=config.td_mode,
            margin_ccy=config.margin_ccy,
            verify_wait_sec=config.protection_verify_wait_sec,
            client_order_id=f"{STRATEGY_ID}-{int(time.time())}",
            equity=float(capital_state["allocated_equity_usdt"]),
            entry_price=trigger_price,
            margin_pct=config.margin_pct,
            risk_pct=config.risk_pct,
            require_stop_loss=True,
        )
        position_state = {
            "strategy_id": STRATEGY_ID,
            "symbol": symbol,
            "side": side,
            "timeframe": TIMEFRAME,
            "entry_price": float(
                ((execution.get("order") or {}).get("average"))
                or ((execution.get("order") or {}).get("price"))
                or ((execution.get("plan") or {}).get("entry_price"))
                or trigger_price
            ),
            "contracts": float(((execution.get("plan") or {}).get("qty")) or 0.0),
            "trigger_price": trigger_price,
            "stop_loss_price": stop_loss_price,
            "exit_trigger_price": stop_loss_price,
            "rr_ratio": float(pending.get("rr_ratio") or 0.0),
            "win_rate": float(pending.get("win_rate") or 0.0),
            "capital_state": dict(capital_state),
            "pending_snapshot": dict(pending),
            "execution": {
                "plan": execution.get("plan"),
                "order": execution.get("order"),
                "protection": execution.get("protection"),
            },
            "opened_at_ms": int(time.time() * 1000),
            "last_live_snapshot": dict(live),
        }
        position_state = set_strategy_active_position(STRATEGY_ID, position_state)
        cleared_pending = clear_strategy_pending_entry(STRATEGY_ID)
        return {
            "status": "opened_position_from_pending",
            "symbol": symbol,
            "side": side,
            "capital_state": capital_state,
            "execution": execution,
            "position": position_state,
            "cleared_pending": cleared_pending,
            "live": live,
        }

    if invalidated:
        cleared = clear_strategy_pending_entry(STRATEGY_ID)
        return {
            "status": "pending_entry_canceled",
            "symbol": symbol,
            "side": side,
            "reason": "ma_invalidation_before_breakout",
            "cleared": cleared,
            "live": live,
        }

    pending_updated = dict(pending)
    pending_updated["last_live_snapshot"] = dict(live)
    pending_updated["last_management_at_ms"] = int(time.time() * 1000)
    set_strategy_pending_entry(STRATEGY_ID, pending_updated)
    return {
        "status": "pending_entry_waiting",
        "symbol": symbol,
        "side": side,
        "trigger_price": trigger_price,
        "live": live,
        "pending": pending_updated,
    }


def open_new_position(exchange, config: MABreakoutStrategyConfig) -> Dict[str, Any]:
    active = get_strategy_active_position(STRATEGY_ID)
    if active:
        return {"status": "blocked_active_position", "position": active}

    pending = get_strategy_pending_entry(STRATEGY_ID)
    if pending:
        return {"status": "blocked_pending_entry", "pending": pending}

    scan_result = scan_ma_breakout_candidates(
        exchange=exchange,
        limit=config.universe_limit,
        candles=config.candles,
        sleep_sec=config.scan_sleep_sec,
    )
    candidate = scan_result.get("best_candidate")
    if not candidate:
        return {"status": "no_candidate", "scan": scan_result}

    if str(candidate.get("trigger_state") or "") == "triggered_now":
        pending_state = set_strategy_pending_entry(
            STRATEGY_ID,
            {
                **dict(candidate),
                "pending_created_at_ms": int(time.time() * 1000),
                "pending_reason": "selected_best_triggered_setup",
            },
        )
        return {
            "status": "best_candidate_already_triggered",
            "scan": scan_result,
            "pending": pending_state,
        }

    pending_state = {
        **dict(candidate),
        "pending_created_at_ms": int(time.time() * 1000),
        "pending_reason": "selected_best_armed_setup",
    }
    set_strategy_pending_entry(STRATEGY_ID, pending_state)
    return {
        "status": "pending_entry_created",
        "candidate": candidate,
        "scan": scan_result,
        "pending": pending_state,
    }


def run_cycle(exchange=None, config: Optional[MABreakoutStrategyConfig] = None) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or MABreakoutStrategyConfig()

    management = manage_active_position(exchange=exchange, config=config)
    if management.get("status") in ("active_position_managed", "active_position_unchanged"):
        return {"phase": "manage_active", "result": management}

    pending = manage_pending_entry(exchange=exchange, config=config)
    if pending.get("status") in (
        "opened_position_from_pending",
        "pending_entry_waiting",
        "pending_entry_canceled",
    ):
        return {"phase": "manage_pending", "result": pending, "precheck": management}

    opening = open_new_position(exchange=exchange, config=config)
    if opening.get("status") == "best_candidate_already_triggered":
        pending_followup = manage_pending_entry(exchange=exchange, config=config)
        return {
            "phase": "open_or_arm",
            "result": opening,
            "followup": pending_followup,
            "precheck": {"active": management, "pending": pending},
        }

    return {
        "phase": "open_or_arm",
        "result": opening,
        "precheck": {"active": management, "pending": pending},
    }


if __name__ == "__main__":
    result = run_cycle()
    print(result)
