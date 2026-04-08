from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from indicator_utils import crossunder, crossover, hma, ohlcv_to_series, rolling_stddev
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position

STRATEGY_ID = "trend_hma_std_4h_v1"
HMA_LENGTH = 9
STDDEV_LENGTH = 9
TIMEFRAME = "4h"
MIN_CANDLES = 80
TP_MULTIPLIER = 3.0
SL_MULTIPLIER = 2.0


@dataclass
class StrategyCandidate:
    symbol: str
    side: str
    timeframe: str
    key_candle_timestamp_ms: float
    key_candle_time_utc: str
    entry_timestamp_ms: float
    entry_time_utc: str
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    key_stddev: float
    latest_stddev: float
    hma_value: float
    close_price: float
    rr_ratio: float
    win_rate: float
    rank_score: float
    rank_reason: str
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
            name="4H Trend HMA + StdDev",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="top volume USDT swap symbols",
            scan_interval_sec=60,
            required_data=[
                StrategyDataRequirement(
                    key="ohlcv_4h",
                    source="exchange.fetch_ohlcv",
                    timeframe="4h",
                    lookback=MIN_CANDLES,
                    note="Need closed candles only for HMA(9), StdDev(9), and next-bar entry logic.",
                ),
                StrategyDataRequirement(
                    key="ticker_last",
                    source="exchange.fetch_ticker",
                    timeframe="realtime",
                    lookback=1,
                    note="Used only as a cross-check or execution context, not for signal generation.",
                ),
            ],
            decision_inputs=[
                "closed-candle HMA(9) crossover/crossunder",
                "closed-candle close relative to HMA",
                "key candle standard deviation length 9 on close",
                "latest closed candle standard deviation length 9 on close",
                "UTC time filter Sunday 00:00 through Monday 00:00",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "RR realized vs expected",
                "strategy-side selection quality",
            ],
            tags=["trend", "4h", "hma", "stddev", "trailing-tp"],
            note="Entry is next candle open after the key candle closes. Stop loss is fixed from key candle stddev. Take profit is dynamic and re-based from entry using the latest closed candle stddev.",
        )
    )


register_strategy_spec()


def _utc_dt_from_ms(timestamp_ms: float) -> datetime:
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)


def _iso_utc(timestamp_ms: float) -> str:
    return _utc_dt_from_ms(timestamp_ms).strftime("%Y-%m-%d %H:%M:%S UTC")


def _is_time_blocked_for_new_entry(entry_timestamp_ms: float) -> bool:
    dt = _utc_dt_from_ms(entry_timestamp_ms)
    weekday = dt.weekday()
    return weekday == 6


