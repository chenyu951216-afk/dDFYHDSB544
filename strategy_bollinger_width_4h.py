from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from indicator_utils import bollinger_bands, bollinger_bandwidth, ohlcv_to_series
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position

STRATEGY_ID = "bollinger_width_4h_v1"
TIMEFRAME = "4h"
BB_LENGTH = 25
YELLOW_MULT = 2.5
BLUE_MULT = 3.75
BBW_THRESHOLD = 0.01
MIN_CANDLES = 80


@dataclass
class BollingerCandidate:
    symbol: str
    side: str
    timeframe: str
    signal_timestamp_ms: float
    entry_timestamp_ms: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    bb_width_hist: float
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
            name="4H Bollinger Width Breakout",
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
                    note="Need closed candles only for yellow BB, blue BB, and BBW filters.",
                ),
            ],
            decision_inputs=[
                "close > yellow upper or close < yellow lower on closed candle",
                "bbw > 0.01 on closed candle",
                "entry on next bar open only",
                "stop from previous closed yellow basis",
                "take from previous closed blue outer band",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "RR realized vs expected",
            ],
            tags=["bollinger", "bbw", "4h", "non-repaint"],
            note="Strict non-repaint implementation with signal on closed candle and entry on next candle open.",
        )
    )


register_strategy_spec()


def _build_assumption_flags() -> List[str]:
    return [
        "Yellow and blue Bollinger Bands are reconstructed from close-based SMA + stdev, matching the provided TradingView formulas.",
        "Signal uses only the latest fully closed 4H candle.",
        "Entry uses the next available 4H candle open from exchange OHLCV.",
        "Exit priority is stop loss first if the same candle range touches both stop and take.",
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
    closes = series["close"]
    opens = series["open"]
    timestamps = series["timestamp"]

    yellow = bollinger_bands(values=closes, length=BB_LENGTH, mult=YELLOW_MULT)
    blue = bollinger_bands(values=closes, length=BB_LENGTH, mult=BLUE_MULT)
    bbw = bollinger_bandwidth(values=closes, length=BB_LENGTH, mult=YELLOW_MULT)

    signal_index = len(closes) - 2
    entry_index = len(closes) - 1
    if signal_index < 0 or entry_index >= len(opens):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["next bar does not exist for entry"],
            "assumption_flags": _build_assumption_flags(),
        }

    yellow_upper = yellow["upper"][signal_index]
    yellow_lower = yellow["lower"][signal_index]
    yellow_mid = yellow["basis"][signal_index]
    blue_upper = blue["upper"][signal_index]
    blue_lower = blue["lower"][signal_index]
    bbw_value = bbw["bbw"][signal_index]
    close_signal = closes[signal_index]
    entry_open = opens[entry_index]

    if None in (yellow_upper, yellow_lower, yellow_mid, blue_upper, blue_lower, bbw_value):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "skip",
            "notes": ["indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    if not (float(bbw_value) > BBW_THRESHOLD):
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "no_signal",
            "notes": ["bbw filter did not pass"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "bbw_value": round(float(bbw_value), 8),
                "bbw_threshold": BBW_THRESHOLD,
            },
        }

    long_signal = float(close_signal) > float(yellow_upper)
    short_signal = float(close_signal) < float(yellow_lower)
    if long_signal and short_signal:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "ambiguous",
            "notes": ["signal candle matched both long and short conditions unexpectedly"],
            "assumption_flags": _build_assumption_flags(),
        }

    if not long_signal and not short_signal:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "status": "no_signal",
            "notes": ["signal candle did not close outside yellow Bollinger band"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "close_signal": round(float(close_signal), 8),
                "yellow_upper": round(float(yellow_upper), 8),
                "yellow_lower": round(float(yellow_lower), 8),
                "bbw_value": round(float(bbw_value), 8),
            },
        }

    side = "buy" if long_signal else "sell"
    stop_loss_price = float(yellow_mid)
    take_profit_price = float(blue_upper) if side == "buy" else float(blue_lower)
    rr_ratio = _risk_reward_ratio(
        entry_price=entry_open,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=symbol, default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=symbol)
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = BollingerCandidate(
        symbol=symbol,
        side=side,
        timeframe=TIMEFRAME,
        signal_timestamp_ms=float(timestamps[signal_index]),
        entry_timestamp_ms=float(timestamps[entry_index]),
        entry_price=round(float(entry_open), 8),
        stop_loss_price=round(float(stop_loss_price), 8),
        take_profit_price=round(float(take_profit_price), 8),
        bb_width_hist=round(float(bbw_value), 8),
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        status="candidate",
        notes=[
            "Signal uses the latest fully closed 4H candle only.",
            "Entry is fixed at the next 4H candle open and is not canceled afterward.",
            "Initial stop and take are based on the signal candle bands.",
            "During holding, stop and take are recalculated each bar from the previous closed candle bands.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot={
            "close_signal": round(float(close_signal), 8),
            "yellow_upper": round(float(yellow_upper), 8),
            "yellow_mid": round(float(yellow_mid), 8),
            "yellow_lower": round(float(yellow_lower), 8),
            "blue_upper": round(float(blue_upper), 8),
            "blue_lower": round(float(blue_lower), 8),
            "bbw_value": round(float(bbw_value), 8),
            "entry_open": round(float(entry_open), 8),
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
