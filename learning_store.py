import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from learning_db import (
    delete_open_trade_record,
    init_learning_db,
    set_meta_value,
    upsert_ai_strategy_profile_record,
    upsert_closed_trade_record,
    upsert_open_trade_record,
    upsert_strategy_rollup_record,
    upsert_symbol_stat_record,
    upsert_weekly_review_record,
)
from strategy_registry import list_enabled_strategies


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "ai_learning_state.json")
JOURNAL_PATH = os.path.join(DATA_DIR, "trade_journal.jsonl")
AI_STRATEGY_ID = "ai_generated_meta_v1"

LEARNING_STATE: Dict[str, Any] = {
    "strategy_stats": {},
    "strategy_rollups": {},
    "open_trade_records": {},
    "weekly_reviews": {},
    "last_weekly_sync_week_key": None,
    "ai_strategy_profile": None,
}


def _default_symbol_stats() -> Dict[str, Any]:
    return {
        "wins": 0,
        "losses": 0,
        "trades": 0,
        "win_rate": None,
        "gross_pnl_usdt": 0.0,
        "net_pnl_usdt": 0.0,
        "fees_usdt": 0.0,
    }


def _default_rollup() -> Dict[str, Any]:
    return {
        "wins": 0,
        "losses": 0,
        "trades": 0,
        "win_rate": None,
        "gross_pnl_usdt": 0.0,
        "net_pnl_usdt": 0.0,
        "fees_usdt": 0.0,
        "avg_rr_ratio": 0.0,
        "avg_leverage": 0.0,
        "avg_margin_usdt": 0.0,
        "avg_hold_minutes": 0.0,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return dict(LEARNING_STATE)
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
            if not isinstance(payload, dict):
                return dict(LEARNING_STATE)
            merged = dict(LEARNING_STATE)
            merged.update(payload)
            merged.setdefault("strategy_stats", {})
            merged.setdefault("strategy_rollups", {})
            merged.setdefault("open_trade_records", {})
            merged.setdefault("weekly_reviews", {})
            merged.setdefault("last_weekly_sync_week_key", None)
            merged.setdefault("ai_strategy_profile", None)
            return merged
    except Exception:
        return dict(LEARNING_STATE)


def _save_state() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(LEARNING_STATE, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)


LEARNING_STATE.update(_load_state())
init_learning_db()


def _append_journal(entry: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(JOURNAL_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False))
        handle.write("\n")


def _extract_order_fee(order: Any) -> float:
    if not isinstance(order, dict):
        return 0.0
    fee = order.get("fee")
    if isinstance(fee, dict):
        value = _safe_float(fee.get("cost"))
        if value > 0:
            return value
    fees = order.get("fees")
    if isinstance(fees, list):
        total = sum(_safe_float((item or {}).get("cost")) for item in fees)
        if total > 0:
            return total
    info = order.get("info") or {}
    if isinstance(info, dict):
        for key in ("fee", "fillFee", "fill_fees", "feeCcy"):
            value = _safe_float(info.get(key))
            if value > 0:
                return value
    return 0.0


def _build_trade_id(position_payload: Dict[str, Any]) -> str:
    explicit = str(position_payload.get("trade_id") or "").strip()
    if explicit:
        return explicit
    strategy_id = str(position_payload.get("strategy_id") or "").strip()
    symbol = str(position_payload.get("symbol") or "").strip()
    side = str(position_payload.get("side") or "").strip()
    opened_at_ms = int(
        position_payload.get("opened_at_ms")
        or position_payload.get("entry_timestamp_ms")
        or position_payload.get("key_candle_timestamp_ms")
        or 0
    )
    return f"{strategy_id}:{symbol}:{side}:{opened_at_ms}"


def _ensure_trade_id(position_payload: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(position_payload or {})
    copied["trade_id"] = _build_trade_id(copied)
    return copied


def _update_stats_from_closed_trade(entry: Dict[str, Any]) -> None:
    strategy_id = str(entry.get("strategy_id") or "")
    symbol = str(entry.get("symbol") or "")
    strategy_map = LEARNING_STATE.setdefault("strategy_stats", {}).setdefault(strategy_id, {})
    symbol_stats = dict(strategy_map.get(symbol) or _default_symbol_stats())

    net_pnl = _safe_float(entry.get("net_pnl_usdt"))
    fees = _safe_float(entry.get("fees_usdt"))
    gross = _safe_float(entry.get("gross_pnl_usdt"))
    rr_ratio = _safe_float(entry.get("rr_ratio"))
    leverage = _safe_float(entry.get("leverage"))
    used_margin = _safe_float(entry.get("used_margin_usdt"))
    hold_minutes = _safe_float(entry.get("hold_minutes"))

    symbol_stats["trades"] = int(symbol_stats.get("trades", 0)) + 1
    if net_pnl >= 0:
        symbol_stats["wins"] = int(symbol_stats.get("wins", 0)) + 1
    else:
        symbol_stats["losses"] = int(symbol_stats.get("losses", 0)) + 1
    symbol_stats["gross_pnl_usdt"] = round(_safe_float(symbol_stats.get("gross_pnl_usdt")) + gross, 8)
    symbol_stats["net_pnl_usdt"] = round(_safe_float(symbol_stats.get("net_pnl_usdt")) + net_pnl, 8)
    symbol_stats["fees_usdt"] = round(_safe_float(symbol_stats.get("fees_usdt")) + fees, 8)
    total_trades = max(int(symbol_stats["trades"]), 1)
    symbol_stats["win_rate"] = round(float(symbol_stats["wins"]) / float(total_trades), 6)
    strategy_map[symbol] = symbol_stats
    upsert_symbol_stat_record(strategy_id=strategy_id, symbol=symbol, payload=symbol_stats)

    rollup = dict(LEARNING_STATE.setdefault("strategy_rollups", {}).get(strategy_id) or _default_rollup())
    previous_trades = int(rollup.get("trades", 0))
    rollup["trades"] = previous_trades + 1
    if net_pnl >= 0:
        rollup["wins"] = int(rollup.get("wins", 0)) + 1
    else:
        rollup["losses"] = int(rollup.get("losses", 0)) + 1
    rollup["gross_pnl_usdt"] = round(_safe_float(rollup.get("gross_pnl_usdt")) + gross, 8)
    rollup["net_pnl_usdt"] = round(_safe_float(rollup.get("net_pnl_usdt")) + net_pnl, 8)
    rollup["fees_usdt"] = round(_safe_float(rollup.get("fees_usdt")) + fees, 8)
    total_rollup_trades = max(int(rollup["trades"]), 1)
    rollup["win_rate"] = round(float(rollup["wins"]) / float(total_rollup_trades), 6)
    rollup["avg_rr_ratio"] = round(
        ((_safe_float(rollup.get("avg_rr_ratio")) * previous_trades) + rr_ratio) / float(total_rollup_trades),
        6,
    )
    rollup["avg_leverage"] = round(
        ((_safe_float(rollup.get("avg_leverage")) * previous_trades) + leverage) / float(total_rollup_trades),
        6,
    )
    rollup["avg_margin_usdt"] = round(
        ((_safe_float(rollup.get("avg_margin_usdt")) * previous_trades) + used_margin) / float(total_rollup_trades),
        6,
    )
    rollup["avg_hold_minutes"] = round(
        ((_safe_float(rollup.get("avg_hold_minutes")) * previous_trades) + hold_minutes) / float(total_rollup_trades),
        6,
    )
    LEARNING_STATE.setdefault("strategy_rollups", {})[strategy_id] = rollup
    upsert_strategy_rollup_record(strategy_id=strategy_id, payload=rollup)


def get_strategy_symbol_stats(strategy_id: str, symbol: str) -> Dict[str, Any]:
    strategy_map = LEARNING_STATE.setdefault("strategy_stats", {}).setdefault(str(strategy_id), {})
    return dict(strategy_map.get(str(symbol), _default_symbol_stats()))


def get_strategy_symbol_win_rate(strategy_id: str, symbol: str, default: float = 0.5) -> float:
    stats = get_strategy_symbol_stats(strategy_id=strategy_id, symbol=symbol)
    win_rate = stats.get("win_rate")
    if win_rate is None:
        return float(default)
    return _safe_float(win_rate, default=default)


def get_strategy_rollup(strategy_id: str) -> Dict[str, Any]:
    return dict(LEARNING_STATE.setdefault("strategy_rollups", {}).get(str(strategy_id), _default_rollup()))


def get_strategy_trade_count(strategy_id: str) -> int:
    return int(get_strategy_rollup(strategy_id).get("trades", 0))


def record_trade_open(position_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _ensure_trade_id(position_payload)
    trade_id = str(payload["trade_id"])
    open_records = LEARNING_STATE.setdefault("open_trade_records", {})
    if trade_id in open_records:
        return payload

    execution = dict(payload.get("execution") or {})
    plan = dict(execution.get("plan") or {})
    order = dict(execution.get("order") or {})
    open_entry = {
        "trade_id": trade_id,
        "strategy_id": str(payload.get("strategy_id") or ""),
        "source_strategy_id": str(payload.get("source_strategy_id") or payload.get("scan_candidate", {}).get("source_strategy_id") or ""),
        "symbol": str(payload.get("symbol") or ""),
        "side": str(payload.get("side") or ""),
        "timeframe": str(payload.get("timeframe") or ""),
        "entry_timestamp_ms": int(payload.get("opened_at_ms") or payload.get("entry_timestamp_ms") or int(time.time() * 1000)),
        "entry_price": round(_safe_float(payload.get("entry_price")), 8),
        "contracts": round(_safe_float(payload.get("contracts")), 8),
        "leverage": round(_safe_float((plan or {}).get("leverage") or payload.get("exchange_leverage") or payload.get("leverage")), 8),
        "used_margin_usdt": round(_safe_float((plan or {}).get("used_margin_usdt")), 8),
        "used_margin_pct": round(_safe_float((plan or {}).get("used_margin_pct")), 8),
        "selected_margin_pct": round(_safe_float((plan or {}).get("selected_margin_pct")), 8),
        "order_notional_usdt": round(_safe_float((plan or {}).get("order_notional_usdt")), 8),
        "estimated_risk_usdt": round(_safe_float((plan or {}).get("estimated_risk_usdt")), 8),
        "allocated_equity_usdt": round(
            _safe_float((plan or {}).get("allocated_equity_usdt") or (payload.get("capital_state") or {}).get("allocated_equity_usdt")),
            8,
        ),
        "fee_entry_usdt": round(_extract_order_fee(order), 8),
        "rr_ratio": round(_safe_float(payload.get("rr_ratio")), 8),
        "win_rate": round(_safe_float(payload.get("win_rate")), 8),
        "stop_loss_price": round(_safe_float(payload.get("stop_loss_price")), 8),
        "take_profit_price": round(_safe_float(payload.get("take_profit_price")), 8),
        "profit_arm_price": round(_safe_float(payload.get("profit_arm_price")), 8),
        "entry_reason": list((payload.get("scan_candidate") or {}).get("notes") or payload.get("notes") or []),
        "entry_indicator_snapshot": dict((payload.get("scan_candidate") or {}).get("indicator_snapshot") or payload.get("indicator_snapshot") or {}),
        "entry_learning_snapshot": dict((payload.get("scan_candidate") or {}).get("learning_snapshot") or {}),
        "execution_plan": dict(plan),
        "execution_order": dict(order),
        "position_snapshot": dict(payload),
    }
    open_records[trade_id] = open_entry
    upsert_open_trade_record(open_entry)
    _save_state()
    payload["trade_id"] = trade_id
    return payload


def record_trade_close(position_payload: Dict[str, Any], exit_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _ensure_trade_id(position_payload)
    trade_id = str(payload["trade_id"])
    open_records = LEARNING_STATE.setdefault("open_trade_records", {})
    open_entry = dict(open_records.pop(trade_id, {}) or {})
    if not open_entry:
        open_entry = {
            "trade_id": trade_id,
            "strategy_id": str(payload.get("strategy_id") or ""),
            "symbol": str(payload.get("symbol") or ""),
            "side": str(payload.get("side") or ""),
            "entry_timestamp_ms": int(payload.get("opened_at_ms") or payload.get("entry_timestamp_ms") or 0),
            "entry_price": _safe_float(payload.get("entry_price")),
            "contracts": _safe_float(payload.get("contracts")),
            "leverage": _safe_float(payload.get("exchange_leverage") or payload.get("leverage")),
            "used_margin_usdt": _safe_float((payload.get("execution") or {}).get("plan", {}).get("used_margin_usdt")),
            "fee_entry_usdt": _extract_order_fee((payload.get("execution") or {}).get("order")),
            "rr_ratio": _safe_float(payload.get("rr_ratio")),
            "position_snapshot": dict(payload),
        }

    side = str(open_entry.get("side") or payload.get("side") or "")
    qty = _safe_float(open_entry.get("contracts") or payload.get("contracts"))
    entry_price = _safe_float(open_entry.get("entry_price") or payload.get("entry_price"))
    exit_price = _safe_float(
        exit_payload.get("exit_price")
        or ((exit_payload.get("close_result") or {}).get("average"))
        or ((exit_payload.get("close_result") or {}).get("price"))
        or ((exit_payload.get("live_snapshot") or {}).get("current_open"))
        or ((exit_payload.get("live_snapshot") or {}).get("current_close"))
        or payload.get("mark_price"),
    )
    exit_timestamp_ms = int(
        exit_payload.get("exit_timestamp_ms")
        or ((exit_payload.get("live_snapshot") or {}).get("current_bar_timestamp_ms"))
        or int(time.time() * 1000)
    )
    fee_exit = _extract_order_fee(exit_payload.get("close_result"))
    direction = 1.0 if side == "buy" else -1.0
    gross_pnl = (exit_price - entry_price) * qty * direction
    fees = _safe_float(open_entry.get("fee_entry_usdt")) + fee_exit
    net_pnl = gross_pnl - fees
    hold_minutes = 0.0
    entry_timestamp_ms = int(open_entry.get("entry_timestamp_ms") or 0)
    if entry_timestamp_ms > 0 and exit_timestamp_ms > entry_timestamp_ms:
        hold_minutes = (exit_timestamp_ms - entry_timestamp_ms) / 60000.0

    closed_entry = {
        **open_entry,
        "status": "closed",
        "exit_reason": str(exit_payload.get("exit_reason") or "closed"),
        "exit_timestamp_ms": exit_timestamp_ms,
        "exit_price": round(float(exit_price), 8),
        "fee_exit_usdt": round(float(fee_exit), 8),
        "fees_usdt": round(float(fees), 8),
        "gross_pnl_usdt": round(float(gross_pnl), 8),
        "net_pnl_usdt": round(float(net_pnl), 8),
        "net_pnl_pct_on_margin": round(
            (net_pnl / max(_safe_float(open_entry.get("used_margin_usdt")), 1e-12)) * 100.0,
            8,
        ),
        "hold_minutes": round(float(hold_minutes), 4),
        "close_result": dict(exit_payload.get("close_result") or {}),
        "exit_live_snapshot": dict(exit_payload.get("live_snapshot") or {}),
        "auto_reversed_to_side": str(exit_payload.get("auto_reversed_to_side") or ""),
    }
    _append_journal(closed_entry)
    delete_open_trade_record(trade_id)
    upsert_closed_trade_record(closed_entry)
    _update_stats_from_closed_trade(closed_entry)
    _save_state()
    return closed_entry


def get_ai_strategy_profile() -> Optional[Dict[str, Any]]:
    profile = LEARNING_STATE.get("ai_strategy_profile")
    return dict(profile) if isinstance(profile, dict) else None


def set_ai_strategy_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    LEARNING_STATE["ai_strategy_profile"] = dict(profile or {})
    upsert_ai_strategy_profile_record(LEARNING_STATE["ai_strategy_profile"])
    _save_state()
    return dict(LEARNING_STATE["ai_strategy_profile"])


def apply_ai_strategy_patch(patch: Dict[str, Any], source_week_key: Optional[str] = None) -> Dict[str, Any]:
    profile = get_ai_strategy_profile() or {}
    updated = dict(profile)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(updated.get(key), dict):
            merged = dict(updated[key])
            merged.update(value)
            updated[key] = merged
        else:
            updated[key] = value
    if source_week_key:
        updated["last_auto_applied_week_key"] = str(source_week_key)
    return set_ai_strategy_profile(updated)


def _base_strategy_ids() -> List[str]:
    enabled = [item for item in list_enabled_strategies() if str(item.get("strategy_id") or "") != AI_STRATEGY_ID]
    return [str(item.get("strategy_id") or "") for item in enabled if str(item.get("strategy_id") or "")]


def ensure_ai_strategy_profile(min_trades_per_strategy: int = 30) -> Dict[str, Any]:
    existing = get_ai_strategy_profile()
    if existing and bool(existing.get("enabled")):
        return {"status": "already_enabled", "profile": existing}

    base_strategy_ids = _base_strategy_ids()
    if not base_strategy_ids:
        return {"status": "no_base_strategies"}

    trade_counts = {strategy_id: get_strategy_trade_count(strategy_id) for strategy_id in base_strategy_ids}
    if any(count < int(min_trades_per_strategy) for count in trade_counts.values()):
        return {"status": "not_ready", "trade_counts": trade_counts, "required": int(min_trades_per_strategy)}

    weights: Dict[str, float] = {}
    total = 0.0
    for strategy_id in base_strategy_ids:
        rollup = get_strategy_rollup(strategy_id)
        expectancy = _safe_float(rollup.get("net_pnl_usdt")) / max(int(rollup.get("trades") or 1), 1)
        score = max(expectancy, 0.0) + (_safe_float(rollup.get("win_rate")) * 100.0)
        score = max(score, 0.01)
        weights[strategy_id] = score
        total += score

    normalized = {
        key: round(value / total, 6) if total > 0 else round(1.0 / max(len(weights), 1), 6)
        for key, value in weights.items()
    }
    profile = {
        "strategy_id": AI_STRATEGY_ID,
        "enabled": True,
        "profile_version": 1,
        "created_at_ms": int(time.time() * 1000),
        "min_trades_per_strategy": int(min_trades_per_strategy),
        "source_strategy_ids": list(base_strategy_ids),
        "source_strategy_weights": dict(normalized),
        "scoring_weights": {
            "candidate_rr": 1.0,
            "candidate_win_rate": 1.0,
            "source_win_rate": 0.5,
        },
        "min_candidate_rr": 0.2,
        "universe_limit": 35,
        "notes": [
            "AI meta strategy is created only after every enabled human strategy has at least 30 closed trades.",
            "This strategy selects and executes from other strategy candidates using learned source weights.",
            "Weekly OpenAI suggestions can directly update this profile.",
        ],
        "source_trade_counts": dict(trade_counts),
        "auto_apply_suggestions": True,
    }
    return {"status": "created", "profile": set_ai_strategy_profile(profile)}


def _current_week_key(now_utc: Optional[datetime] = None) -> str:
    now = now_utc or datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def build_weekly_summary(now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    week_key = _current_week_key(now)
    window_start = now - timedelta(days=7)
    trades: List[Dict[str, Any]] = []
    if os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                exit_ms = _safe_float(entry.get("exit_timestamp_ms"))
                if exit_ms <= 0:
                    continue
                exit_dt = datetime.fromtimestamp(exit_ms / 1000.0, tz=timezone.utc)
                if exit_dt >= window_start and exit_dt <= now:
                    trades.append(entry)

    by_strategy: Dict[str, Dict[str, Any]] = {}
    for entry in trades:
        strategy_id = str(entry.get("strategy_id") or "")
        item = by_strategy.setdefault(
            strategy_id,
            {
                "strategy_id": strategy_id,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "gross_pnl_usdt": 0.0,
                "net_pnl_usdt": 0.0,
                "fees_usdt": 0.0,
                "avg_rr_ratio": 0.0,
                "avg_leverage": 0.0,
                "avg_margin_usdt": 0.0,
                "top_symbols": {},
                "recent_examples": [],
            },
        )
        count = int(item["trades"])
        item["trades"] = count + 1
        if _safe_float(entry.get("net_pnl_usdt")) >= 0:
            item["wins"] = int(item["wins"]) + 1
        else:
            item["losses"] = int(item["losses"]) + 1
        item["gross_pnl_usdt"] = round(_safe_float(item["gross_pnl_usdt"]) + _safe_float(entry.get("gross_pnl_usdt")), 8)
        item["net_pnl_usdt"] = round(_safe_float(item["net_pnl_usdt"]) + _safe_float(entry.get("net_pnl_usdt")), 8)
        item["fees_usdt"] = round(_safe_float(item["fees_usdt"]) + _safe_float(entry.get("fees_usdt")), 8)
        item["avg_rr_ratio"] = round(((_safe_float(item["avg_rr_ratio"]) * count) + _safe_float(entry.get("rr_ratio"))) / float(item["trades"]), 6)
        item["avg_leverage"] = round(((_safe_float(item["avg_leverage"]) * count) + _safe_float(entry.get("leverage"))) / float(item["trades"]), 6)
        item["avg_margin_usdt"] = round(((_safe_float(item["avg_margin_usdt"]) * count) + _safe_float(entry.get("used_margin_usdt"))) / float(item["trades"]), 6)
        symbol = str(entry.get("symbol") or "")
        item["top_symbols"][symbol] = item["top_symbols"].get(symbol, 0) + 1
        if len(item["recent_examples"]) < 3:
            item["recent_examples"].append(
                {
                    "symbol": symbol,
                    "side": entry.get("side"),
                    "net_pnl_usdt": entry.get("net_pnl_usdt"),
                    "exit_reason": entry.get("exit_reason"),
                    "rr_ratio": entry.get("rr_ratio"),
                }
            )

    strategy_summaries: List[Dict[str, Any]] = []
    for strategy_id, item in by_strategy.items():
        trades_count = max(int(item["trades"]), 1)
        strategy_summaries.append(
            {
                **item,
                "win_rate": round(float(item["wins"]) / float(trades_count), 6),
                "top_symbols": sorted(item["top_symbols"].items(), key=lambda kv: kv[1], reverse=True)[:5],
            }
        )

    strategy_summaries.sort(key=lambda row: _safe_float(row.get("net_pnl_usdt")), reverse=True)
    return {
        "week_key": week_key,
        "generated_at_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "window_start_utc": window_start.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "window_end_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_closed_trades": len(trades),
        "strategies": strategy_summaries,
        "ai_strategy_profile": get_ai_strategy_profile(),
    }


def save_weekly_ai_review(review_payload: Dict[str, Any]) -> Dict[str, Any]:
    week_key = str(review_payload.get("week_key") or _current_week_key())
    weekly_reviews = LEARNING_STATE.setdefault("weekly_reviews", {})
    weekly_reviews[week_key] = dict(review_payload or {})
    LEARNING_STATE["last_weekly_sync_week_key"] = week_key
    upsert_weekly_review_record(week_key=week_key, payload=weekly_reviews[week_key])
    set_meta_value("last_weekly_sync_week_key", week_key)
    _save_state()
    return dict(weekly_reviews[week_key])


def get_last_weekly_sync_week_key() -> Optional[str]:
    value = LEARNING_STATE.get("last_weekly_sync_week_key")
    return str(value) if value else None


def should_run_weekly_sync(now_utc: Optional[datetime] = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    if now.weekday() != 6:
        return False
    week_key = _current_week_key(now)
    return get_last_weekly_sync_week_key() != week_key


def sync_learning_state_to_db() -> None:
    for trade_id, payload in (LEARNING_STATE.get("open_trade_records") or {}).items():
        item = dict(payload or {})
        if not item.get("trade_id"):
            item["trade_id"] = str(trade_id)
        upsert_open_trade_record(item)

    for strategy_id, rollup in (LEARNING_STATE.get("strategy_rollups") or {}).items():
        upsert_strategy_rollup_record(strategy_id=strategy_id, payload=dict(rollup or {}))

    for strategy_id, symbol_map in (LEARNING_STATE.get("strategy_stats") or {}).items():
        for symbol, stats in (symbol_map or {}).items():
            upsert_symbol_stat_record(strategy_id=strategy_id, symbol=symbol, payload=dict(stats or {}))

    for week_key, payload in (LEARNING_STATE.get("weekly_reviews") or {}).items():
        upsert_weekly_review_record(week_key=str(week_key), payload=dict(payload or {}))

    profile = LEARNING_STATE.get("ai_strategy_profile")
    if isinstance(profile, dict):
        upsert_ai_strategy_profile_record(profile)

    last_week_key = LEARNING_STATE.get("last_weekly_sync_week_key")
    if last_week_key:
        set_meta_value("last_weekly_sync_week_key", str(last_week_key))

    if os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if isinstance(entry, dict) and entry.get("trade_id"):
                    upsert_closed_trade_record(entry)


sync_learning_state_to_db()
