from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from indicator_utils import ohlcv_to_series, rolling_highest, rolling_lowest, sma
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import (
    get_strategy_active_position,
    get_strategy_pending_entry,
    has_strategy_active_position,
    has_strategy_pending_entry,
)

STRATEGY_ID = "ma_breakout_4h_v1"
TIMEFRAME = "4h"
SHORT_MA_LENGTH = 2
LONG_MA_LENGTH = 30
BREAKOUT_LOOKBACK = 16
MIN_CANDLES = 80


@dataclass
class MovingAverageBreakoutCandidate:
    symbol: str
    side: str
    timeframe: str
    trigger_price: float
    entry_price: float
    stop_loss_price: float
    exit_trigger_price: float
    current_price: float
    current_high: float
    current_low: float
    current_bar_timestamp_ms: float
    short_ma_current: float
    long_ma_current: float
    rr_ratio: float
    win_rate: float
    rank_score: float
    trigger_state: str
    status: str
    notes: List[str]
    assumption_flags: List[str]
    indicator_snapshot: Dict[str, Any]
    learning_snapshot: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def register_strategy_spec() -> None:
    upsert_strategy(
        StrategySpec(
            strategy_id=STRATEGY_ID,
            name="4H Moving Average Breakout",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="top volume USDT swap symbols",
            scan_interval_sec=20,
            required_data=[
                StrategyDataRequirement(
                    key="ohlcv_4h",
                    source="exchange.fetch_ohlcv",
                    timeframe="4h",
                    lookback=MIN_CANDLES,
                    note="Need the current partial 4H bar plus prior candles for MA2/MA30 and 16-bar breakout levels.",
                ),
                StrategyDataRequirement(
                    key="ticker_last",
                    source="exchange.fetch_ticker",
                    timeframe="realtime",
                    lookback=1,
                    note="Used to approximate live stop-entry breakout checks on the current unfinished bar.",
                ),
            ],
            decision_inputs=[
                "potential long environment when current MA2 < current MA30",
                "potential short environment when current MA2 > current MA30",
                "breakout trigger from highest/lowest of the prior 16 completed candles relative to the current unfinished candle",
                "pending entry cancel when the MA environment invalidates before breakout",
                "post-entry exit from the opposite 16-bar extreme only",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "breakout selection quality",
                "open-ended reward proxy quality",
            ],
            tags=["ma", "breakout", "4h", "pending-entry", "trend"],
            note="Implements a local pending stop-entry workflow. Real exchange stop-entry can be approximated with polling, but exact tick-order replay is not available from OHLCV snapshots alone.",
        )
    )


register_strategy_spec()


def _build_assumption_flags() -> List[str]:
    return [
        "MA2 and MA30 are reconstructed from exchange OHLCV close values, not TradingView broker-specific candles.",
        "The strategy uses the current unfinished 4H bar for breakout monitoring, so it is executable but still limited by polling frequency rather than exact tick replay.",
        "Pending entry trigger is fixed when the setup is armed; it is canceled only if the MA invalidation rule appears before breakout.",
        "If the same live bar appears to both invalidate the MA condition and touch the pending trigger, breakout is given priority so the system does not retroactively cancel a setup that likely already filled intrabar.",
        "Because this strategy has no fixed TP, ranking uses a risk-efficiency proxy based on initial stop distance rather than a literal fixed take-profit RR.",
    ]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _risk_efficiency_proxy(entry_price: float, stop_loss_price: float) -> float:
    entry = float(entry_price)
    risk = abs(entry - float(stop_loss_price))
    if entry <= 0 or risk <= 0:
        return 0.0
    return 1.0 / (risk / entry)


def build_live_snapshot(
    symbol: str,
    ohlcv: List[List[float]],
    current_price: Optional[float] = None,
) -> Dict[str, Any]:
    if len(ohlcv) < MIN_CANDLES:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": [f"need at least {MIN_CANDLES} candles"],
            "assumption_flags": _build_assumption_flags(),
        }

    series = ohlcv_to_series(ohlcv)
    timestamps = series["timestamp"]
    opens = series["open"]
    highs = series["high"]
    lows = series["low"]
    closes = series["close"]
    current_index = len(closes) - 1
    prev_index = current_index - 1
    if prev_index < 0:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["not enough candles"],
            "assumption_flags": _build_assumption_flags(),
        }

    live_closes = list(closes)
    live_current_price = _safe_float(current_price, closes[current_index])
    if live_current_price > 0:
        live_closes[current_index] = float(live_current_price)

    ma_short = sma(live_closes, SHORT_MA_LENGTH)
    ma_long = sma(live_closes, LONG_MA_LENGTH)
    prior_highest = rolling_highest(highs, BREAKOUT_LOOKBACK)
    prior_lowest = rolling_lowest(lows, BREAKOUT_LOOKBACK)

    short_ma_current = ma_short[current_index]
    long_ma_current = ma_long[current_index]
    trigger_buy = prior_highest[prev_index]
    trigger_sell = prior_lowest[prev_index]
    exit_long = prior_lowest[prev_index]
    exit_short = prior_highest[prev_index]

    if None in (short_ma_current, long_ma_current, trigger_buy, trigger_sell, exit_long, exit_short):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    current_last = _safe_float(current_price, live_closes[current_index])
    if current_last <= 0:
        current_last = float(live_closes[current_index])
    current_high = max(float(highs[current_index]), current_last)
    current_low = min(float(lows[current_index]), current_last)

    environment = "neutral"
    if float(short_ma_current) < float(long_ma_current):
        environment = "long"
    elif float(short_ma_current) > float(long_ma_current):
        environment = "short"

    long_trigger_hit = current_high >= float(trigger_buy)
    short_trigger_hit = current_low <= float(trigger_sell)

    return {
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "status": "ok",
        "environment": environment,
        "current_bar_timestamp_ms": float(timestamps[current_index]),
        "current_bar_open": float(opens[current_index]),
        "current_price": round(float(current_last), 8),
        "current_high": round(float(current_high), 8),
        "current_low": round(float(current_low), 8),
        "short_ma_current": round(float(short_ma_current), 8),
        "long_ma_current": round(float(long_ma_current), 8),
        "trigger_buy_price": round(float(trigger_buy), 8),
        "trigger_sell_price": round(float(trigger_sell), 8),
        "exit_long_price": round(float(exit_long), 8),
        "exit_short_price": round(float(exit_short), 8),
        "long_trigger_hit": bool(long_trigger_hit),
        "short_trigger_hit": bool(short_trigger_hit),
        "indicator_snapshot": {
            "current_bar_timestamp_ms": float(timestamps[current_index]),
            "current_price": round(float(current_last), 8),
            "current_high": round(float(current_high), 8),
            "current_low": round(float(current_low), 8),
            "short_ma_current": round(float(short_ma_current), 8),
            "long_ma_current": round(float(long_ma_current), 8),
            "trigger_buy_price": round(float(trigger_buy), 8),
            "trigger_sell_price": round(float(trigger_sell), 8),
            "exit_long_price": round(float(exit_long), 8),
            "exit_short_price": round(float(exit_short), 8),
        },
        "assumption_flags": _build_assumption_flags(),
    }


