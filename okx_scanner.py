import time
from typing import Any, Dict, List

from okx_force_order import create_okx_exchange
from strategy_dual_sma_pullback_2h import STRATEGY_ID as DUAL_SMA_STRATEGY_ID
from strategy_dual_sma_pullback_2h import TIMEFRAME as DUAL_SMA_TIMEFRAME
from strategy_dual_sma_pullback_2h import apply_strategy_position_lock as apply_dual_sma_position_lock
from strategy_dual_sma_pullback_2h import evaluate_symbol_for_entry as evaluate_dual_sma_entry
from strategy_dual_sma_pullback_2h import rank_candidates as rank_dual_sma_candidates
from strategy_mean_reversion_atr_2h_daily import STRATEGY_ID as MEAN_REV_STRATEGY_ID
from strategy_mean_reversion_atr_2h_daily import ENTRY_TIMEFRAME as MEAN_REV_TIMEFRAME
from strategy_mean_reversion_atr_2h_daily import apply_strategy_position_lock as apply_mean_rev_position_lock
from strategy_mean_reversion_atr_2h_daily import evaluate_symbol_for_entry as evaluate_mean_rev_entry
from strategy_mean_reversion_atr_2h_daily import rank_candidates as rank_mean_rev_candidates
from strategy_naked_k_reversal_1h import STRATEGY_ID as NAKED_K_STRATEGY_ID
from strategy_naked_k_reversal_1h import TIMEFRAME as NAKED_K_TIMEFRAME
from strategy_naked_k_reversal_1h import apply_strategy_position_lock as apply_naked_k_position_lock
from strategy_naked_k_reversal_1h import evaluate_symbol_for_entry as evaluate_naked_k_entry
from strategy_naked_k_reversal_1h import rank_candidates as rank_naked_k_candidates
from strategy_burst_sma_channel_1h import STRATEGY_ID as BURST_STRATEGY_ID
from strategy_burst_sma_channel_1h import TIMEFRAME as BURST_TIMEFRAME
from strategy_burst_sma_channel_1h import apply_strategy_position_lock as apply_burst_position_lock
from strategy_burst_sma_channel_1h import evaluate_symbol_for_entry as evaluate_burst_entry
from strategy_burst_sma_channel_1h import rank_candidates as rank_burst_candidates
from strategy_ma_breakout_4h import STRATEGY_ID as MA_BREAKOUT_STRATEGY_ID
from strategy_ma_breakout_4h import TIMEFRAME as MA_BREAKOUT_TIMEFRAME
from strategy_ma_breakout_4h import apply_strategy_position_lock as apply_ma_breakout_position_lock
from strategy_ma_breakout_4h import evaluate_symbol_for_entry as evaluate_ma_breakout_entry
from strategy_ma_breakout_4h import rank_candidates as rank_ma_breakout_candidates
from strategy_bollinger_width_4h import STRATEGY_ID as BBW_STRATEGY_ID
from strategy_bollinger_width_4h import TIMEFRAME as BBW_TIMEFRAME
from strategy_bollinger_width_4h import apply_strategy_position_lock as apply_bbw_position_lock
from strategy_bollinger_width_4h import evaluate_symbol_for_entry as evaluate_bbw_entry
from strategy_bollinger_width_4h import rank_candidates as rank_bbw_candidates
from strategy_portfolio import filter_candidates_by_symbol_lock
from strategy_larry_breakout_cmo import STRATEGY_ID as LARRY_STRATEGY_ID
from strategy_larry_breakout_cmo import SUPPORTED_TIMEFRAMES as LARRY_TIMEFRAMES
from strategy_larry_breakout_cmo import apply_strategy_position_lock as apply_larry_position_lock
from strategy_larry_breakout_cmo import evaluate_symbol_timeframe_for_entry as evaluate_larry_entry
from strategy_larry_breakout_cmo import rank_candidates as rank_larry_candidates
from strategy_trend_hma_std import TIMEFRAME as TREND_TIMEFRAME
from strategy_trend_hma_std import STRATEGY_ID as TREND_STRATEGY_ID
from strategy_trend_hma_std import apply_strategy_position_lock
from strategy_trend_hma_std import evaluate_symbol_for_entry, rank_candidates


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def is_usdt_swap_market(symbol: str, market: Dict[str, Any]) -> bool:
    if not market:
        return False
    if not bool(market.get("swap")):
        return False
    if not bool(market.get("active", True)):
        return False
    settle = str(market.get("settle") or "").upper()
    quote = str(market.get("quote") or "").upper()
    market_id = str(market.get("id") or "").upper()
    symbol_upper = str(symbol or "").upper()
    return (
        settle == "USDT"
        or quote == "USDT"
        or symbol_upper.endswith("USDT:USDT")
        or market_id.endswith("-USDT-SWAP")
    )


