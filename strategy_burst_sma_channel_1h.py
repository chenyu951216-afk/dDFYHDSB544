from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from indicator_utils import ohlcv_to_series, rolling_highest, rolling_lowest, sma
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position

STRATEGY_ID = "burst_sma_channel_1h_v1"
TIMEFRAME = "1h"
SMA_LENGTH = 60
ENTRY_LOOKBACK = 25
STOP_MULTIPLIER = 6
TAKE_MULTIPLIER = 10
STOP_LOOKBACK = ENTRY_LOOKBACK * STOP_MULTIPLIER
TAKE_LOOKBACK = ENTRY_LOOKBACK * TAKE_MULTIPLIER
MIN_CANDLES = 320


@dataclass
class BurstChannelCandidate:
    symbol: str
    side: str
    timeframe: str
    key_candle_timestamp_ms: float
    entry_timestamp_ms: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    sma_value: float
    entry_floor_price: float
    entry_ceiling_price: float
    stop_window_price: float
    take_window_price: float
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
            name="1H Burst SMA Channel",
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
                    note="Need prior 25/150/250-bar channels plus SMA60 on closed candles only.",
                ),
            ],
            decision_inputs=[
                "SMA60 rising or falling on closed candles",
                "closed-candle close crossing below prior 25-bar low for long",
                "closed-candle close crossing above prior 25-bar high for short",
                "fixed stop from prior 150-bar extreme",
                "dynamic take from prior 250-bar extreme with next-open execution after a close breakout",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "RR realized vs expected",
                "burst reversal selection quality",
            ],
            tags=["1h", "sma", "channel", "burst", "non-repaint"],
            note="Signal and TP exit use only fully closed candles. Stop uses touch logic against the live bar range.",
        )
    )


register_strategy_spec()