def evaluate_symbol_for_entry(
    symbol: str,
    ohlcv: List[List[float]],
    current_price: Optional[float] = None,
) -> Dict[str, Any]:
    live = build_live_snapshot(symbol=symbol, ohlcv=ohlcv, current_price=current_price)
    if live.get("status") != "ok":
        return live

    environment = str(live["environment"])
    if environment == "neutral":
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "no_signal",
            "notes": ["MA2 equals MA30, so there is no valid long or short preparation zone"],
            "assumption_flags": list(live["assumption_flags"]),
            "indicator_snapshot": dict(live["indicator_snapshot"]),
        }

    side = "buy" if environment == "long" else "sell"
    trigger_price = float(live["trigger_buy_price"] if side == "buy" else live["trigger_sell_price"])
    stop_loss_price = float(live["exit_long_price"] if side == "buy" else live["exit_short_price"])
    rr_ratio = _risk_efficiency_proxy(entry_price=trigger_price, stop_loss_price=stop_loss_price)
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)
    trigger_hit = bool(live["long_trigger_hit"] if side == "buy" else live["short_trigger_hit"])

    candidate = MovingAverageBreakoutCandidate(
        symbol=symbol,
        side=side,
        timeframe=TIMEFRAME,
        trigger_price=round(float(trigger_price), 8),
        entry_price=round(float(trigger_price), 8),
        stop_loss_price=round(float(stop_loss_price), 8),
        exit_trigger_price=round(float(stop_loss_price), 8),
        current_price=round(float(live["current_price"]), 8),
        current_high=round(float(live["current_high"]), 8),
        current_low=round(float(live["current_low"]), 8),
        current_bar_timestamp_ms=float(live["current_bar_timestamp_ms"]),
        short_ma_current=round(float(live["short_ma_current"]), 8),
        long_ma_current=round(float(live["long_ma_current"]), 8),
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        trigger_state="triggered_now" if trigger_hit else "armed",
        status="candidate",
        notes=[
            "Current 4H unfinished candle defines the live environment and breakout trigger.",
            "The trigger price is the prior 16 completed candles breakout level at setup time.",
            "If selected but not yet triggered, this setup should be stored as a pending stop-entry and monitored instead of scanning the whole market again.",
            "After entry, the MA condition is ignored and only the opposite 16-bar breakout level controls exit.",
        ],
        assumption_flags=list(live["assumption_flags"]),
        indicator_snapshot=dict(live["indicator_snapshot"]),
        learning_snapshot=dict(learning_stats),
    )
    return candidate.to_dict()


def rank_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    only_candidates = [item for item in candidates if item.get("status") == "candidate"]
    return sorted(
        only_candidates,
        key=lambda item: (
            1 if item.get("trigger_state") == "triggered_now" else 0,
            float(item.get("rr_ratio", 0.0) or 0.0),
            float(item.get("win_rate", 0.0) or 0.0),
            float(item.get("rank_score", 0.0) or 0.0),
        ),
        reverse=True,
    )


def apply_strategy_position_lock(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active_position = get_strategy_active_position(STRATEGY_ID) or {}
    pending_entry = get_strategy_pending_entry(STRATEGY_ID) or {}
    has_lock = has_strategy_active_position(STRATEGY_ID) or has_strategy_pending_entry(STRATEGY_ID)
    if not has_lock:
        return candidates

    blocked: List[Dict[str, Any]] = []
    for item in candidates:
        copied = dict(item)
        if copied.get("status") == "candidate":
            notes = list(copied.get("notes") or [])
            if active_position:
                notes.append("strategy already has an active position, so this new setup is blocked")
                copied["active_position"] = dict(active_position)
                copied["status"] = "blocked_active_position"
            elif pending_entry:
                notes.append("strategy already has a pending entry, so this new setup is blocked")
                copied["pending_entry"] = dict(pending_entry)
                copied["status"] = "blocked_pending_entry"
            copied["notes"] = notes
        blocked.append(copied)
    return blocked
