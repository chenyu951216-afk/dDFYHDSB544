from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position
from indicator_utils import ohlcv_to_series

STRATEGY_ID = "naked_k_reversal_1h_v1"
TIMEFRAME = "1h"
STOP_LOSS_PCT = 0.06
PROFIT_ARM_PCT = 0.02
MIN_CANDLES = 120


@dataclass
class NakedKCandidate:
    symbol: str
    side: str
    timeframe: str
    key_candle_timestamp_ms: float
    entry_timestamp_ms: float
    entry_price: float
    stop_loss_price: float
    profit_arm_price: float
    rr_ratio: float
    win_rate: float
    rank_score: float
    yesterday_high: float
    yesterday_low: float
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
            name="1H Naked K Reversal",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="top volume USDT swap symbols",
            scan_interval_sec=60,
            required_data=[
                StrategyDataRequirement(
                    key="ohlcv_1h",
                    source="exchange.fetch_ohlcv",
                    timeframe="1h",
                    lookback=MIN_CANDLES,
                    note="Need hourly candles for previous-day levels, 3-bar engulfing, and 3-soldier exit logic.",
                ),
            ],
            decision_inputs=[
                "previous UTC day high/low levels",
                "3-bar bullish or bearish engulfing on closed candles",
                "6 percent fixed stop loss from entry",
                "2 percent profit activation threshold",
                "3 consecutive opposite-color candles for next-open profit exit",
                "opposite engulfing after day rollover for reversal handling",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "RR floor selection quality",
                "daily reversal timing quality",
            ],
            tags=["1h", "price-action", "engulfing", "naked-k", "reversal"],
            note="Uses closed-candle signal confirmation and next-open execution to avoid repaint and signal regret.",
        )
    )


register_strategy_spec()


def _build_assumption_flags() -> List[str]:
    return [
        "Previous-day high and low are built from UTC day boundaries because exchange hourly candles are UTC-aligned by default.",
        "The daily trigger uses candle wick extremes: long requires the signal bar low to break the previous day low, short requires the signal bar high to break the previous day high.",
        "Bullish engulfing is interpreted as close1 > high2 and bearish engulfing as close1 < low2, matching your 'completely above / completely below including the wick' wording.",
        "The 2 percent target is treated as a profit activation threshold, not a hard take-profit level. Actual profit exit happens on the next bar open after a confirmed 3-soldier reversal pattern.",
        "The reversal clause is applied symmetrically only after the position has crossed into a new UTC day, because your short-side wording was incomplete and I chose the safer consistent rule instead of inventing asymmetry.",
    ]


def _utc_day_key(timestamp_ms: float) -> str:
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _build_previous_day_levels(
    timestamps: List[float],
    highs: List[float],
    lows: List[float],
) -> Dict[str, List[Optional[float]]]:
    prev_day_highs: List[Optional[float]] = [None] * len(timestamps)
    prev_day_lows: List[Optional[float]] = [None] * len(timestamps)
    current_day: Optional[str] = None
    day_high = 0.0
    day_low = 0.0
    previous_summary: Optional[Dict[str, float]] = None

    for index, timestamp_ms in enumerate(timestamps):
        day_key = _utc_day_key(timestamp_ms)
        high_value = float(highs[index])
        low_value = float(lows[index])

        if current_day is None:
            current_day = day_key
            day_high = high_value
            day_low = low_value
        elif day_key != current_day:
            previous_summary = {
                "high": float(day_high),
                "low": float(day_low),
            }
            current_day = day_key
            day_high = high_value
            day_low = low_value
        else:
            day_high = max(day_high, high_value)
            day_low = min(day_low, low_value)

        if previous_summary is not None:
            prev_day_highs[index] = float(previous_summary["high"])
            prev_day_lows[index] = float(previous_summary["low"])

    return {
        "prev_day_high": prev_day_highs,
        "prev_day_low": prev_day_lows,
    }


