from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from indicator_utils import chande_momentum_oscillator, hlc3, rolling_highest, rolling_lowest, ohlcv_to_series
from learning_store import get_strategy_symbol_stats, get_strategy_symbol_win_rate
from strategy_registry import StrategyDataRequirement, StrategySpec, upsert_strategy
from strategy_runtime_state import get_strategy_active_position, has_strategy_active_position

STRATEGY_ID = "larry_breakout_cmo_2h_4h_v1"
DEFAULT_LENGTH = 40
DEFAULT_MOMENTUM_LENGTH = 30
SUPPORTED_TIMEFRAMES = ("2h", "4h")
MIN_CANDLES = 120
DEFAULT_ADAPTIVE = 0.0
DEFAULT_SCALING_FACTOR = 0.1


@dataclass
class LarryCandidate:
    symbol: str
    side: str
    timeframe: str
    trigger_price: float
    entry_price: float
    stop_loss_price: float
    green_line_prev: float
    red_line_prev: float
    green_line_current: float
    red_line_current: float
    cmo_closed: float
    cmo_current_proxy: float
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
            name="Larry Breakout + ChandeMO",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="top volume USDT swap symbols",
            scan_interval_sec=20,
            required_data=[
                StrategyDataRequirement(
                    key="ohlcv_2h",
                    source="exchange.fetch_ohlcv",
                    timeframe="2h",
                    lookback=MIN_CANDLES,
                    note="Needed for breakout line reconstruction and intrabar trigger approximation.",
                ),
                StrategyDataRequirement(
                    key="ohlcv_4h",
                    source="exchange.fetch_ohlcv",
                    timeframe="4h",
                    lookback=MIN_CANDLES,
                    note="Needed for breakout line reconstruction and intrabar trigger approximation.",
                ),
                StrategyDataRequirement(
                    key="ticker_last",
                    source="exchange.fetch_ticker",
                    timeframe="realtime",
                    lookback=1,
                    note="Used to approximate current intrabar trigger execution.",
                ),
            ],
            decision_inputs=[
                "previous breakout lines from highest(PH, length)+tick and lowest(PL, length)-tick",
                "current intrabar price crossing previous line",
                "current opposite line as initial stop loss",
                "Chande Momentum Oscillator length 30 for momentum exit",
                "line-touch trailing exit",
            ],
            learning_targets=[
                "per-symbol win rate for this strategy",
                "proxy RR selection quality",
                "timeframe selection quality between 2h and 4h",
            ],
            tags=["trend", "breakout", "larry-williams", "cmo", "intrabar"],
            note="Breakout is approximated by current price crossing the previous completed breakout line. Exact TradingView stop-order replay is not available from exchange OHLCV alone.",
        )
    )


register_strategy_spec()


def _iso_utc(timestamp_ms: float) -> str:
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _build_assumption_flags() -> List[str]:
    return [
        "The original ST168/LarryWilliams main indicator source was not provided, so the core breakout lines are reconstructed from the visible formula snippet only.",
        "Breakout entry is approximated with current exchange price and current partial candle values, not TradingView tick-by-tick stop-order replay.",
        "Adaptive parameter is kept at 0 for exact reconstruction with the provided information. Non-zero adaptive behavior is intentionally not fabricated.",
        "CMO uses length 30 and close source, matching the user-provided setting rather than the default TradingView example file.",
    ]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _price_tick_size(market: Dict[str, Any], reference_price: float) -> float:
    info = market.get("info") or {}
    raw_tick = info.get("tickSz") if isinstance(info, dict) else None
    if raw_tick is not None:
        tick = _safe_float(raw_tick)
        if tick > 0:
            return tick

    precision = (market.get("precision") or {}).get("price")
    if precision is not None:
        try:
            precision_value = int(precision)
            if precision_value >= 0:
                return 10 ** (-precision_value)
        except Exception:
            pass

    return max(abs(float(reference_price)) * 0.000001, 1e-8)


def _proxy_rr(entry_price: float, stop_loss_price: float, green_line: float, red_line: float) -> float:
    risk = abs(float(entry_price) - float(stop_loss_price))
    if risk <= 0:
        return 0.0
    channel_width = abs(float(green_line) - float(red_line))
    return channel_width / risk