def _build_assumption_flags() -> List[str]:
    return [
        "SMA source is the standard TradingView SMA close formula from the provided SMA.txt file, with length changed from 9 to 60.",
        "Trend direction is implemented as SMA60 rising when current closed SMA is greater than the previous closed SMA, and falling when it is lower.",
        "All 25/150/250-bar channel levels exclude the signal bar itself and always look backward only, so the implementation does not peek into future data.",
        "Long TP and short TP are dynamic reference levels, but the actual TP exit is only executed after a candle closes through that level, at the next bar open approximation.",
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
    highs = series["high"]
    lows = series["low"]

    sma_values = sma(closes, SMA_LENGTH)
    entry_highs = rolling_highest(highs, ENTRY_LOOKBACK)
    entry_lows = rolling_lowest(lows, ENTRY_LOOKBACK)
    stop_highs = rolling_highest(highs, STOP_LOOKBACK)
    stop_lows = rolling_lowest(lows, STOP_LOOKBACK)
    take_highs = rolling_highest(highs, TAKE_LOOKBACK)
    take_lows = rolling_lowest(lows, TAKE_LOOKBACK)

    key_index = len(closes) - 2
    prev_index = key_index - 1
    entry_index = len(closes) - 1
    if prev_index < 0 or entry_index >= len(opens):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["not enough candles for entry alignment"],
            "assumption_flags": _build_assumption_flags(),
        }

    key_sma = sma_values[key_index]
    prev_sma = sma_values[prev_index]
    entry_high = entry_highs[prev_index]
    entry_low = entry_lows[prev_index]
    stop_high = stop_highs[prev_index]
    stop_low = stop_lows[prev_index]
    take_high = take_highs[prev_index]
    take_low = take_lows[prev_index]
    key_close = closes[key_index]
    entry_open = opens[entry_index]

    if None in (key_sma, prev_sma, entry_high, entry_low, stop_high, stop_low, take_high, take_low):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    long_trend = float(key_sma) > float(prev_sma)
    short_trend = float(key_sma) < float(prev_sma)
    long_signal = long_trend and float(key_close) < float(entry_low)
    short_signal = short_trend and float(key_close) > float(entry_high)

    if long_signal and short_signal:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "ambiguous",
            "notes": ["closed candle matched both long and short logic unexpectedly"],
            "assumption_flags": _build_assumption_flags(),
        }

    if not long_signal and not short_signal:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "no_signal",
            "notes": ["latest closed 1H candle did not form a valid burst entry setup"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "key_close": round(float(key_close), 8),
                "sma_prev": round(float(prev_sma), 8),
                "sma_key": round(float(key_sma), 8),
                "entry_high_25_prev": round(float(entry_high), 8),
                "entry_low_25_prev": round(float(entry_low), 8),
            },
        }

    side = "buy" if long_signal else "sell"
    stop_loss_price = float(stop_low) if side == "buy" else float(stop_high)
    take_profit_price = float(take_high) if side == "buy" else float(take_low)
    rr_ratio = _risk_reward_ratio(
        entry_price=entry_open,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = BurstChannelCandidate(
        symbol=symbol,
        side=side,
        timeframe=TIMEFRAME,
        key_candle_timestamp_ms=float(timestamps[key_index]),
        entry_timestamp_ms=float(timestamps[entry_index]),
        entry_price=round(float(entry_open), 8),
        stop_loss_price=round(float(stop_loss_price), 8),
        take_profit_price=round(float(take_profit_price), 8),
        sma_value=round(float(key_sma), 8),
        entry_floor_price=round(float(entry_low), 8),
        entry_ceiling_price=round(float(entry_high), 8),
        stop_window_price=round(float(stop_low if side == "buy" else stop_high), 8),
        take_window_price=round(float(take_high if side == "buy" else take_low), 8),
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        status="candidate",
        notes=[
            "Signal uses only the latest fully closed 1H candle.",
            "Entry is fixed at the next 1H candle open and is not canceled afterward.",
            "Stop loss is fixed from the prior 150-bar extreme at signal time.",
            "Take profit is dynamic from the prior 250-bar extreme and exits on next-open after a closed-candle breakout.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot={
            "key_close": round(float(key_close), 8),
            "sma_prev": round(float(prev_sma), 8),
            "sma_key": round(float(key_sma), 8),
            "entry_high_25_prev": round(float(entry_high), 8),
            "entry_low_25_prev": round(float(entry_low), 8),
            "stop_high_150_prev": round(float(stop_high), 8),
            "stop_low_150_prev": round(float(stop_low), 8),
            "take_high_250_prev": round(float(take_high), 8),
            "take_low_250_prev": round(float(take_low), 8),
            "entry_open": round(float(entry_open), 8),
        },
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

    stop_highs = rolling_highest(highs, STOP_LOOKBACK)
    stop_lows = rolling_lowest(lows, STOP_LOOKBACK)
    take_highs = rolling_highest(highs, TAKE_LOOKBACK)
    take_lows = rolling_lowest(lows, TAKE_LOOKBACK)

    current_index = len(closes) - 1
    prev_closed_index = current_index - 1
    prev_prev_index = prev_closed_index - 1
    if prev_prev_index < 0:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["not enough candles for management snapshot"],
            "assumption_flags": _build_assumption_flags(),
        }

    prev_stop_high = stop_highs[prev_prev_index]
    prev_stop_low = stop_lows[prev_prev_index]
    prev_take_high = take_highs[prev_prev_index]
    prev_take_low = take_lows[prev_prev_index]
    current_take_high = take_highs[prev_closed_index]
    current_take_low = take_lows[prev_closed_index]

    if None in (prev_stop_high, prev_stop_low, prev_take_high, prev_take_low, current_take_high, current_take_low):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["management indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

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
        "stop_high_prev": float(prev_stop_high),
        "stop_low_prev": float(prev_stop_low),
        "take_high_prev": float(prev_take_high),
        "take_low_prev": float(prev_take_low),
        "take_high_current": float(current_take_high),
        "take_low_current": float(current_take_low),
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
