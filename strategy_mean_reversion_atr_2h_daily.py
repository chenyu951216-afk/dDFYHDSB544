from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from indicator_utils import atr, ohlcv_to_series
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position

STRATEGY_ID = "mean_reversion_atr_2h_daily_v1"
ENTRY_TIMEFRAME = "2h"
REFERENCE_TIMEFRAME = "1d"
ATR_LENGTH = 14
ATR_SMOOTHING = "RMA"
ATR_MULTIPLIER = 4.0
R_MULTIPLIER = 3.0
MIN_2H_CANDLES = 120
MIN_1D_CANDLES = 40


@dataclass
class MeanReversionAtrCandidate:
    symbol: str
    side: str
    timeframe: str
    key_candle_timestamp_ms: float
    entry_timestamp_ms: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    atr_value: float
    previous_day_high: float
    previous_day_low: float
    rr_ratio: float
    win_rate: float
    rank_score: float
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
            name="2H Daily Mean Reversion ATR",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="top volume USDT swap symbols",
            scan_interval_sec=60,
            required_data=[
                StrategyDataRequirement(
                    key="ohlcv_1d",
                    source="exchange.fetch_ohlcv",
                    timeframe="1d",
                    lookback=MIN_1D_CANDLES,
                    note="Need previous daily high and low as the large-timeframe reversal anchor.",
                ),
                StrategyDataRequirement(
                    key="ohlcv_2h",
                    source="exchange.fetch_ohlcv",
                    timeframe="2h",
                    lookback=MIN_2H_CANDLES,
                    note="Need 3-bar 2H reversal pattern and ATR(14) on the key candle.",
                ),
            ],
            decision_inputs=[
                "previous daily high and low from the completed prior day only",
                "three most recent closed 2H candles for reversal setup",
                "latest closed 2H close above previous bar high for long or below previous bar low for short",
                "ATR(14, RMA) on the key candle for stop distance",
                "3R fixed take profit and opposite-signal immediate reversal",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "RR realized vs expected",
                "cross-timeframe reversal timing quality",
            ],
            tags=["2h", "1d", "mean-reversion", "atr", "cross-timeframe"],
            note="Cross-timeframe reversal system using previous daily extremes and a 2H three-bar engulfing-style confirmation.",
        )
    )


register_strategy_spec()


def _build_assumption_flags() -> List[str]:
    return [
        "ATR is reconstructed from the provided TradingView ATR source with the default ATR(14) and RMA smoothing, because no alternate setting was requested.",
        "The large-timeframe reversal anchor uses the previous completed UTC day high and low only; the current daily candle is never used as the reference level.",
        "The touch condition is implemented as any of the latest three closed 2H candles having a wick or close that reaches the previous daily level.",
        "The engulfing confirmation is implemented as the latest closed 2H close above the previous bar high for long, or below the previous bar low for short, matching your wording more closely than a body-only engulfing.",
        "The opposite-signal reversal filter acts on the next 2H bar open approximation once the opposite signal is confirmed on closed candles.",
    ]


def _utc_day_key(timestamp_ms: float) -> str:
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _previous_day_key(timestamp_ms: float) -> str:
    dt = datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")


def _build_daily_level_map(daily_ohlcv: List[List[float]]) -> Dict[str, Dict[str, float]]:
    levels: Dict[str, Dict[str, float]] = {}
    for row in daily_ohlcv:
        if len(row) < 5:
            continue
        levels[_utc_day_key(row[0])] = {
            "high": float(row[2]),
            "low": float(row[3]),
        }
    return levels


def _risk_reward_ratio(entry_price: float, stop_loss_price: float, take_profit_price: float) -> float:
    risk = abs(float(entry_price) - float(stop_loss_price))
    reward = abs(float(take_profit_price) - float(entry_price))
    if risk <= 0:
        return 0.0
    return reward / risk