def _is_bullish(open_price: float, close_price: float) -> bool:
    return float(close_price) > float(open_price)


def _is_bearish(open_price: float, close_price: float) -> bool:
    return float(close_price) < float(open_price)


def _evaluate_signal_at_index(
    series: Dict[str, List[float]],
    prev_day_levels: Dict[str, List[Optional[float]]],
    key_index: int,
) -> Optional[Dict[str, Any]]:
    if key_index < 2 or key_index + 1 >= len(series["open"]):
        return None

    opens = series["open"]
    highs = series["high"]
    lows = series["low"]
    closes = series["close"]
    timestamps = series["timestamp"]
    prev_day_high = prev_day_levels["prev_day_high"][key_index]
    prev_day_low = prev_day_levels["prev_day_low"][key_index]
    if prev_day_high is None or prev_day_low is None:
        return None

    open1, close1, high1, low1 = opens[key_index], closes[key_index], highs[key_index], lows[key_index]
    open2, close2, high2, low2 = opens[key_index - 1], closes[key_index - 1], highs[key_index - 1], lows[key_index - 1]
    open3, close3 = opens[key_index - 2], closes[key_index - 2]

    bullish_engulfing = (
        _is_bullish(open1, close1)
        and _is_bearish(open2, close2)
        and _is_bearish(open3, close3)
        and float(close1) > float(high2)
        and float(close2) < float(close3)
        and float(low1) < float(prev_day_low)
    )
    bearish_engulfing = (
        _is_bearish(open1, close1)
        and _is_bullish(open2, close2)
        and _is_bullish(open3, close3)
        and float(close1) < float(low2)
        and float(close2) > float(close3)
        and float(high1) > float(prev_day_high)
    )

    if bullish_engulfing and bearish_engulfing:
        return None
    if not bullish_engulfing and not bearish_engulfing:
        return None

    side = "buy" if bullish_engulfing else "sell"
    entry_price = float(opens[key_index + 1])
    stop_loss_price = (
        entry_price * (1.0 - STOP_LOSS_PCT)
        if side == "buy"
        else entry_price * (1.0 + STOP_LOSS_PCT)
    )
    profit_arm_price = (
        entry_price * (1.0 + PROFIT_ARM_PCT)
        if side == "buy"
        else entry_price * (1.0 - PROFIT_ARM_PCT)
    )
    return {
        "side": side,
        "key_candle_timestamp_ms": float(timestamps[key_index]),
        "entry_timestamp_ms": float(timestamps[key_index + 1]),
        "entry_price": round(float(entry_price), 8),
        "stop_loss_price": round(float(stop_loss_price), 8),
        "profit_arm_price": round(float(profit_arm_price), 8),
        "yesterday_high": round(float(prev_day_high), 8),
        "yesterday_low": round(float(prev_day_low), 8),
        "indicator_snapshot": {
            "key_open": round(float(open1), 8),
            "key_high": round(float(high1), 8),
            "key_low": round(float(low1), 8),
            "key_close": round(float(close1), 8),
            "bar2_high": round(float(high2), 8),
            "bar2_low": round(float(low2), 8),
            "bar2_close": round(float(close2), 8),
            "bar3_close": round(float(close3), 8),
            "yesterday_high": round(float(prev_day_high), 8),
            "yesterday_low": round(float(prev_day_low), 8),
        },
    }