def _build_assumption_flags() -> List[str]:
    return [
        "Signal uses exchange OHLCV close values, not TradingView broker-specific candles.",
        "Cross detection is evaluated only after candle close to avoid intrabar repaint-like mistakes.",
        "Next-bar open entry uses exchange OHLCV next candle open; live fill can differ from historical open.",
        "Trailing TP is recalculated from the latest fully closed candle standard deviation, matching your rule as closely as exchange OHLCV allows.",
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
            "status": "skip",
            "notes": [f"need at least {MIN_CANDLES} candles"],
            "assumption_flags": _build_assumption_flags(),
        }

    series = ohlcv_to_series(ohlcv)
    closes = series["close"]
    opens = series["open"]
    timestamps = series["timestamp"]
    hma_values = hma(closes, HMA_LENGTH)
    std_values = rolling_stddev(closes, STDDEV_LENGTH)

    key_index = len(closes) - 2
    prev_index = key_index - 1
    entry_index = len(closes) - 1

    if prev_index < 0:
        return {
            "symbol": symbol,
            "status": "skip",
            "notes": ["not enough candles for crossover check"],
            "assumption_flags": _build_assumption_flags(),
        }

    key_hma = hma_values[key_index]
    prev_hma = hma_values[prev_index]
    key_stddev = std_values[key_index]
    latest_closed_stddev = std_values[key_index]
    prev_close = closes[prev_index]
    key_close = closes[key_index]
    entry_open = opens[entry_index]
    entry_timestamp_ms = timestamps[entry_index]

    if key_hma is None or prev_hma is None or key_stddev is None or latest_closed_stddev is None:
        return {
            "symbol": symbol,
            "status": "skip",
            "notes": ["indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    if _is_time_blocked_for_new_entry(entry_timestamp_ms):
        return {
            "symbol": symbol,
            "status": "blocked_time_filter",
            "notes": ["UTC Sunday through Monday entries are disabled"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "key_close": round(key_close, 8),
                "key_hma": round(float(key_hma), 8),
                "key_stddev": round(float(key_stddev), 8),
            },
        }

    long_trigger = crossover(prev_close, float(prev_hma), key_close, float(key_hma)) and key_close > float(key_hma)
    short_trigger = crossunder(prev_close, float(prev_hma), key_close, float(key_hma)) and key_close < float(key_hma)

    if not long_trigger and not short_trigger:
        return {
            "symbol": symbol,
            "status": "no_signal",
            "notes": ["latest closed 4H candle is not a valid key candle"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "prev_close": round(prev_close, 8),
                "prev_hma": round(float(prev_hma), 8),
                "key_close": round(key_close, 8),
                "key_hma": round(float(key_hma), 8),
                "key_stddev": round(float(key_stddev), 8),
            },
        }

    side = "buy" if long_trigger else "sell"
    if side == "buy":
        stop_loss_price = entry_open - float(key_stddev) * SL_MULTIPLIER
        take_profit_price = entry_open + float(latest_closed_stddev) * TP_MULTIPLIER
    else:
        stop_loss_price = entry_open + float(key_stddev) * SL_MULTIPLIER
        take_profit_price = entry_open - float(latest_closed_stddev) * TP_MULTIPLIER

    rr_ratio = _risk_reward_ratio(
        entry_price=entry_open,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = StrategyCandidate(
        symbol=symbol,
        side=side,
        timeframe=TIMEFRAME,
        key_candle_timestamp_ms=float(timestamps[key_index]),
        key_candle_time_utc=_iso_utc(timestamps[key_index]),
        entry_timestamp_ms=float(entry_timestamp_ms),
        entry_time_utc=_iso_utc(entry_timestamp_ms),
        entry_price=round(entry_open, 8),
        stop_loss_price=round(stop_loss_price, 8),
        take_profit_price=round(take_profit_price, 8),
        key_stddev=round(float(key_stddev), 8),
        latest_stddev=round(float(latest_closed_stddev), 8),
        hma_value=round(float(key_hma), 8),
        close_price=round(float(key_close), 8),
        rr_ratio=round(rr_ratio, 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(rank_score, 6),
        rank_reason="rank = rr_ratio + learned_win_rate; rr first, then win rate as tie-breaker",
        status="candidate",
        notes=[
            "Key candle is the latest fully closed 4H bar.",
            "Entry is the next 4H candle open from exchange OHLCV.",
            "Stop loss is fixed from key candle stddev * 2.",
            "Take profit is initialized from the latest closed candle stddev * 3 and should be updated after each new closed candle.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot={
            "prev_close": round(prev_close, 8),
            "prev_hma": round(float(prev_hma), 8),
            "key_close": round(key_close, 8),
            "key_hma": round(float(key_hma), 8),
            "key_stddev": round(float(key_stddev), 8),
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


def update_dynamic_take_profit(
    side: str,
    entry_price: float,
    latest_closed_stddev: float,
) -> float:
    if str(side).lower() == "buy":
        return round(float(entry_price) + float(latest_closed_stddev) * TP_MULTIPLIER, 8)
    return round(float(entry_price) - float(latest_closed_stddev) * TP_MULTIPLIER, 8)


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