def _apply_position_lock(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


def build_breakout_lines(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    length: int,
    tick_size: float,
    adaptive: float = DEFAULT_ADAPTIVE,
) -> Dict[str, List[Optional[float]]]:
    if adaptive not in (0, 0.0):
        raise NotImplementedError("adaptive != 0 is not implemented because the source indicator formula was not provided")

    typical = hlc3(highs, lows, closes)
    ph = [2.0 * t - float(low) for t, low in zip(typical, lows)]
    pl = [2.0 * t - float(high) for t, high in zip(typical, highs)]
    highest_ph = rolling_highest(ph, length)
    lowest_pl = rolling_lowest(pl, length)

    green_lines: List[Optional[float]] = []
    red_lines: List[Optional[float]] = []
    for high_value, low_value in zip(highest_ph, lowest_pl):
        green_lines.append(None if high_value is None else float(high_value) + float(tick_size))
        red_lines.append(None if low_value is None else float(low_value) - float(tick_size))

    return {
        "ph": ph,
        "pl": pl,
        "green": green_lines,
        "red": red_lines,
    }


def evaluate_symbol_timeframe_for_entry(
    symbol: str,
    timeframe: str,
    market: Dict[str, Any],
    ohlcv: List[List[float]],
    current_price: float,
    length: int = DEFAULT_LENGTH,
    momentum_length: int = DEFAULT_MOMENTUM_LENGTH,
    adaptive: float = DEFAULT_ADAPTIVE,
    scaling_factor: float = DEFAULT_SCALING_FACTOR,
) -> Dict[str, Any]:
    del scaling_factor  # kept for config compatibility; exact formula missing
    if len(ohlcv) < MIN_CANDLES:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
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
    prev_index = len(closes) - 2

    tick_size = _price_tick_size(market=market, reference_price=current_price or closes[current_index])
    lines = build_breakout_lines(
        highs=highs,
        lows=lows,
        closes=closes,
        length=length,
        tick_size=tick_size,
        adaptive=adaptive,
    )
    green_prev = lines["green"][prev_index]
    red_prev = lines["red"][prev_index]
    green_current = lines["green"][current_index]
    red_current = lines["red"][current_index]
    if None in (green_prev, red_prev, green_current, red_current):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "skip",
            "notes": ["indicator warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    cmo_values = chande_momentum_oscillator(closes, momentum_length)
    cmo_closed = cmo_values[prev_index]
    cmo_current_proxy = cmo_values[current_index]
    if cmo_closed is None:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "skip",
            "notes": ["CMO warmup incomplete"],
            "assumption_flags": _build_assumption_flags(),
        }

    current_last = float(current_price or closes[current_index])
    current_open = float(opens[current_index])
    current_high = float(max(highs[current_index], current_last))
    current_low = float(min(lows[current_index], current_last))

    long_trigger = current_last >= float(green_prev) and current_low <= float(green_prev) and float(cmo_closed) > 0
    short_trigger = current_last <= float(red_prev) and current_high >= float(red_prev) and float(cmo_closed) < 0

    if long_trigger and short_trigger:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "ambiguous",
            "notes": ["current bar overlaps both long and short breakout lines"],
            "assumption_flags": _build_assumption_flags(),
        }

    if not long_trigger and not short_trigger:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "no_signal",
            "notes": ["current intrabar price has not cleanly broken the previous breakout line"],
            "assumption_flags": _build_assumption_flags(),
            "indicator_snapshot": {
                "current_open": round(current_open, 8),
                "current_last": round(current_last, 8),
                "current_high": round(current_high, 8),
                "current_low": round(current_low, 8),
                "green_line_prev": round(float(green_prev), 8),
                "red_line_prev": round(float(red_prev), 8),
                "cmo_closed": round(float(cmo_closed), 8),
            },
        }

    side = "buy" if long_trigger else "sell"
    entry_price = current_last
    stop_loss_price = float(red_current) if side == "buy" else float(green_current)
    rr_ratio = _proxy_rr(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        green_line=float(green_current),
        red_line=float(red_current),
    )
    win_rate = get_strategy_symbol_win_rate(strategy_id=STRATEGY_ID, symbol=f"{symbol}|{timeframe}", default=0.5)
    learning_stats = get_strategy_symbol_stats(strategy_id=STRATEGY_ID, symbol=f"{symbol}|{timeframe}")
    rank_score = float(rr_ratio) + float(win_rate)

    candidate = LarryCandidate(
        symbol=symbol,
        side=side,
        timeframe=timeframe,
        trigger_price=round(float(green_prev if side == "buy" else red_prev), 8),
        entry_price=round(entry_price, 8),
        stop_loss_price=round(stop_loss_price, 8),
        green_line_prev=round(float(green_prev), 8),
        red_line_prev=round(float(red_prev), 8),
        green_line_current=round(float(green_current), 8),
        red_line_current=round(float(red_current), 8),
        cmo_closed=round(float(cmo_closed), 8),
        cmo_current_proxy=round(float(cmo_current_proxy or cmo_closed), 8),
        rr_ratio=round(float(rr_ratio), 6),
        win_rate=round(float(win_rate), 6),
        rank_score=round(float(rank_score), 6),
        status="candidate",
        notes=[
            "Entry is approximated intrabar when current price breaks the previous completed breakout line.",
            "Initial stop loss is the current opposite line from the live bar.",
            "Trailing exit is handled by updating the opposite line as protection and by momentum-based next-open exit.",
            "Proxy RR is used because this strategy does not have a fixed TP target.",
        ],
        assumption_flags=_build_assumption_flags(),
        indicator_snapshot={
            "current_bar_time_utc": _iso_utc(timestamps[current_index]),
            "previous_bar_time_utc": _iso_utc(timestamps[prev_index]),
            "current_open": round(current_open, 8),
            "current_last": round(current_last, 8),
            "current_high": round(current_high, 8),
            "current_low": round(current_low, 8),
            "green_prev": round(float(green_prev), 8),
            "red_prev": round(float(red_prev), 8),
            "green_current": round(float(green_current), 8),
            "red_current": round(float(red_current), 8),
            "tick_size": round(float(tick_size), 10),
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
    return _apply_position_lock(candidates)