def fetch_scan_universe(exchange, limit: int = 70) -> List[str]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    ranked: List[tuple[str, float]] = []
    for symbol, ticker in (tickers or {}).items():
        market = markets.get(symbol) or {}
        if not is_usdt_swap_market(symbol, market):
            continue

        volume = _safe_float(ticker.get("quoteVolume"))
        if volume <= 0:
            volume = _safe_float((ticker.get("info") or {}).get("volCcy24h"))
        if volume <= 0:
            continue

        ranked.append((symbol, volume))

    ranked.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ranked[: max(int(limit or 0), 1)]]


def build_symbol_snapshot(exchange, symbol: str, timeframe: str = "15m", candles: int = 120) -> Dict[str, Any]:
    ticker = exchange.fetch_ticker(symbol)
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=candles)

    closes = [row[4] for row in ohlcv if len(row) >= 5]
    highs = [row[2] for row in ohlcv if len(row) >= 3]
    lows = [row[3] for row in ohlcv if len(row) >= 4]

    last_price = _safe_float(ticker.get("last"))
    open_price = _safe_float(ticker.get("open"))
    high_price = max(highs) if highs else _safe_float(ticker.get("high"))
    low_price = min(lows) if lows else _safe_float(ticker.get("low"))
    change_pct = 0.0
    if open_price > 0 and last_price > 0:
        change_pct = (last_price - open_price) / open_price * 100.0

    close_ma_20 = sum(closes[-20:]) / min(len(closes), 20) if closes else 0.0
    close_ma_60 = sum(closes[-60:]) / min(len(closes), 60) if closes else 0.0

    return {
        "symbol": symbol,
        "last_price": round(last_price, 8),
        "quote_volume": round(_safe_float(ticker.get("quoteVolume")), 2),
        "base_volume": round(_safe_float(ticker.get("baseVolume")), 4),
        "change_pct": round(change_pct, 4),
        "high_price": round(high_price, 8),
        "low_price": round(low_price, 8),
        "close_ma_20": round(close_ma_20, 8),
        "close_ma_60": round(close_ma_60, 8),
        "close_above_ma20": bool(last_price > close_ma_20) if close_ma_20 else False,
        "close_above_ma60": bool(last_price > close_ma_60) if close_ma_60 else False,
        "candles": len(ohlcv),
        "timeframe": timeframe,
    }