def evaluate_symbol_for_entry(symbol: str, ohlcv: List[List[float]]) -> Dict[str, Any]:
    if len(ohlcv) < MIN_CANDLES:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": [f"need at least {MIN_CANDLES} candles"],
            "assumption_flags": _build_assumption_flags(),
        }

    series = ohlcv_to_series(ohlcv)
    prev_day_levels = _build_previous_day_levels(
        timestamps=series["timestamp"],
        highs=series["high"],
        lows=series["low"],
    )
    key_index = len(series["close"]) - 2
    signal = _evaluate_signal_at_index(series=series, prev_day_levels=prev_day_levels, key_index=key_index)
    if signal is None:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "no_signal",
            "notes": ["latest closed 1H candle did not form a valid naked-K reversal signal"],
            "assumption_flags": _build_assumption_flags(),
        }

    rr_ratio = round(PROFIT_ARM_PCT / STOP_LOSS_PCT, 6)
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = NakedKCandidate(
        symbol=symbol,
        side=str(signal["side"]),
        timeframe=TIMEFRAME,
        key_candle_timestamp_ms=float(signal["key_candle_timestamp_ms"]),
        entry_timestamp_ms=float(signal["entry_timestamp_ms"]),
        entry_price=float(signal["entry_price"]),
        stop_loss_price=float(signal["stop_loss_price"]),
        profit_arm_price=float(signal["profit_arm_price"]),
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        yesterday_high=float(signal["yesterday_high"]),
        yesterday_low=float(signal["yesterday_low"]),
        status="candidate",
        notes=[
            "Signal uses the latest fully closed 1H candle plus the two bars before it.",
            "Entry is fixed at the next 1H candle open and is not canceled afterward.",
            "Stop loss is fixed at 6 percent from entry.",
            "Profit exit only activates after price first reaches plus or minus 2 percent, then waits for the opposite 3-soldier pattern.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot=dict(signal["indicator_snapshot"]),
        learning_snapshot=dict(learning_stats),
    )
    return candidate.to_dict()


def build_live_management_snapshot(symbol: str, ohlcv: List[List[float]]) -> Dict[str, Any]:
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
    prev_closed_index = current_index - 1
    if prev_closed_index < 2:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["not enough candles for live management"],
            "assumption_flags": _build_assumption_flags(),
        }

    prev_day_levels = _build_previous_day_levels(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
    )
    opposite_signal = _evaluate_signal_at_index(
        series=series,
        prev_day_levels=prev_day_levels,
        key_index=prev_closed_index,
    )

    last_three_bearish = all(
        _is_bearish(opens[index], closes[index])
        for index in range(prev_closed_index - 2, prev_closed_index + 1)
    )
    last_three_bullish = all(
        _is_bullish(opens[index], closes[index])
        for index in range(prev_closed_index - 2, prev_closed_index + 1)
    )

    return {
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "status": "ok",
        "current_bar_timestamp_ms": float(timestamps[current_index]),
        "prev_closed_timestamp_ms": float(timestamps[prev_closed_index]),
        "current_open": float(opens[current_index]),
        "current_high": float(highs[current_index]),
        "current_low": float(lows[current_index]),
        "prev_close": float(closes[prev_closed_index]),
        "prev_closed_day_utc": _utc_day_key(timestamps[prev_closed_index]),
        "three_black_crows": bool(last_three_bearish),
        "three_white_soldiers": bool(last_three_bullish),
        "opposite_signal": dict(opposite_signal) if opposite_signal else None,
    }


def rank_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    only_candidates = [item for item in candidates if item.get("status") == "candidate"]
    return sorted(
        only_candidates,
        key=lambda item: (
            float(item.get("rr_ratio", 0.0) or 0.0),
            float(item.get("win_rate", 0.0) or 0.0),
            float(item.get("rank_score", 0.0) or 0.0),
        ),
        reverse=True,
    )


def apply_strategy_position_lock(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not has_strategy_active_position(STRATEGY_ID):
        return candidates

    active_position = get_strategy_active_position(STRATEGY_ID) or {}
    blocked: List[Dict[str, Any]] = []
    for item in candidates:
        copied = dict(item)
        if copied.get("status") == "candidate":
            notes = list(copied.get("notes") or [])
            notes.append("strategy already has an active position, so this new signal is blocked")
            copied["status"] = "blocked_active_position"
            copied["notes"] = notes
            copied["active_position"] = dict(active_position)
        blocked.append(copied)
    return blocked
