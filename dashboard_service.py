import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

from learning_store import LEARNING_STATE, JOURNAL_PATH, get_ai_strategy_profile, get_strategy_rollup
from okx_force_order import create_okx_exchange, env_or_blank, fetch_total_equity_usdt
from strategy_portfolio import get_per_strategy_allocated_equity, get_strategy_slot_count
from strategy_registry import list_enabled_strategies
from strategy_runtime_state import list_active_positions, list_pending_entries


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "ui_dashboard.log")
CUSTOM_BG_PATH = os.path.join(BASE_DIR, "static", "images", "conan-custom.jpg")

LOGGER = logging.getLogger("dashboard_ui")
if not LOGGER.handlers:
    os.makedirs(LOG_DIR, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.propagate = False


STRATEGY_NAME_MAP = {
    "trend_hma_std_4h_v1": "\u7b56\u7565 1\uff5c\u9806\u52e2 HMA \u6a19\u6e96\u5dee",
    "larry_breakout_cmo_2h_4h_v1": "\u7b56\u7565 2\uff5cLarry \u7a81\u7834\u52d5\u80fd",
    "bollinger_width_4h_v1": "\u7b56\u7565 3\uff5c\u5e03\u6797\u901a\u9053 BBW",
    "ma_breakout_4h_v1": "\u7b56\u7565 4\uff5c\u5747\u7dda\u7a81\u7834\u639b\u55ae",
    "burst_sma_channel_1h_v1": "\u7b56\u7565 5\uff5c\u7206\u767c\u6d41 SMA \u901a\u9053",
    "naked_k_reversal_1h_v1": "\u7b56\u7565 6\uff5c\u88f8 K \u53cd\u8f49",
    "mean_reversion_atr_2h_daily_v1": "\u7b56\u7565 7\uff5c\u5747\u503c\u56de\u6b78 ATR",
    "dual_sma_pullback_2h_v1": "\u7b56\u7565 8\uff5c\u96d9\u5747\u7dda\u56de\u8e29",
    "ai_generated_meta_v1": "AI \u7b56\u7565\u683c\uff5c\u81ea\u751f\u6210\u6df7\u5408\u7b56\u7565",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _fmt_ts_ms(timestamp_ms: Any) -> str:
    ts = _safe_float(timestamp_ms)
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_text(active: Optional[Dict[str, Any]], pending: Optional[Dict[str, Any]]) -> str:
    if active:
        return "\u6301\u5009\u4e2d"
    if pending:
        return "\u7b49\u5f85\u89f8\u767c"
    return "\u7a7a\u5009"


def _read_recent_trades(limit: int = 20) -> List[Dict[str, Any]]:
    if not os.path.exists(JOURNAL_PATH):
        return []
    rows: List[Dict[str, Any]] = []
    with open(JOURNAL_PATH, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return list(reversed(rows[-max(limit, 1) :]))


def _tail_logs(limit: int = 150) -> List[str]:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as handle:
        lines = [line.rstrip("\n") for line in handle]
    return lines[-max(limit, 1) :]


def _latest_weekly_review() -> Optional[Dict[str, Any]]:
    weekly_reviews = dict(LEARNING_STATE.get("weekly_reviews") or {})
    if not weekly_reviews:
        return None
    latest_key = sorted(weekly_reviews.keys())[-1]
    payload = dict(weekly_reviews.get(latest_key) or {})
    payload["week_key"] = latest_key
    return payload


def _balance_snapshot() -> Dict[str, Any]:
    has_keys = bool(env_or_blank("OKX_API_KEY") and env_or_blank("OKX_SECRET") and env_or_blank("OKX_PASSWORD"))
    if not has_keys:
        return {
            "status": "\u672a\u8a2d\u5b9a API",
            "total_equity_usdt": 0.0,
            "free_usdt": 0.0,
            "used_usdt": 0.0,
            "allocated_equity_usdt": 0.0,
            "strategy_slot_count": get_strategy_slot_count(),
            "note": "\u5c1a\u672a\u5075\u6e2c\u5230 OKX API \u74b0\u5883\u8b8a\u6578\uff0c\u9918\u984d\u5340\u584a\u76ee\u524d\u53ea\u986f\u793a\u672c\u5730\u8cc7\u6599\u3002",
        }

    try:
        exchange = create_okx_exchange()
        balance = exchange.fetch_balance()
        total_equity = _safe_float(fetch_total_equity_usdt(exchange))
        free_usdt = _safe_float(((balance.get("free") or {}).get("USDT")))
        used_usdt = _safe_float(((balance.get("used") or {}).get("USDT")))
        capital_state = get_per_strategy_allocated_equity(exchange)
        return {
            "status": "\u5df2\u9023\u7dda",
            "total_equity_usdt": round(total_equity, 8),
            "free_usdt": round(free_usdt, 8),
            "used_usdt": round(used_usdt, 8),
            "allocated_equity_usdt": round(_safe_float(capital_state.get("allocated_equity_usdt")), 8),
            "strategy_slot_count": _safe_int(capital_state.get("strategy_slot_count"), 1),
            "note": "\u9019\u88e1\u986f\u793a\u7684\u662f OKX \u5373\u6642\u9918\u984d\u8207\u76ee\u524d\u5e73\u5747\u5206\u914d\u5f8c\u7684\u55ae\u7b56\u7565\u8cc7\u91d1\u6c60\u3002",
        }
    except Exception as exc:
        LOGGER.warning("\u6293\u53d6\u4ea4\u6613\u6240\u9918\u984d\u5931\u6557\uff1a%s", exc)
        return {
            "status": "\u8b80\u53d6\u5931\u6557",
            "total_equity_usdt": 0.0,
            "free_usdt": 0.0,
            "used_usdt": 0.0,
            "allocated_equity_usdt": 0.0,
            "strategy_slot_count": get_strategy_slot_count(),
            "note": f"\u4ea4\u6613\u6240\u9918\u984d\u8b80\u53d6\u5931\u6557\uff1a{exc}",
        }


def _try_refresh_live_price(symbol: str, fallback_mark: float) -> float:
    has_keys = bool(env_or_blank("OKX_API_KEY") and env_or_blank("OKX_SECRET") and env_or_blank("OKX_PASSWORD"))
    if not has_keys:
        return fallback_mark
    try:
        exchange = create_okx_exchange()
        ticker = exchange.fetch_ticker(symbol)
        return _safe_float(ticker.get("last"), fallback_mark)
    except Exception as exc:
        LOGGER.warning("\u66f4\u65b0 %s \u5373\u6642\u50f9\u683c\u5931\u6557\uff1a%s", symbol, exc)
        return fallback_mark


def _position_unrealized(position: Dict[str, Any], current_price: float) -> float:
    entry = _safe_float(position.get("entry_price"))
    qty = _safe_float(position.get("contracts"))
    side = str(position.get("side") or "")
    if entry <= 0 or qty <= 0 or current_price <= 0:
        return 0.0
    direction = 1.0 if side == "buy" else -1.0
    return round((current_price - entry) * qty * direction, 8)


def _movement_flags(position: Dict[str, Any]) -> Dict[str, Any]:
    scan = dict(position.get("scan_candidate") or {})
    origin_stop = _safe_float(
        position.get("fixed_stop_loss_price")
        or scan.get("stop_loss_price")
        or position.get("stop_loss_price")
    )
    origin_take = _safe_float(scan.get("take_profit_price") or position.get("take_profit_price"))
    current_stop = _safe_float(position.get("stop_loss_price"))
    current_take = _safe_float(position.get("take_profit_price"))
    eps_stop = max(abs(current_stop) * 0.0000001, 1e-8)
    eps_take = max(abs(current_take) * 0.0000001, 1e-8)
    return {
        "stop_moved": abs(current_stop - origin_stop) > eps_stop if origin_stop > 0 and current_stop > 0 else False,
        "take_moved": abs(current_take - origin_take) > eps_take if origin_take > 0 and current_take > 0 else False,
    }


def _strategy_cards() -> List[Dict[str, Any]]:
    enabled = list_enabled_strategies()
    active_positions = list_active_positions()
    pending_entries = list_pending_entries()
    cards: List[Dict[str, Any]] = []

    for spec in enabled:
        strategy_id = str(spec.get("strategy_id") or "")
        active = dict(active_positions.get(strategy_id) or {})
        pending = dict(pending_entries.get(strategy_id) or {})
        rollup = get_strategy_rollup(strategy_id)
        current_price = 0.0
        unrealized = 0.0
        movement = {"stop_moved": False, "take_moved": False}

        if active:
            current_price = _try_refresh_live_price(
                symbol=str(active.get("symbol") or ""),
                fallback_mark=_safe_float(active.get("mark_price")),
            )
            unrealized = _position_unrealized(active, current_price)
            movement = _movement_flags(active)

        side_value = str((active or pending or {}).get("side") or "")
        if side_value == "buy":
            side_text = "\u591a\u55ae"
        elif side_value:
            side_text = "\u7a7a\u55ae"
        else:
            side_text = ""

        cards.append(
            {
                "strategy_id": strategy_id,
                "name": STRATEGY_NAME_MAP.get(strategy_id, str(spec.get("name") or strategy_id)),
                "status_text": _status_text(active or None, pending or None),
                "timeframe": (active or pending or {}).get("timeframe") or "",
                "symbol": (active or pending or {}).get("symbol") or "",
                "side": side_text,
                "entry_price": round(_safe_float(active.get("entry_price")), 8),
                "current_price": round(current_price, 8),
                "stop_loss_price": round(_safe_float(active.get("stop_loss_price")), 8),
                "take_profit_price": round(_safe_float(active.get("take_profit_price")), 8),
                "stop_moved": movement["stop_moved"],
                "take_moved": movement["take_moved"],
                "rr_ratio": round(_safe_float((active or pending).get("rr_ratio")), 4) if (active or pending) else 0.0,
                "win_rate": round(_safe_float((active or pending).get("win_rate")) * 100.0, 2) if (active or pending) else 0.0,
                "contracts": round(_safe_float(active.get("contracts")), 8),
                "unrealized_pnl_usdt": round(unrealized, 8),
                "realized_pnl_usdt": round(_safe_float(rollup.get("net_pnl_usdt")), 8),
                "fees_usdt": round(_safe_float(rollup.get("fees_usdt")), 8),
                "trade_count": _safe_int(rollup.get("trades")),
                "win_rate_total": round(_safe_float(rollup.get("win_rate")) * 100.0, 2) if rollup.get("win_rate") is not None else 0.0,
                "avg_rr_ratio": round(_safe_float(rollup.get("avg_rr_ratio")), 4),
                "avg_leverage": round(_safe_float(rollup.get("avg_leverage")), 4),
                "avg_margin_usdt": round(_safe_float(rollup.get("avg_margin_usdt")), 4),
                "last_management_at": _fmt_ts_ms((active or pending).get("last_management_at_ms")),
                "opened_at": _fmt_ts_ms(active.get("opened_at_ms")),
                "pending_created_at": _fmt_ts_ms(pending.get("pending_created_at_ms")),
            }
        )
    return cards


def _summary(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_realized = round(sum(_safe_float(card.get("realized_pnl_usdt")) for card in cards), 8)
    total_unrealized = round(sum(_safe_float(card.get("unrealized_pnl_usdt")) for card in cards), 8)
    total_fees = round(sum(_safe_float(card.get("fees_usdt")) for card in cards), 8)
    total_trades = sum(_safe_int(card.get("trade_count")) for card in cards)
    holding_count = sum(1 for card in cards if str(card.get("status_text")) == "\u6301\u5009\u4e2d")
    pending_count = sum(1 for card in cards if str(card.get("status_text")) == "\u7b49\u5f85\u89f8\u767c")
    return {
        "total_realized_pnl_usdt": total_realized,
        "total_unrealized_pnl_usdt": total_unrealized,
        "total_combined_pnl_usdt": round(total_realized + total_unrealized, 8),
        "total_fees_usdt": total_fees,
        "total_trades": total_trades,
        "holding_count": holding_count,
        "pending_count": pending_count,
    }


def _ai_panel() -> Dict[str, Any]:
    profile = get_ai_strategy_profile() or {}
    weekly = _latest_weekly_review()
    strategy_reviews = list(((weekly or {}).get("review") or {}).get("strategy_reviews") or [])
    return {
        "ai_strategy_enabled": bool(profile.get("enabled")),
        "ai_strategy_profile": profile,
        "latest_weekly_review": weekly,
        "latest_strategy_reviews": strategy_reviews,
        "last_week_key": (weekly or {}).get("week_key") or "",
        "overall_observations": list(((weekly or {}).get("review") or {}).get("overall_observations") or []),
    }


def dashboard_snapshot() -> Dict[str, Any]:
    cards = _strategy_cards()
    summary = _summary(cards)
    balance = _balance_snapshot()
    recent_trades = _read_recent_trades(limit=16)
    ai_panel = _ai_panel()

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "background_has_custom_image": os.path.exists(CUSTOM_BG_PATH),
        "balance": balance,
        "summary": summary,
        "strategies": cards,
        "recent_trades": recent_trades,
        "ai_panel": ai_panel,
    }
    LOGGER.info(
        "\u5100\u8868\u677f\u5feb\u7167\u5df2\u6574\u7406\uff5c\u7b56\u7565\u6578=%s\uff5c\u6301\u5009=%s\uff5c\u7b49\u5f85=%s\uff5c\u7e3d\u5df2\u5be6\u73fe\u640d\u76ca=%.4f",
        len(cards),
        summary["holding_count"],
        summary["pending_count"],
        summary["total_realized_pnl_usdt"],
    )
    return payload


def dashboard_logs(limit: int = 150) -> Dict[str, Any]:
    rows = _tail_logs(limit=limit)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "rows": rows,
    }


def startup_message() -> None:
    LOGGER.info("\u4e2d\u6587\u76e3\u63a7\u5100\u8868\u677f\u5df2\u555f\u52d5\uff0c\u7b49\u5f85\u524d\u7aef\u8acb\u6c42\u3002")
