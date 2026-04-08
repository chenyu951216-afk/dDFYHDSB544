from typing import Any, Dict, List, Optional

from okx_force_order import fetch_total_equity_usdt
from strategy_ai_generated_meta import refresh_strategy_spec as refresh_ai_generated_strategy_spec
from strategy_registry import list_enabled_strategies
from strategy_runtime_state import list_active_symbols

# Ensure known strategies register themselves before we count strategy slots.
import strategy_ai_generated_meta  # noqa: F401
import strategy_trend_hma_std  # noqa: F401
import strategy_larry_breakout_cmo  # noqa: F401
import strategy_bollinger_width_4h  # noqa: F401
import strategy_ma_breakout_4h  # noqa: F401
import strategy_burst_sma_channel_1h  # noqa: F401
import strategy_naked_k_reversal_1h  # noqa: F401
import strategy_mean_reversion_atr_2h_daily  # noqa: F401
import strategy_dual_sma_pullback_2h  # noqa: F401


def get_strategy_slot_count() -> int:
    refresh_ai_generated_strategy_spec()
    count = len(list_enabled_strategies())
    return max(int(count or 0), 1)


def get_per_strategy_allocated_equity(exchange) -> Dict[str, float]:
    total_equity = float(fetch_total_equity_usdt(exchange) or 0.0)
    slots = get_strategy_slot_count()
    allocated = total_equity / float(slots) if slots > 0 else total_equity
    return {
        "total_equity_usdt": round(total_equity, 8),
        "strategy_slot_count": slots,
        "allocated_equity_usdt": round(allocated, 8),
    }


def filter_candidates_by_symbol_lock(
    strategy_id: str,
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    active_symbols = list_active_symbols(exclude_strategy_id=strategy_id)
    if not active_symbols:
        return candidates

    filtered: List[Dict[str, Any]] = []
    for item in candidates:
        copied = dict(item)
        symbol = str(copied.get("symbol") or "").strip()
        blocker = active_symbols.get(symbol)
        if blocker and copied.get("status") == "candidate":
            notes = list(copied.get("notes") or [])
            notes.append(f"symbol is already held by strategy {blocker}")
            copied["status"] = "blocked_symbol_lock"
            copied["notes"] = notes
            copied["blocked_by_strategy"] = blocker
        filtered.append(copied)
    return filtered


def is_symbol_locked(symbol: str, strategy_id: Optional[str] = None) -> bool:
    active_symbols = list_active_symbols(exclude_strategy_id=strategy_id)
    return str(symbol or "").strip() in active_symbols