def _evaluate_signal_at_index(
    two_h_ohlcv: List[List[float]],
    daily_ohlcv: List[List[float]],
    key_index: int,
) -> Optional[Dict[str, Any]]:
    if len(two_h_ohlcv) < MIN_2H_CANDLES or len(daily_ohlcv) < MIN_1D_CANDLES:
        return None
    if key_index < 2 or key_index + 1 >= len(two_h_ohlcv):
        return None

    series = ohlcv_to_series(two_h_ohlcv)
    timestamps = series["timestamp"]
    opens = series["open"]
    highs = series["high"]
    lows = series["low"]
    closes = series["close"]
    atr_values = atr(highs=highs, lows=lows, closes=closes, length=ATR_LENGTH, smoothing=ATR_SMOOTHING)
    key_atr = atr_values[key_index]
    if key_atr is None:
        return None

    daily_levels = _build_daily_level_map(daily_ohlcv)
    previous_day_key = _previous_day_key(timestamps[key_index])
    previous_day = daily_levels.get(previous_day_key)
    if not previous_day:
        return None

    prev_day_high = float(previous_day["high"])
    prev_day_low = float(previous_day["low"])
    touch_long = any(
        float(lows[index]) <= prev_day_low or float(closes[index]) <= prev_day_low
        for index in range(key_index - 2, key_index + 1)
    )
    touch_short = any(
        float(highs[index]) >= prev_day_high or float(closes[index]) >= prev_day_high
        for index in range(key_index - 2, key_index + 1)
    )

    latest_close = float(closes[key_index])
    second_high = float(highs[key_index - 1])
    second_low = float(lows[key_index - 1])
    latest_low = float(lows[key_index])
    latest_high = float(highs[key_index])
    entry_price = float(opens[key_index + 1])

    long_signal = touch_long and latest_close > second_high
    short_signal = touch_short and latest_close < second_low
    if long_signal and short_signal:
        return None
    if not long_signal and not short_signal:
        return None

    side = "buy" if long_signal else "sell"
    if side == "buy":
        stop_loss_price = latest_low - (float(key_atr) * ATR_MULTIPLIER)
        risk_distance = entry_price - stop_loss_price
        take_profit_price = entry_price + (risk_distance * R_MULTIPLIER)
    else:
        stop_loss_price = latest_high + (float(key_atr) * ATR_MULTIPLIER)
        risk_distance = stop_loss_price - entry_price
        take_profit_price = entry_price - (risk_distance * R_MULTIPLIER)

    if risk_distance <= 0:
        return None

    return {
        "side": side,
        "key_candle_timestamp_ms": float(timestamps[key_index]),
        "entry_timestamp_ms": float(timestamps[key_index + 1]),
        "entry_price": round(float(entry_price), 8),
        "stop_loss_price": round(float(stop_loss_price), 8),
        "take_profit_price": round(float(take_profit_price), 8),
        "atr_value": round(float(key_atr), 8),
        "previous_day_high": round(float(prev_day_high), 8),
        "previous_day_low": round(float(prev_day_low), 8),
        "indicator_snapshot": {
            "previous_day_high": round(float(prev_day_high), 8),
            "previous_day_low": round(float(prev_day_low), 8),
            "latest_close": round(float(latest_close), 8),
            "second_high": round(float(second_high), 8),
            "second_low": round(float(second_low), 8),
            "latest_high": round(float(latest_high), 8),
            "latest_low": round(float(latest_low), 8),
            "atr_key": round(float(key_atr), 8),
            "touch_long": bool(touch_long),
            "touch_short": bool(touch_short),
        },
    }


def evaluate_symbol_for_entry(
    symbol: str,
    two_h_ohlcv: List[List[float]],
    daily_ohlcv: List[List[float]],
) -> Dict[str, Any]:
    signal = _evaluate_signal_at_index(
        two_h_ohlcv=two_h_ohlcv,
        daily_ohlcv=daily_ohlcv,
        key_index=len(two_h_ohlcv) - 2,
    )
    if signal is None:
        return {
            "symbol": symbol,
            "timeframe": ENTRY_TIMEFRAME,
            "status": "no_signal",
            "notes": ["latest closed 2H candle did not form a valid mean-reversion setup"],
            "assumption_flags": _build_assumption_flags(),
        }

    rr_ratio = _risk_reward_ratio(
        entry_price=float(signal["entry_price"]),
        stop_loss_price=float(signal["stop_loss_price"]),
        take_profit_price=float(signal["take_profit_price"]),
    )
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = MeanReversionAtrCandidate(
        symbol=symbol,
        side=str(signal["side"]),
        timeframe=ENTRY_TIMEFRAME,
        key_candle_timestamp_ms=float(signal["key_candle_timestamp_ms"]),
        entry_timestamp_ms=float(signal["entry_timestamp_ms"]),
        entry_price=float(signal["entry_price"]),
        stop_loss_price=float(signal["stop_loss_price"]),
        take_profit_price=float(signal["take_profit_price"]),
        atr_value=float(signal["atr_value"]),
        previous_day_high=float(signal["previous_day_high"]),
        previous_day_low=float(signal["previous_day_low"]),
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        status="candidate",
        notes=[
            "Signal uses the previous completed daily range as the reversal anchor.",
            "Entry is fixed at the next 2H candle open and is not canceled afterward.",
            "Stop loss is based on the key candle extreme plus or minus ATR * 4.",
            "Take profit is fixed at 3R unless an opposite signal appears first and forces a reverse.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot=dict(signal["indicator_snapshot"]),
        learning_snapshot=dict(learning_stats),
    )
    return candidate.to_dict()


def build_live_management_snapshot(
    symbol: str,
    two_h_ohlcv: List[List[float]],
    daily_ohlcv: List[List[float]],
) -> Dict[str, Any]:
    if len(two_h_ohlcv) < MIN_2H_CANDLES or len(daily_ohlcv) < MIN_1D_CANDLES:
        return {
            "symbol": symbol,
            "timeframe": ENTRY_TIMEFRAME,
            "status": "skip",
            "notes": ["not enough candles for live management"],
            "assumption_flags": _build_assumption_flags(),
        }

    series = ohlcv_to_series(two_h_ohlcv)
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
            "timeframe": ENTRY_TIMEFRAME,
            "status": "skip",
            "notes": ["not enough closed bars for signal management"],
            "assumption_flags": _build_assumption_flags(),
        }

    opposite_signal = _evaluate_signal_at_index(
        two_h_ohlcv=two_h_ohlcv,
        daily_ohlcv=daily_ohlcv,
        key_index=prev_closed_index,
    )

    return {
        "symbol": symbol,
        "timeframe": ENTRY_TIMEFRAME,
        "status": "ok",
        "current_bar_timestamp_ms": float(timestamps[current_index]),
        "prev_closed_timestamp_ms": float(timestamps[prev_closed_index]),
        "current_open": float(opens[current_index]),
        "current_high": float(highs[current_index]),
        "current_low": float(lows[current_index]),
        "current_close": float(closes[current_index]),
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
