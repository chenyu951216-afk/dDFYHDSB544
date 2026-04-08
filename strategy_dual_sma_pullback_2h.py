from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from indicator_utils import ohlcv_to_series, sma
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position

STRATEGY_ID = "dual_sma_pullback_2h_v1"
TIMEFRAME = "2h"
SHORT_SMA_LENGTH = 13
LONG_SMA_LENGTH = 59
THRESHOLD_PCT = 0.035
PROFIT_DISTANCE = 0.3
MIN_CANDLES = 120


@dataclass
class DualSmaCandidate:
    symbol: str
    side: str
    timeframe: str
    key_candle_timestamp_ms: float
    entry_timestamp_ms: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    short_sma_value: float
    long_sma_value: float
    threshold_pct: float
    profit_distance: float
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
            name="2H Dual SMA Pullback",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="top volume USDT swap symbols",
            scan_interval_sec=60,
            required_data=[
                StrategyDataRequirement(
                    key="ohlcv_2h",
                    source="exchange.fetch_ohlcv",
                    timeframe="2h",
                    lookback=MIN_CANDLES,
                    note="Need closed 2H candles for SMA13, SMA59, pullback signal, threshold filter, and next-bar entry.",
                ),
            ],
            decision_inputs=[
                "SMA13 > SMA59 for long-only trend, SMA13 < SMA59 for short-only trend",
                "closed candle pullback through the short SMA",
                "distance from close to long SMA greater than close times threshold percent",
                "stop loss fixed at the key candle long SMA",
                "take profit derived from the key candle close-to-long-SMA distance times profit distance",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "RR realized vs expected",
                "pullback quality after trend expansion",
            ],
            tags=["2h", "sma", "pullback", "trend", "threshold"],
            note="Closed-candle pullback strategy with next-open execution and fixed TP/SL from the signal candle.",
        )
    )


register_strategy_spec()


def _build_assumption_flags() -> List[str]:
    return [
        "Both moving averages use the standard TradingView SMA(close, length) formula from the provided SMA.txt source.",
        "Trend, pullback, and threshold are evaluated only on the latest fully closed 2H candle.",
        "Entry is fixed at the next 2H candle open and is not canceled afterward.",
        "Threshold percent is applied exactly as a distance filter from the key candle close to the long SMA using the key candle close as the percentage base.",
        "Take profit is fixed from the key candle spread to the long SMA and is not dynamically recomputed later unless you ask for a re-entry or pyramid variant.",
    ]


def _risk_reward_ratio(entry_price: float, stop_loss_price: float, take_profit_price: float) -> float:
    risk = abs(float(entry_price) - float(stop_loss_price))
    reward = abs(float(take_profit_price) - float(entry_price))
    if risk <= 0:
        return 0.0
    return reward / risk


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
    timestamps = series["timestamp"]
    opens = series["open"]
    closes = series["close"]
    short_sma = sma(closes, SHORT_SMA_LENGTH)
    long_sma = sma(closes, LONG_SMA_LENGTH)

    key_index = len(closes) - 2
    entry_index = len(closes) - 1
    if key_index < 0 or entry_index >= len(opens):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["next bar does not exist for entry"],
            "assumption_flags": _build_assumption_flags(),
        }

    key_close = float(closes[key_index])
    key_short_sma = short_sma[key_index]
    key_long_sma = long_sma[key_index]
    entry_open = float(opens[entry_index])
    if key_short_sma is None or key_long_sma is None:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    key_short_sma_value = float(key_short_sma)
    key_long_sma_value = float(key_long_sma)
    long_signal = (
        key_short_sma_value > key_long_sma_value
        and key_close < key_short_sma_value
        and (key_close - key_long_sma_value) > (key_close * THRESHOLD_PCT)
    )
    short_signal = (
        key_short_sma_value < key_long_sma_value
        and key_close > key_short_sma_value
        and (key_long_sma_value - key_close) > (key_close * THRESHOLD_PCT)
    )

    if long_signal and short_signal:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "ambiguous",
            "notes": ["signal candle matched both long and short rules unexpectedly"],
            "assumption_flags": _build_assumption_flags(),
        }
    if not long_signal and not short_signal:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "no_signal",
            "notes": ["latest closed 2H candle did not form a valid dual-SMA pullback setup"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "key_close": round(key_close, 8),
                "short_sma_13": round(key_short_sma_value, 8),
                "long_sma_59": round(key_long_sma_value, 8),
                "threshold_pct": THRESHOLD_PCT,
            },
        }

    side = "buy" if long_signal else "sell"
    stop_loss_price = key_long_sma_value
    spread = abs(key_close - key_long_sma_value)
    if side == "buy":
        take_profit_price = key_close + (spread * PROFIT_DISTANCE)
    else:
        take_profit_price = key_close - (spread * PROFIT_DISTANCE)

    rr_ratio = _risk_reward_ratio(
        entry_price=entry_open,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = DualSmaCandidate(
        symbol=symbol,
        side=side,
        timeframe=TIMEFRAME,
        key_candle_timestamp_ms=float(timestamps[key_index]),
        entry_timestamp_ms=float(timestamps[entry_index]),
        entry_price=round(entry_open, 8),
        stop_loss_price=round(float(stop_loss_price), 8),
        take_profit_price=round(float(take_profit_price), 8),
        short_sma_value=round(key_short_sma_value, 8),
        long_sma_value=round(key_long_sma_value, 8),
        threshold_pct=THRESHOLD_PCT,
        profit_distance=PROFIT_DISTANCE,
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        status="candidate",
        notes=[
            "Signal uses the latest fully closed 2H candle only.",
            "Entry is fixed at the next 2H candle open and is not canceled afterward.",
            "Stop loss is fixed at the key candle SMA59 value.",
            "Take profit is fixed from the key candle close-to-SMA59 spread times the profit-distance factor.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot={
            "key_close": round(key_close, 8),
            "short_sma_13": round(key_short_sma_value, 8),
            "long_sma_59": round(key_long_sma_value, 8),
            "spread_to_long_sma": round(spread, 8),
            "threshold_pct": THRESHOLD_PCT,
            "profit_distance": PROFIT_DISTANCE,
            "entry_open": round(entry_open, 8),
        },
        learning_snapshot=dict(learning_stats),
    )
    return candidate.to_dict()


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