def scan_market(
    exchange,
    limit: int = 70,
    timeframe: str = "15m",
    candles: int = 120,
    sleep_sec: float = 0.2,
) -> List[Dict[str, Any]]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    snapshots: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            snapshot = build_symbol_snapshot(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
            )
            snapshot["rank"] = index
            snapshots.append(snapshot)
        except Exception as exc:
            snapshots.append(
                {
                    "symbol": symbol,
                    "rank": index,
                    "error": str(exc),
                    "timeframe": timeframe,
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    return snapshots


def scan_trend_hma_std_candidates(
    exchange,
    limit: int = 70,
    candles: int = 120,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TREND_TIMEFRAME, limit=candles)
            item = evaluate_symbol_for_entry(symbol=symbol, ohlcv=ohlcv)
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                    "timeframe": TREND_TIMEFRAME,
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_strategy_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=TREND_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_candidates(evaluations)
    return {
        "strategy_id": TREND_STRATEGY_ID,
        "timeframe": TREND_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_larry_breakout_candidates(
    exchange,
    limit: int = 70,
    candles: int = 120,
    sleep_sec: float = 0.2,
    length: int = 40,
    momentum_length: int = 30,
    adaptive: float = 0.0,
    scaling_factor: float = 0.1,
) -> Dict[str, Any]:
    markets = exchange.load_markets()
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for symbol_index, symbol in enumerate(symbols, start=1):
        market = markets.get(symbol) or {}
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = _safe_float(ticker.get("last"))
        except Exception:
            current_price = 0.0

        for timeframe in LARRY_TIMEFRAMES:
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=candles)
                item = evaluate_larry_entry(
                    symbol=symbol,
                    timeframe=timeframe,
                    market=market,
                    ohlcv=ohlcv,
                    current_price=current_price,
                    length=length,
                    momentum_length=momentum_length,
                    adaptive=adaptive,
                    scaling_factor=scaling_factor,
                )
                item["scan_rank"] = symbol_index
                evaluations.append(item)
            except Exception as exc:
                evaluations.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "scan_rank": symbol_index,
                        "status": "error",
                        "notes": [str(exc)],
                    }
                )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_larry_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=LARRY_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_larry_candidates(evaluations)
    return {
        "strategy_id": LARRY_STRATEGY_ID,
        "timeframes": list(LARRY_TIMEFRAMES),
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_bollinger_width_candidates(
    exchange,
    limit: int = 70,
    candles: int = 120,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=BBW_TIMEFRAME, limit=candles)
            item = evaluate_bbw_entry(symbol=symbol, ohlcv=ohlcv)
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "timeframe": BBW_TIMEFRAME,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_bbw_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=BBW_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_bbw_candidates(evaluations)
    return {
        "strategy_id": BBW_STRATEGY_ID,
        "timeframe": BBW_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_ma_breakout_candidates(
    exchange,
    limit: int = 70,
    candles: int = 120,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = _safe_float(ticker.get("last"))
        except Exception:
            current_price = 0.0

        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=MA_BREAKOUT_TIMEFRAME, limit=candles)
            item = evaluate_ma_breakout_entry(
                symbol=symbol,
                ohlcv=ohlcv,
                current_price=current_price,
            )
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "timeframe": MA_BREAKOUT_TIMEFRAME,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_ma_breakout_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=MA_BREAKOUT_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_ma_breakout_candidates(evaluations)
    return {
        "strategy_id": MA_BREAKOUT_STRATEGY_ID,
        "timeframe": MA_BREAKOUT_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_burst_sma_channel_candidates(
    exchange,
    limit: int = 70,
    candles: int = 360,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=BURST_TIMEFRAME, limit=candles)
            item = evaluate_burst_entry(symbol=symbol, ohlcv=ohlcv)
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "timeframe": BURST_TIMEFRAME,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_burst_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=BURST_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_burst_candidates(evaluations)
    return {
        "strategy_id": BURST_STRATEGY_ID,
        "timeframe": BURST_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_naked_k_reversal_candidates(
    exchange,
    limit: int = 70,
    candles: int = 180,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=NAKED_K_TIMEFRAME, limit=candles)
            item = evaluate_naked_k_entry(symbol=symbol, ohlcv=ohlcv)
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "timeframe": NAKED_K_TIMEFRAME,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_naked_k_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=NAKED_K_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_naked_k_candidates(evaluations)
    return {
        "strategy_id": NAKED_K_STRATEGY_ID,
        "timeframe": NAKED_K_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_mean_reversion_atr_candidates(
    exchange,
    limit: int = 70,
    two_h_candles: int = 160,
    daily_candles: int = 60,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            two_h_ohlcv = exchange.fetch_ohlcv(symbol, timeframe=MEAN_REV_TIMEFRAME, limit=two_h_candles)
            daily_ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=daily_candles)
            item = evaluate_mean_rev_entry(
                symbol=symbol,
                two_h_ohlcv=two_h_ohlcv,
                daily_ohlcv=daily_ohlcv,
            )
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "timeframe": MEAN_REV_TIMEFRAME,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_mean_rev_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=MEAN_REV_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_mean_rev_candidates(evaluations)
    return {
        "strategy_id": MEAN_REV_STRATEGY_ID,
        "timeframe": MEAN_REV_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


def scan_dual_sma_pullback_candidates(
    exchange,
    limit: int = 70,
    candles: int = 140,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    symbols = fetch_scan_universe(exchange=exchange, limit=limit)
    evaluations: List[Dict[str, Any]] = []

    for index, symbol in enumerate(symbols, start=1):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=DUAL_SMA_TIMEFRAME, limit=candles)
            item = evaluate_dual_sma_entry(symbol=symbol, ohlcv=ohlcv)
            item["scan_rank"] = index
            evaluations.append(item)
        except Exception as exc:
            evaluations.append(
                {
                    "symbol": symbol,
                    "timeframe": DUAL_SMA_TIMEFRAME,
                    "scan_rank": index,
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
        time.sleep(max(float(sleep_sec or 0), 0.0))

    evaluations = apply_dual_sma_position_lock(evaluations)
    evaluations = filter_candidates_by_symbol_lock(
        strategy_id=DUAL_SMA_STRATEGY_ID,
        candidates=evaluations,
    )
    ranked = rank_dual_sma_candidates(evaluations)
    return {
        "strategy_id": DUAL_SMA_STRATEGY_ID,
        "timeframe": DUAL_SMA_TIMEFRAME,
        "candidates": evaluations,
        "ranked_candidates": ranked,
        "best_candidate": ranked[0] if ranked else None,
    }


if __name__ == "__main__":
    exchange = create_okx_exchange()
    results = scan_trend_hma_std_candidates(
        exchange=exchange,
        limit=20,
        candles=120,
        sleep_sec=0.2,
    )
    print(results.get("best_candidate"))
