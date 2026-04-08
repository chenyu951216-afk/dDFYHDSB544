import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from strategy_orchestrator import run_all_strategies


RUNNER_STATE: Dict[str, Any] = {
    "enabled": False,
    "started": False,
    "running": False,
    "interval_sec": 60,
    "loop_count": 0,
    "last_cycle_started_at": "",
    "last_cycle_finished_at": "",
    "last_error": "",
    "last_results": [],
}

_RUNNER_LOCK = threading.Lock()


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


def _summarize_cycle(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in list(result.get("results") or []):
        strategy_id = str(item.get("strategy_id") or "")
        inner = dict(item.get("result") or {})
        phase = str(inner.get("phase") or "")
        payload = dict(inner.get("result") or {})
        status = str(payload.get("status") or "")
        rows.append(
            {
                "strategy_id": strategy_id,
                "phase": phase,
                "status": status,
                "symbol": str(payload.get("symbol") or payload.get("candidate", {}).get("symbol") or payload.get("position", {}).get("symbol") or ""),
                "side": str(payload.get("side") or payload.get("candidate", {}).get("side") or payload.get("position", {}).get("side") or ""),
            }
        )
    return rows


def runner_enabled() -> bool:
    return _truthy("ENABLE_AUTOTRADER", "false")


def get_runner_snapshot() -> Dict[str, Any]:
    with _RUNNER_LOCK:
        state = dict(RUNNER_STATE)
        state["enabled"] = runner_enabled()
        state["interval_sec"] = _interval_sec()
        return state


def _runner_loop() -> None:
    while True:
        with _RUNNER_LOCK:
            RUNNER_STATE["enabled"] = True
            RUNNER_STATE["running"] = True
            RUNNER_STATE["interval_sec"] = _interval_sec()
            RUNNER_STATE["last_cycle_started_at"] = _utc_now_text()
            RUNNER_STATE["last_error"] = ""

        try:
            result = run_all_strategies()
            rows = _summarize_cycle(result)
            with _RUNNER_LOCK:
                RUNNER_STATE["loop_count"] = int(RUNNER_STATE.get("loop_count") or 0) + 1
                RUNNER_STATE["last_results"] = rows
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
    with _RUNNER_LOCK:
        if RUNNER_STATE.get("started"):
            return dict(RUNNER_STATE)
        RUNNER_STATE["enabled"] = runner_enabled()
        RUNNER_STATE["interval_sec"] = _interval_sec()
        if not RUNNER_STATE["enabled"]:
            return dict(RUNNER_STATE)
        thread = threading.Thread(target=_runner_loop, name="quant-background-runner", daemon=True)
        thread.start()
        RUNNER_STATE["started"] = True
        return dict(RUNNER_STATE)
