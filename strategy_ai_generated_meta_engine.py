import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from okx_force_order import (
    DEFAULT_MARGIN_PCT,
    DEFAULT_RISK_PCT,
    cancel_protection_orders,
    create_okx_exchange,
    force_close_position,
    force_open_with_tp_sl,
    get_position_snapshot,
)
from learning_store import get_ai_strategy_profile, get_strategy_rollup
from okx_scanner import (
    scan_dual_sma_pullback_candidates,
    scan_mean_reversion_atr_candidates,
    scan_trend_hma_std_candidates,
)
from strategy_ai_generated_meta import STRATEGY_ID, refresh_strategy_spec
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_runtime_state import (
    close_strategy_trade,
    get_strategy_active_position,
    set_strategy_active_position,
)


@dataclass
class AIGeneratedMetaStrategyConfig:
    leverage: int = 10
    td_mode: str = "cross"
    margin_ccy: str = "USDT"
    margin_pct: float = DEFAULT_MARGIN_PCT
    risk_pct: float = DEFAULT_RISK_PCT
    universe_limit: int = 35
    scan_sleep_sec: float = 0.2
    protection_verify_wait_sec: float = 1.0


def _profile() -> Dict[str, Any]:
    refresh_strategy_spec()
    return dict(get_ai_strategy_profile() or {})


def _source_candidates(exchange, profile: Dict[str, Any], config: AIGeneratedMetaStrategyConfig) -> List[Dict[str, Any]]:
    source_ids = list(profile.get("source_strategy_ids") or [])
    candidates: List[Dict[str, Any]] = []
    for strategy_id in source_ids:
        if strategy_id == "trend_hma_std_4h_v1":
            scan = scan_trend_hma_std_candidates(exchange=exchange, limit=config.universe_limit, candles=120, sleep_sec=config.scan_sleep_sec)
            top = (scan.get("ranked_candidates") or [])[:2]
        elif strategy_id == "mean_reversion_atr_2h_daily_v1":
            scan = scan_mean_reversion_atr_candidates(exchange=exchange, limit=config.universe_limit, two_h_candles=160, daily_candles=60, sleep_sec=config.scan_sleep_sec)
            top = (scan.get("ranked_candidates") or [])[:2]
        elif strategy_id == "dual_sma_pullback_2h_v1":
            scan = scan_dual_sma_pullback_candidates(exchange=exchange, limit=config.universe_limit, candles=140, sleep_sec=config.scan_sleep_sec)
            top = (scan.get("ranked_candidates") or [])[:2]
        else:
            top = []
        for item in top:
            if item.get("status") != "candidate":
                continue
            if not item.get("take_profit_price") or not item.get("stop_loss_price"):
                continue
            copied = dict(item)
            copied["source_strategy_id"] = strategy_id
            candidates.append(copied)
    return candidates


