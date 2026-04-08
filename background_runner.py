import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from okx_force_order import create_okx_exchange, env_or_blank
from okx_scanner import (
    scan_bollinger_width_candidates,
    scan_burst_sma_channel_candidates,
    scan_dual_sma_pullback_candidates,
    scan_larry_breakout_candidates,
    scan_ma_breakout_candidates,
    scan_mean_reversion_atr_candidates,
    scan_naked_k_reversal_candidates,
    scan_trend_hma_std_candidates,
)
from strategy_orchestrator import run_all_strategies


RUNNER_STATE: Dict[str, Any] = {
    "enabled": False,
    "started": False,
    "running": False,
    "mode": "\u672a\u555f\u7528",
    "interval_sec": 60,
    "loop_count": 0,
    "last_cycle_started_at": "",
    "last_cycle_finished_at": "",
    "last_error": "",
    "last_results": [],
    "last_trade_results": [],
}

_RUNNER_LOCK = threading.Lock()
_RUNNER_THREAD = None


def _truthy(name: str, default: str = "false") -> bool:
    value = str(os.getenv(name, default) or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _interval_sec() -> int:
    try:
        return max(int(os.getenv("AUTO_SCAN_INTERVAL_SEC", "60")), 10)
    except Exception:
        return 60


def _scanner_enabled() -> bool:
    return _truthy("ENABLE_BACKGROUND_SCANNER", "true")


def _autotrader_enabled() -> bool:
    return _truthy("ENABLE_AUTOTRADER", "true")


def _runner_enabled() -> bool:
    return _scanner_enabled() or _autotrader_enabled()


def _mode_text() -> str:
    if _scanner_enabled() and _autotrader_enabled():
        return "\u6383\u5e63 + \u81ea\u52d5\u4e0b\u55ae"
    if _autotrader_enabled():
        return "\u81ea\u52d5\u4e0b\u55ae"
    if _scanner_enabled():
        return "\u53ea\u6383\u5e63"
    return "\u672a\u555f\u7528"


def _has_okx_credentials() -> bool:
    return bool(
        env_or_blank("OKX_API_KEY")
        and env_or_blank("OKX_SECRET")
        and env_or_blank("OKX_PASSWORD")
    )


def _scan_jobs() -> List[Dict[str, Any]]:
    return [
        {
            "strategy_id": "trend_hma_std_4h_v1",
            "name": "\u7b56\u7565 1\uff5c\u9806\u52e2 HMA \u6a19\u6e96\u5dee",
            "runner": lambda exchange: scan_trend_hma_std_candidates(
                exchange=exchange,
                limit=70,
                candles=120,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "larry_breakout_cmo_2h_4h_v1",
            "name": "\u7b56\u7565 2\uff5cLarry \u7a81\u7834\u52d5\u80fd",
            "runner": lambda exchange: scan_larry_breakout_candidates(
                exchange=exchange,
                limit=70,
                candles=140,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "bollinger_width_4h_v1",
            "name": "\u7b56\u7565 3\uff5c\u5e03\u6797\u901a\u9053 BBW",
            "runner": lambda exchange: scan_bollinger_width_candidates(
                exchange=exchange,
                limit=70,
                candles=120,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "ma_breakout_4h_v1",
            "name": "\u7b56\u7565 4\uff5c\u5747\u7dda\u7a81\u7834\u639b\u55ae",
            "runner": lambda exchange: scan_ma_breakout_candidates(
                exchange=exchange,
                limit=70,
                candles=120,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "burst_sma_channel_1h_v1",
            "name": "\u7b56\u7565 5\uff5c\u7206\u767c\u6d41 SMA \u901a\u9053",
            "runner": lambda exchange: scan_burst_sma_channel_candidates(
                exchange=exchange,
                limit=70,
                candles=360,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "naked_k_reversal_1h_v1",
            "name": "\u7b56\u7565 6\uff5c\u88f8 K \u53cd\u8f49",
            "runner": lambda exchange: scan_naked_k_reversal_candidates(
                exchange=exchange,
                limit=70,
                candles=180,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "mean_reversion_atr_2h_daily_v1",
            "name": "\u7b56\u7565 7\uff5c\u5747\u503c\u56de\u6b78 ATR",
            "runner": lambda exchange: scan_mean_reversion_atr_candidates(
                exchange=exchange,
                limit=70,
                two_h_candles=160,
                daily_candles=60,
                sleep_sec=0.05,
            ),
        },
        {
            "strategy_id": "dual_sma_pullback_2h_v1",
            "name": "\u7b56\u7565 8\uff5c\u96d9\u5747\u7dda\u56de\u8e29",
            "runner": lambda exchange: scan_dual_sma_pullback_candidates(
                exchange=exchange,
                limit=70,
                candles=140,
                sleep_sec=0.05,
            ),
        },
    ]


def _best_candidates(result: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in list(result.get("ranked_candidates") or [])[: max(int(limit or 1), 1)]:
        rows.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "side": str(item.get("side") or ""),
                "timeframe": str(item.get("timeframe") or ""),
                "rr_ratio": float(item.get("rr_ratio") or 0.0),
                "win_rate": float(item.get("win_rate") or 0.0),
                "status": str(item.get("status") or ""),
            }
        )
    return rows


def _status_counts(result: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in list(result.get("candidates") or []):
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _summarize_scan(strategy_id: str, name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    ranked = list(result.get("ranked_candidates") or [])
    best = dict(ranked[0] or {}) if ranked else {}
    return {
        "strategy_id": strategy_id,
        "name": name,
        "phase": "scan",
        "status": "\u6383\u63cf\u5b8c\u6210" if result else "\u6383\u63cf\u5931\u6557",
        "symbol": str(best.get("symbol") or ""),
        "side": str(best.get("side") or ""),
        "timeframe": str(best.get("timeframe") or result.get("timeframe") or ""),
        "candidate_count": len(ranked),
        "status_counts": _status_counts(result),
        "top_candidates": _best_candidates(result, limit=3),
    }


def _summarize_trade_cycle(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in list(result.get("results") or []):
        strategy_id = str(item.get("strategy_id") or "")
        inner = dict(item.get("result") or {})
        phase = str(inner.get("phase") or "")
        payload = dict(inner.get("result") or {})
        candidate = dict(payload.get("candidate") or {})
        position = dict(payload.get("position") or {})
        rows.append(
            {
                "strategy_id": strategy_id,
                "phase": phase,
                "status": str(payload.get("status") or ""),
                "symbol": str(
                    payload.get("symbol")
                    or candidate.get("symbol")
                    or position.get("symbol")
                    or ""
                ),
                "side": str(
                    payload.get("side")
                    or candidate.get("side")
                    or position.get("side")
                    or ""
                ),
            }
        )
    return rows


def get_runner_snapshot() -> Dict[str, Any]:
    with _RUNNER_LOCK:
        state = dict(RUNNER_STATE)
        state["enabled"] = _runner_enabled()
        state["interval_sec"] = _interval_sec()
        state["mode"] = _mode_text()
        state["thread_alive"] = bool(_RUNNER_THREAD and _RUNNER_THREAD.is_alive())
        return state


def _runner_loop() -> None:
    while True:
        with _RUNNER_LOCK:
            RUNNER_STATE["enabled"] = _runner_enabled()
            RUNNER_STATE["running"] = True
            RUNNER_STATE["mode"] = _mode_text()
            RUNNER_STATE["interval_sec"] = _interval_sec()
            RUNNER_STATE["last_cycle_started_at"] = _utc_now_text()
            RUNNER_STATE["last_error"] = ""

        try:
            if not _has_okx_credentials():
                raise RuntimeError(
                    "\u7f3a\u5c11 OKX API \u74b0\u5883\u8b8a\u6578\uff0c\u7121\u6cd5\u555f\u52d5\u6383\u5e63\u8207\u81ea\u52d5\u4e0b\u55ae\u3002"
                )

            exchange = create_okx_exchange()
            scan_rows: List[Dict[str, Any]] = []

            if _scanner_enabled():
                for job in _scan_jobs():
                    try:
                        result = job["runner"](exchange)
                        scan_rows.append(
                            _summarize_scan(
                                strategy_id=job["strategy_id"],
                                name=job["name"],
                                result=result,
                            )
                        )
                    except Exception as exc:
                        scan_rows.append(
                            {
                                "strategy_id": job["strategy_id"],
                                "name": job["name"],
                                "phase": "scan",
                                "status": f"\u6383\u63cf\u5931\u6557\uff1a{exc}",
                                "symbol": "",
                                "side": "",
                                "timeframe": "",
                                "candidate_count": 0,
                                "status_counts": {},
                                "top_candidates": [],
                            }
                        )

            trade_rows: List[Dict[str, Any]] = []
            if _autotrader_enabled():
                trade_result = run_all_strategies(exchange=exchange)
                trade_rows = _summarize_trade_cycle(trade_result)

            with _RUNNER_LOCK:
                RUNNER_STATE["loop_count"] = int(RUNNER_STATE.get("loop_count") or 0) + 1
                RUNNER_STATE["last_results"] = scan_rows
                RUNNER_STATE["last_trade_results"] = trade_rows
                RUNNER_STATE["last_cycle_finished_at"] = _utc_now_text()
        except Exception as exc:
            with _RUNNER_LOCK:
                RUNNER_STATE["last_error"] = str(exc)
                RUNNER_STATE["last_cycle_finished_at"] = _utc_now_text()
        finally:
            with _RUNNER_LOCK:
                RUNNER_STATE["running"] = False
            time.sleep(_interval_sec())


def start_background_runner() -> Dict[str, Any]:
    global _RUNNER_THREAD

    with _RUNNER_LOCK:
        if RUNNER_STATE.get("started") and _RUNNER_THREAD and _RUNNER_THREAD.is_alive():
            return dict(RUNNER_STATE)

        RUNNER_STATE["enabled"] = _runner_enabled()
        RUNNER_STATE["interval_sec"] = _interval_sec()
        RUNNER_STATE["mode"] = _mode_text()

        if not RUNNER_STATE["enabled"]:
            return dict(RUNNER_STATE)

        thread = threading.Thread(
            target=_runner_loop,
            name="quant-background-runner",
            daemon=True,
        )
        thread.start()
        _RUNNER_THREAD = thread
        RUNNER_STATE["started"] = True
        RUNNER_STATE["running"] = bool(thread.is_alive())
        return dict(RUNNER_STATE)
