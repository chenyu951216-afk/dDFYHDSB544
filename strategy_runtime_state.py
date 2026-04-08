import json
import os
from typing import Any, Dict, Optional

from learning_store import record_trade_close, record_trade_open

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
STATE_PATH = os.path.join(STATE_DIR, "strategy_runtime_state.json")

STRATEGY_RUNTIME_STATE: Dict[str, Dict[str, Any]] = {
    "active_positions": {},
    "pending_entries": {},
}


def _load_state() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(STATE_PATH):
        return {"active_positions": {}, "pending_entries": {}}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if not isinstance(data, dict):
                return {"active_positions": {}, "pending_entries": {}}
            data.setdefault("active_positions", {})
            data.setdefault("pending_entries", {})
            return data
    except Exception:
        return {"active_positions": {}, "pending_entries": {}}


def _save_state() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(STRATEGY_RUNTIME_STATE, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)


STRATEGY_RUNTIME_STATE.update(_load_state())


def get_strategy_active_position(strategy_id: str) -> Optional[Dict[str, Any]]:
    return STRATEGY_RUNTIME_STATE.setdefault("active_positions", {}).get(str(strategy_id))


def has_strategy_active_position(strategy_id: str) -> bool:
    return get_strategy_active_position(strategy_id) is not None


def set_strategy_active_position(strategy_id: str, position_payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(position_payload or {})
    existing = STRATEGY_RUNTIME_STATE.setdefault("active_positions", {}).get(str(strategy_id))
    if existing is None and data.get("entry_price") is not None and data.get("symbol"):
        data = record_trade_open(data)
    STRATEGY_RUNTIME_STATE.setdefault("active_positions", {})[str(strategy_id)] = data
    _save_state()
    return data


def clear_strategy_active_position(strategy_id: str) -> Optional[Dict[str, Any]]:
    value = STRATEGY_RUNTIME_STATE.setdefault("active_positions", {}).pop(str(strategy_id), None)
    _save_state()
    return value


def close_strategy_trade(strategy_id: str, exit_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    value = STRATEGY_RUNTIME_STATE.setdefault("active_positions", {}).pop(str(strategy_id), None)
    if value is not None:
        record_trade_close(value, exit_payload or {})
    _save_state()
    return value


def get_strategy_pending_entry(strategy_id: str) -> Optional[Dict[str, Any]]:
    return STRATEGY_RUNTIME_STATE.setdefault("pending_entries", {}).get(str(strategy_id))


def has_strategy_pending_entry(strategy_id: str) -> bool:
    return get_strategy_pending_entry(strategy_id) is not None


def set_strategy_pending_entry(strategy_id: str, pending_payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(pending_payload or {})
    STRATEGY_RUNTIME_STATE.setdefault("pending_entries", {})[str(strategy_id)] = data
    _save_state()
    return data


def clear_strategy_pending_entry(strategy_id: str) -> Optional[Dict[str, Any]]:
    value = STRATEGY_RUNTIME_STATE.setdefault("pending_entries", {}).pop(str(strategy_id), None)
    _save_state()
    return value


def list_active_positions() -> Dict[str, Dict[str, Any]]:
    return {
        str(strategy_id): dict(payload or {})
        for strategy_id, payload in STRATEGY_RUNTIME_STATE.setdefault("active_positions", {}).items()
    }


def list_pending_entries() -> Dict[str, Dict[str, Any]]:
    return {
        str(strategy_id): dict(payload or {})
        for strategy_id, payload in STRATEGY_RUNTIME_STATE.setdefault("pending_entries", {}).items()
    }


def list_active_symbols(exclude_strategy_id: Optional[str] = None) -> Dict[str, str]:
    active_symbols: Dict[str, str] = {}
    for strategy_id, payload in STRATEGY_RUNTIME_STATE.setdefault("active_positions", {}).items():
        if exclude_strategy_id is not None and str(strategy_id) == str(exclude_strategy_id):
            continue
        symbol = str((payload or {}).get("symbol") or "").strip()
        if symbol:
            active_symbols[symbol] = str(strategy_id)
    for strategy_id, payload in STRATEGY_RUNTIME_STATE.setdefault("pending_entries", {}).items():
        if exclude_strategy_id is not None and str(strategy_id) == str(exclude_strategy_id):
            continue
        symbol = str((payload or {}).get("symbol") or "").strip()
        if symbol and symbol not in active_symbols:
            active_symbols[symbol] = str(strategy_id)
    return active_symbols