def _rank_meta_candidates(candidates: List[Dict[str, Any]], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    source_weights = dict(profile.get("source_strategy_weights") or {})
    scoring_weights = dict(profile.get("scoring_weights") or {})
    rr_weight = float(scoring_weights.get("candidate_rr", 1.0))
    win_weight = float(scoring_weights.get("candidate_win_rate", 1.0))
    source_win_weight = float(scoring_weights.get("source_win_rate", 0.5))
    min_rr = float(profile.get("min_candidate_rr", 0.0))
    ranked: List[Dict[str, Any]] = []
    for item in candidates:
        rr_ratio = float(item.get("rr_ratio", 0.0) or 0.0)
        if rr_ratio < min_rr:
            continue
        source_id = str(item.get("source_strategy_id") or "")
        source_rollup = get_strategy_rollup(source_id)
        source_weight = float(source_weights.get(source_id, 0.0) or 0.0)
        source_win_rate = float(source_rollup.get("win_rate", 0.0) or 0.0)
        meta_score = (rr_ratio * rr_weight) + (float(item.get("win_rate", 0.0) or 0.0) * win_weight) + (source_win_rate * source_win_weight) + source_weight
        copied = dict(item)
        copied["meta_score"] = round(float(meta_score), 6)
        ranked.append(copied)
    return sorted(ranked, key=lambda item: float(item.get("meta_score", 0.0) or 0.0), reverse=True)


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
    updated["entry_price"] = exchange_position["entry_price"] if exchange_position["entry_price"] > 0 else active.get("entry_price")
    updated["mark_price"] = exchange_position["mark_price"]
    updated["exchange_leverage"] = exchange_position["leverage"]
    set_strategy_active_position(STRATEGY_ID, updated)
    return {"status": "active_position_synced", "position": updated}


def manage_active_position(exchange, config: AIGeneratedMetaStrategyConfig) -> Dict[str, Any]:
    sync_result = sync_strategy_position_state(exchange=exchange)
    if sync_result.get("status") != "active_position_synced":
        return sync_result
    active = dict(sync_result["position"])
    symbol = str(active["symbol"])
    side = str(active["side"])
    qty = float(active["contracts"])
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=str(active.get("timeframe") or "2h"), limit=3)
    current = ohlcv[-1]
    current_high = float(current[2])
    current_low = float(current[3])
    stop_price = float(active.get("stop_loss_price") or 0.0)
    take_price = float(active.get("take_profit_price") or 0.0)
    if side == "buy":
        stop_hit = current_low <= stop_price
        take_hit = current_high >= take_price
    else:
        stop_hit = current_high >= stop_price
        take_hit = current_low <= take_price
    if stop_hit or take_hit:
        reason = "stop_loss_hit" if stop_hit else "take_profit_hit"
        cancel_protection_orders(exchange=exchange, symbol=symbol, side=side, order_kind="all")
        close_result = force_close_position(exchange=exchange, symbol=symbol, side=side, qty=qty, td_mode=config.td_mode, margin_ccy=config.margin_ccy)
        cleared = close_strategy_trade(
            STRATEGY_ID,
            {
                "exit_reason": reason,
                "close_result": close_result,
                "live_snapshot": {
                    "current_high": current_high,
                    "current_low": current_low,
                    "timeframe": str(active.get("timeframe") or "2h"),
                },
            },
        )
        return {"status": "position_closed_by_strategy", "reason": reason, "close_result": close_result, "cleared": cleared}
    return {"status": "active_position_unchanged", "position": active}


def open_new_position(exchange, config: AIGeneratedMetaStrategyConfig) -> Dict[str, Any]:
    profile = _profile()
    if not profile or not bool(profile.get("enabled")):
        return {"status": "disabled", "profile": profile}
    if get_strategy_active_position(STRATEGY_ID):
        return {"status": "blocked_active_position"}
    capital_state = get_per_strategy_allocated_equity(exchange=exchange)
    candidates = _source_candidates(exchange=exchange, profile=profile, config=config)
    ranked = _rank_meta_candidates(candidates=candidates, profile=profile)
    candidate = ranked[0] if ranked else None
    if not candidate:
        return {"status": "no_candidate", "profile": profile}
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
        "source_strategy_id": candidate["source_strategy_id"],
        "symbol": candidate["symbol"],
        "side": candidate["side"],
        "timeframe": candidate["timeframe"],
        "entry_price": float(((execution.get("order") or {}).get("average")) or ((execution.get("order") or {}).get("price")) or ((execution.get("plan") or {}).get("entry_price")) or candidate["entry_price"]),
        "contracts": float(((execution.get("plan") or {}).get("qty")) or 0.0),
        "stop_loss_price": float(candidate["stop_loss_price"]),
        "take_profit_price": float(candidate["take_profit_price"]),
        "rr_ratio": float(candidate.get("rr_ratio") or 0.0),
        "win_rate": float(candidate.get("win_rate") or 0.0),
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
    return {"status": "opened_position", "candidate": candidate, "position": position_state, "profile": profile}


def run_cycle(exchange=None, config: Optional[AIGeneratedMetaStrategyConfig] = None) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or AIGeneratedMetaStrategyConfig()
    management = manage_active_position(exchange=exchange, config=config)
    if management.get("status") == "active_position_unchanged":
        return {"phase": "manage", "result": management}
    opening = open_new_position(exchange=exchange, config=config)
    return {"phase": "open", "result": opening, "precheck": management}
