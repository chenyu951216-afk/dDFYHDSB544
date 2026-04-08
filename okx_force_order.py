import os
import time
from typing import Any, Dict, List, Optional, Tuple

import ccxt

DEFAULT_RISK_PCT = 0.01
DEFAULT_MARGIN_PCT = 0.04
MIN_MARGIN_PCT = 0.01
MAX_MARGIN_PCT = 0.08


def env_or_blank(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def create_okx_exchange() -> ccxt.okx:
    config: Dict[str, Any] = {
        "apiKey": env_or_blank("OKX_API_KEY"),
        "secret": env_or_blank("OKX_SECRET"),
        "password": env_or_blank("OKX_PASSWORD"),
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        },
    }
    exchange = ccxt.okx(config)
    exchange.timeout = 15000
    exchange.enableRateLimit = True
    return exchange


def normalize_side(side: str) -> str:
    value = str(side or "").lower().strip()
    if value in ("buy", "long"):
        return "buy"
    if value in ("sell", "short"):
        return "sell"
    raise ValueError(f"unsupported side: {side}")


def normalize_pos_side(side: str) -> str:
    return "long" if normalize_side(side) == "buy" else "short"


def normalize_td_mode(td_mode: str) -> str:
    value = str(td_mode or "cross").lower().strip()
    if value not in ("cross", "isolated"):
        raise ValueError(f"unsupported tdMode: {td_mode}")
    return value


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(float(low), min(float(high), float(value)))


def normalize_client_order_id(client_order_id: Optional[str]) -> str:
    raw = str(client_order_id or "").strip().lower()
    if not raw:
        raw = f"cx{int(time.time() * 1000)}"
    cleaned = "".join(ch for ch in raw if ch.isalnum())
    if not cleaned:
        cleaned = f"cx{int(time.time() * 1000)}"
    if not cleaned.startswith("cx"):
        cleaned = f"cx{cleaned}"
    return cleaned[:32]


def fetch_total_equity_usdt(exchange: ccxt.okx) -> float:
    balance = exchange.fetch_balance()

    candidates = [
        ((balance.get("total") or {}).get("USDT")),
        ((balance.get("free") or {}).get("USDT")),
        (((balance.get("USDT") or {}).get("total")) if isinstance(balance.get("USDT"), dict) else None),
        (((balance.get("USDT") or {}).get("free")) if isinstance(balance.get("USDT"), dict) else None),
    ]
    for candidate in candidates:
        value = _safe_float(candidate)
        if value > 0:
            return value

    info = balance.get("info") or {}
    detail_list = []
    if isinstance(info, dict):
        detail_list = info.get("data") or info.get("details") or []
        if isinstance(detail_list, dict):
            detail_list = [detail_list]
    for item in detail_list or []:
        if str(item.get("ccy") or "").upper() != "USDT":
            continue
        for key in ("eq", "cashBal", "availEq", "availBal"):
            value = _safe_float(item.get(key))
            if value > 0:
                return value

    return 0.0


def get_market(exchange: ccxt.okx, symbol: str) -> Dict[str, Any]:
    markets = exchange.load_markets()
    return dict(markets.get(symbol) or {})


def fetch_symbol_max_leverage(
    exchange: ccxt.okx,
    symbol: str,
    fallback: int = 10,
) -> int:
    market = get_market(exchange=exchange, symbol=symbol)
    candidates: List[float] = []

    limits = market.get("limits") or {}
    leverage_limit = (limits.get("leverage") or {}).get("max")
    if leverage_limit is not None:
        candidates.append(_safe_float(leverage_limit))

    info = market.get("info") or {}
    if isinstance(info, dict):
        for key in ("maxLeverage", "maxLever", "lever", "leverMax"):
            value = _safe_float(info.get(key))
            if value > 0:
                candidates.append(value)

    try:
        tiers = exchange.fetch_leverage_tiers([symbol])
    except Exception:
        tiers = {}
    symbol_tiers = tiers.get(symbol) if isinstance(tiers, dict) else None
    if isinstance(symbol_tiers, list):
        for item in symbol_tiers:
            value = _safe_float((item or {}).get("maxLeverage"))
            if value > 0:
                candidates.append(value)

    valid = [int(value) for value in candidates if float(value) > 0]
    if valid:
        return max(valid)
    return max(int(fallback or 1), 1)


def resolve_safe_leverage(
    exchange: ccxt.okx,
    symbol: str,
    entry_price: float,
    stop_price: float,
    requested_leverage: int,
    liquidation_buffer: float = 1.25,
) -> Dict[str, Any]:
    requested = max(int(requested_leverage or 1), 1)
    symbol_max = fetch_symbol_max_leverage(
        exchange=exchange,
        symbol=symbol,
        fallback=requested,
    )

    entry = float(entry_price)
    stop = float(stop_price)
    stop_distance_pct = abs(entry - stop) / max(abs(entry), 1e-12)
    if stop_distance_pct <= 0:
        by_stop = min(requested, symbol_max)
    else:
        safe_upper = (1.0 / stop_distance_pct) / max(float(liquidation_buffer), 1.0)
        by_stop = max(int(safe_upper), 1)

    selected = max(1, min(requested, symbol_max, by_stop))
    return {
        "requested_leverage": int(requested),
        "symbol_max_leverage": int(symbol_max),
        "stop_distance_pct": round(float(stop_distance_pct), 8),
        "liquidation_buffer": float(liquidation_buffer),
        "safe_leverage_by_stop": int(max(by_stop, 1)),
        "selected_leverage": int(selected),
        "note": "Liquidation safety is approximated conservatively from stop distance and symbol max leverage, not exact exchange liquidation math.",
    }


def set_symbol_leverage(
    exchange: ccxt.okx,
    symbol: str,
    leverage: int,
    td_mode: str = "cross",
    side: Optional[str] = None,
) -> Dict[str, Any]:
    td_mode = normalize_td_mode(td_mode)
    leverage = max(int(leverage or 1), 1)
    params: Dict[str, Any] = {"mgnMode": td_mode}
    if side:
        params["posSide"] = normalize_pos_side(side)

    last_error: Optional[Exception] = None
    attempts = [params]
    if "posSide" in params:
        attempts.append({"mgnMode": td_mode})

    for item in attempts:
        try:
            return exchange.set_leverage(leverage, symbol, item)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"failed to set leverage for {symbol}: {last_error}")


def compute_order_size(
    exchange: ccxt.okx,
    symbol: str,
    entry_price: float,
    stop_price: float,
    equity: float,
    leverage: int,
    margin_pct: Optional[float] = None,
    risk_pct: float = DEFAULT_RISK_PCT,
    min_margin_pct: float = MIN_MARGIN_PCT,
    max_margin_pct: float = MAX_MARGIN_PCT,
    min_order_margin_usdt: float = 1.0,
) -> Dict[str, float]:
    entry_price = float(entry_price)
    stop_price = float(stop_price)
    equity = float(equity)
    leverage = max(int(leverage or 1), 1)

    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    if equity <= 0:
        raise ValueError("equity must be > 0")

    stop_distance = abs(entry_price - stop_price)
    selected_margin_pct = float(
        margin_pct if margin_pct is not None else DEFAULT_MARGIN_PCT
    )
    selected_margin_pct = clamp(selected_margin_pct, min_margin_pct, max_margin_pct)
    min_margin_usdt = max(equity * float(min_margin_pct), float(min_order_margin_usdt))
    target_margin_usdt = max(equity * selected_margin_pct, min_margin_usdt)
    risk_budget_usdt = max(equity * float(risk_pct), float(min_order_margin_usdt))

    qty_by_target_margin = target_margin_usdt * leverage / entry_price
    qty_by_floor_margin = min_margin_usdt * leverage / entry_price

    if stop_distance > 0:
        qty_by_risk = risk_budget_usdt / stop_distance
        raw_qty = min(qty_by_risk, qty_by_target_margin)
    else:
        qty_by_risk = 0.0
        raw_qty = qty_by_target_margin

    raw_qty = max(raw_qty, qty_by_floor_margin)

    try:
        qty = float(exchange.amount_to_precision(symbol, raw_qty))
    except Exception:
        qty = raw_qty

    qty = max(float(qty), 0.0)
    used_margin_usdt = qty * entry_price / leverage if leverage > 0 else qty * entry_price
    used_margin_pct = used_margin_usdt / equity if equity > 0 else selected_margin_pct
    used_margin_pct = clamp(used_margin_pct, min_margin_pct, max_margin_pct)
    order_notional_usdt = qty * entry_price
    est_risk_usdt = qty * stop_distance

    return {
        "qty": round(qty, 8),
        "entry_price": round(entry_price, 8),
        "stop_price": round(stop_price, 8),
        "stop_distance": round(stop_distance, 8),
        "order_notional_usdt": round(order_notional_usdt, 4),
        "used_margin_usdt": round(used_margin_usdt, 4),
        "used_margin_pct": round(used_margin_pct, 4),
        "selected_margin_pct": round(selected_margin_pct, 4),
        "risk_budget_usdt": round(risk_budget_usdt, 4),
        "estimated_risk_usdt": round(est_risk_usdt, 4),
        "leverage": float(leverage),
        "qty_by_risk": round(qty_by_risk, 8),
        "qty_by_target_margin": round(qty_by_target_margin, 8),
        "qty_by_floor_margin": round(qty_by_floor_margin, 8),
    }


def build_forced_order_plan(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    stop_loss_price: float,
    take_profit_price: float,
    leverage: int = 10,
    td_mode: str = "cross",
    equity: Optional[float] = None,
    entry_price: Optional[float] = None,
    margin_pct: Optional[float] = DEFAULT_MARGIN_PCT,
    risk_pct: float = DEFAULT_RISK_PCT,
    min_margin_pct: float = MIN_MARGIN_PCT,
    max_margin_pct: float = MAX_MARGIN_PCT,
) -> Dict[str, Any]:
    del td_mode  # Reserved for caller consistency.
    del side
    entry = float(entry_price) if entry_price is not None else 0.0
    if entry <= 0:
        ticker = exchange.fetch_ticker(symbol)
        entry = _safe_float(ticker.get("last"))
    if entry <= 0:
        raise RuntimeError(f"failed to get entry price for {symbol}")

    available_equity = float(equity) if equity is not None else 0.0
    if available_equity <= 0:
        available_equity = fetch_total_equity_usdt(exchange)
    if available_equity <= 0:
        raise RuntimeError("failed to resolve account equity")

    leverage_state = resolve_safe_leverage(
        exchange=exchange,
        symbol=symbol,
        entry_price=entry,
        stop_price=float(stop_loss_price),
        requested_leverage=leverage,
    )
    leverage_to_use = int(leverage_state["selected_leverage"])

    size = compute_order_size(
        exchange=exchange,
        symbol=symbol,
        entry_price=entry,
        stop_price=float(stop_loss_price),
        equity=available_equity,
        leverage=leverage_to_use,
        margin_pct=margin_pct,
        risk_pct=risk_pct,
        min_margin_pct=min_margin_pct,
        max_margin_pct=max_margin_pct,
    )
    size["take_profit_price"] = round(float(take_profit_price), 8)
    size["symbol"] = symbol
    size["allocated_equity_usdt"] = round(float(available_equity), 8)
    size["leverage"] = float(leverage_to_use)
    size["leverage_state"] = dict(leverage_state)
    return size


def _find_algo_matches(
    algo_orders: Any,
    close_side: str,
    sl_price: float,
    tp_price: float,
) -> Tuple[bool, bool]:
    sl_ok = False
    tp_ok = False
    sl_keys = ("sltriggerpx", "stoplossprice", "triggerprice")
    tp_keys = ("tptriggerpx", "takeprofitprice", "triggerprice")

    for order in algo_orders or []:
        raw = (order.get("info") or {}) if isinstance(order, dict) else {}
        side = str((order.get("side") if isinstance(order, dict) else "") or raw.get("side") or "").lower()
        if side and side != close_side:
            continue

        payload = {}
        if isinstance(order, dict):
            for key, value in order.items():
                payload[str(key).lower()] = value
        if isinstance(raw, dict):
            for key, value in raw.items():
                payload[str(key).lower()] = value

        stop_marker = " ".join(str(payload.get(k, "")).lower() for k in ("ordtype", "type", "algoordtype"))
        for key in sl_keys:
            candidate = _safe_float(payload.get(key))
            if candidate and abs(candidate - sl_price) <= max(sl_price * 0.0005, 1e-8):
                if "tp" not in stop_marker:
                    sl_ok = True
        for key in tp_keys:
            candidate = _safe_float(payload.get(key))
            if candidate and abs(candidate - tp_price) <= max(tp_price * 0.0005, 1e-8):
                if "sl" not in stop_marker:
                    tp_ok = True

    return sl_ok, tp_ok


def fetch_open_algo_orders(exchange: ccxt.okx, symbol: str) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen_ids = set()
    for params in ({"stop": True}, {"trigger": True}):
        try:
            orders = exchange.fetch_open_orders(symbol, params=params)
        except Exception:
            orders = []
        for order in orders or []:
            order_id = str((order or {}).get("id") or ((order or {}).get("info") or {}).get("algoId") or "")
            dedupe_key = order_id or repr(order)
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            merged.append(order)
    return merged


def _normalize_order_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    raw = (order or {}).get("info") or {}
    for source in (order or {}, raw if isinstance(raw, dict) else {}):
        if isinstance(source, dict):
            for key, value in source.items():
                payload[str(key).lower()] = value
    return payload


def list_protection_orders(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    order_kind: str = "all",
) -> List[Dict[str, Any]]:
    close_side = "sell" if normalize_side(side) == "buy" else "buy"
    pos_side = normalize_pos_side(side)
    result: List[Dict[str, Any]] = []

    for order in fetch_open_algo_orders(exchange=exchange, symbol=symbol):
        payload = _normalize_order_payload(order)
        order_side = str(payload.get("side") or "").lower()
        order_pos_side = str(payload.get("posside") or "").lower()
        if order_side and order_side != close_side:
            continue
        if order_pos_side and order_pos_side != pos_side:
            continue

        type_marker = " ".join(
            str(payload.get(key) or "").lower()
            for key in ("ordtype", "type", "algoordtype")
        )
        has_sl = any(payload.get(key) is not None for key in ("sltriggerpx", "stoplossprice"))
        has_tp = any(payload.get(key) is not None for key in ("tptriggerpx", "takeprofitprice"))
        guessed_kind = "unknown"
        if has_sl or "sl" in type_marker or "stop" in type_marker:
            guessed_kind = "sl"
        if has_tp or "tp" in type_marker or "takeprofit" in type_marker:
            guessed_kind = "tp" if guessed_kind == "unknown" else "both"

        if order_kind == "all":
            result.append(order)
        elif order_kind == "sl" and guessed_kind in ("sl", "both"):
            result.append(order)
        elif order_kind == "tp" and guessed_kind in ("tp", "both"):
            result.append(order)
    return result


def cancel_orders(exchange: ccxt.okx, symbol: str, orders: List[Dict[str, Any]]) -> List[str]:
    cancelled: List[str] = []
    for order in orders or []:
        order_id = str((order or {}).get("id") or ((order or {}).get("info") or {}).get("algoId") or "")
        if not order_id:
            continue
        try:
            exchange.cancel_order(order_id, symbol)
            cancelled.append(order_id)
        except Exception:
            continue
    return cancelled


def cancel_protection_orders(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    order_kind: str = "all",
) -> List[str]:
    orders = list_protection_orders(
        exchange=exchange,
        symbol=symbol,
        side=side,
        order_kind=order_kind,
    )
    return cancel_orders(exchange=exchange, symbol=symbol, orders=orders)


def replace_protection_orders(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    tp_price: float,
    td_mode: str = "cross",
    verify_wait_sec: float = 1.0,
) -> Dict[str, Any]:
    cancelled = cancel_protection_orders(
        exchange=exchange,
        symbol=symbol,
        side=side,
        order_kind="all",
    )
    protection = ensure_exchange_protection(
        exchange=exchange,
        symbol=symbol,
        side=side,
        qty=qty,
        sl_price=sl_price,
        tp_price=tp_price,
        td_mode=td_mode,
        verify_wait_sec=verify_wait_sec,
    )
    protection["cancelled_order_ids"] = cancelled
    return protection


def replace_stop_loss_only(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    td_mode: str = "cross",
    verify_wait_sec: float = 1.0,
) -> Dict[str, Any]:
    cancelled = cancel_protection_orders(
        exchange=exchange,
        symbol=symbol,
        side=side,
        order_kind="all",
    )
    protection = ensure_stop_loss_only(
        exchange=exchange,
        symbol=symbol,
        side=side,
        qty=qty,
        sl_price=sl_price,
        td_mode=td_mode,
        verify_wait_sec=verify_wait_sec,
    )
    protection["cancelled_order_ids"] = cancelled
    return protection


def fetch_positions(exchange: ccxt.okx, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        if symbol:
            return exchange.fetch_positions([symbol])
        return exchange.fetch_positions()
    except Exception:
        return []


def get_position_snapshot(
    exchange: ccxt.okx,
    symbol: str,
    side: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    target_pos_side = normalize_pos_side(side) if side else None
    positions = fetch_positions(exchange=exchange, symbol=symbol)
    for position in positions or []:
        contracts = _safe_float((position or {}).get("contracts"))
        raw = (position or {}).get("info") or {}
        if contracts <= 0:
            contracts = _safe_float(raw.get("pos"))
        if contracts <= 0:
            continue

        position_symbol = str((position or {}).get("symbol") or "")
        if position_symbol and position_symbol != symbol:
            continue

        pos_side = str((position or {}).get("side") or raw.get("posSide") or "").lower()
        if target_pos_side and pos_side and pos_side != target_pos_side:
            continue

        return {
            "symbol": symbol,
            "side": "buy" if pos_side == "long" else "sell",
            "pos_side": pos_side or (target_pos_side or ""),
            "contracts": round(float(contracts), 8),
            "entry_price": round(_safe_float((position or {}).get("entryPrice") or raw.get("avgPx")), 8),
            "mark_price": round(_safe_float((position or {}).get("markPrice") or raw.get("markPx")), 8),
            "leverage": round(_safe_float((position or {}).get("leverage") or raw.get("lever"), 1.0), 8),
            "raw": position,
        }
    return None


def verify_protection_orders(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    sl_price: float,
    tp_price: float,
) -> Tuple[bool, bool]:
    close_side = "sell" if normalize_side(side) == "buy" else "buy"
    try:
        algo_orders = exchange.fetch_open_orders(symbol, params={"stop": True})
    except Exception:
        algo_orders = []

    sl_ok, tp_ok = _find_algo_matches(
        algo_orders=algo_orders,
        close_side=close_side,
        sl_price=float(sl_price),
        tp_price=float(tp_price),
    )

    if sl_ok and tp_ok:
        return True, True

    try:
        algo_orders = exchange.fetch_open_orders(symbol, params={"trigger": True})
    except Exception:
        algo_orders = []

    sl_ok2, tp_ok2 = _find_algo_matches(
        algo_orders=algo_orders,
        close_side=close_side,
        sl_price=float(sl_price),
        tp_price=float(tp_price),
    )
    return bool(sl_ok or sl_ok2), bool(tp_ok or tp_ok2)


def _submit_sl_order(
    exchange: ccxt.okx,
    symbol: str,
    close_side: str,
    pos_side: str,
    qty: float,
    sl_price: float,
    td_mode: str,
) -> None:
    attempts = [
        {
            "reduceOnly": True,
            "tdMode": td_mode,
            "posSide": pos_side,
            "stopLossPrice": str(sl_price),
        },
        {
            "reduceOnly": True,
            "tdMode": td_mode,
            "posSide": pos_side,
            "stopLoss": {
                "triggerPrice": str(sl_price),
                "type": "market",
            },
        },
        {
            "reduceOnly": True,
            "tdMode": td_mode,
            "posSide": pos_side,
            "triggerPrice": str(sl_price),
            "ordType": "conditional",
            "stop": True,
        },
    ]

    last_error: Optional[Exception] = None
    for params in attempts:
        try:
            exchange.create_order(symbol, "market", close_side, qty, None, params)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"failed to place stop loss order: {last_error}")


def _submit_tp_order(
    exchange: ccxt.okx,
    symbol: str,
    close_side: str,
    pos_side: str,
    qty: float,
    tp_price: float,
    td_mode: str,
) -> None:
    attempts = [
        {
            "reduceOnly": True,
            "tdMode": td_mode,
            "posSide": pos_side,
            "takeProfitPrice": str(tp_price),
        },
        {
            "reduceOnly": True,
            "tdMode": td_mode,
            "posSide": pos_side,
            "takeProfit": {
                "triggerPrice": str(tp_price),
                "type": "market",
            },
        },
        {
            "reduceOnly": True,
            "tdMode": td_mode,
            "posSide": pos_side,
            "triggerPrice": str(tp_price),
            "ordType": "oco",
            "stop": True,
        },
    ]

    last_error: Optional[Exception] = None
    for params in attempts:
        try:
            exchange.create_order(symbol, "market", close_side, qty, None, params)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"failed to place take profit order: {last_error}")


def ensure_stop_loss_only(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    td_mode: str = "cross",
    verify_wait_sec: float = 1.0,
) -> Dict[str, Any]:
    if qty <= 0:
        return {"sl_ok": False, "message": "qty<=0"}

    open_side = normalize_side(side)
    close_side = "sell" if open_side == "buy" else "buy"
    pos_side = normalize_pos_side(side)
    td_mode = normalize_td_mode(td_mode)

    sl_ok = False
    sl_error = ""

    try:
        _submit_sl_order(exchange, symbol, close_side, pos_side, qty, float(sl_price), td_mode)
        sl_ok = True
    except Exception as exc:
        sl_error = str(exc)

    time.sleep(max(float(verify_wait_sec or 0), 0.2))
    verified_sl, _ = verify_protection_orders(exchange, symbol, side, sl_price, sl_price)
    return {
        "sl_ok": bool(sl_ok or verified_sl),
        "sl_error": sl_error,
        "pos_side": pos_side,
        "close_side": close_side,
    }


def ensure_exchange_protection(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    tp_price: float,
    td_mode: str = "cross",
    verify_wait_sec: float = 1.0,
) -> Dict[str, Any]:
    if qty <= 0:
        return {"sl_ok": False, "tp_ok": False, "message": "qty<=0"}

    open_side = normalize_side(side)
    close_side = "sell" if open_side == "buy" else "buy"
    pos_side = normalize_pos_side(side)
    td_mode = normalize_td_mode(td_mode)

    sl_ok = False
    tp_ok = False
    sl_error = ""
    tp_error = ""

    try:
        _submit_sl_order(exchange, symbol, close_side, pos_side, qty, float(sl_price), td_mode)
        sl_ok = True
    except Exception as exc:
        sl_error = str(exc)

    try:
        _submit_tp_order(exchange, symbol, close_side, pos_side, qty, float(tp_price), td_mode)
        tp_ok = True
    except Exception as exc:
        tp_error = str(exc)

    time.sleep(max(float(verify_wait_sec or 0), 0.2))
    verified_sl, verified_tp = verify_protection_orders(exchange, symbol, side, sl_price, tp_price)

    return {
        "sl_ok": bool(sl_ok or verified_sl),
        "tp_ok": bool(tp_ok or verified_tp),
        "sl_error": sl_error,
        "tp_error": tp_error,
        "pos_side": pos_side,
        "close_side": close_side,
    }


def force_market_order(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: float,
    leverage: int = 10,
    td_mode: str = "cross",
    margin_ccy: str = "USDT",
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    if qty <= 0:
        raise ValueError("qty must be > 0")

    open_side = normalize_side(side)
    pos_side = normalize_pos_side(side)
    td_mode = normalize_td_mode(td_mode)

    params: Dict[str, Any] = {
        "tdMode": td_mode,
        "posSide": pos_side,
        "ccy": margin_ccy,
        "lever": str(int(leverage)),
    }
    if client_order_id:
        params["clOrdId"] = normalize_client_order_id(client_order_id)

    set_symbol_leverage(
        exchange=exchange,
        symbol=symbol,
        leverage=leverage,
        td_mode=td_mode,
        side=side,
    )

    try:
        return exchange.create_order(symbol, "market", open_side, float(qty), None, params)
    except Exception as exc:
        error_text = str(exc).lower()
        if "clordid" not in error_text and "51000" not in error_text:
            raise

        fallback_params = dict(params)
        fallback_params.pop("clOrdId", None)
        fallback_params.pop("clordid", None)
        return exchange.create_order(symbol, "market", open_side, float(qty), None, fallback_params)


def force_open_with_tp_sl(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: Optional[float],
    stop_loss_price: float,
    take_profit_price: float,
    leverage: int = 10,
    td_mode: str = "cross",
    margin_ccy: str = "USDT",
    verify_wait_sec: float = 1.0,
    client_order_id: Optional[str] = None,
    equity: Optional[float] = None,
    entry_price: Optional[float] = None,
    margin_pct: Optional[float] = DEFAULT_MARGIN_PCT,
    risk_pct: float = DEFAULT_RISK_PCT,
    min_margin_pct: float = MIN_MARGIN_PCT,
    max_margin_pct: float = MAX_MARGIN_PCT,
    require_stop_loss: bool = True,
) -> Dict[str, Any]:
    plan: Optional[Dict[str, Any]] = None
    resolved_qty = float(qty or 0)
    if resolved_qty <= 0:
        plan = build_forced_order_plan(
            exchange=exchange,
            symbol=symbol,
            side=side,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            td_mode=td_mode,
            equity=equity,
            entry_price=entry_price,
            margin_pct=margin_pct,
            risk_pct=risk_pct,
            min_margin_pct=min_margin_pct,
            max_margin_pct=max_margin_pct,
        )
        resolved_qty = float(plan["qty"])
        if resolved_qty <= 0:
            raise RuntimeError("computed qty <= 0")

    order = force_market_order(
        exchange=exchange,
        symbol=symbol,
        side=side,
        qty=resolved_qty,
        leverage=int(((plan or {}).get("leverage")) or leverage),
        td_mode=td_mode,
        margin_ccy=margin_ccy,
        client_order_id=client_order_id,
    )
    protection = ensure_exchange_protection(
        exchange=exchange,
        symbol=symbol,
        side=side,
        qty=resolved_qty,
        sl_price=stop_loss_price,
        tp_price=take_profit_price,
        td_mode=td_mode,
        verify_wait_sec=verify_wait_sec,
    )
    if require_stop_loss and not bool(protection.get("sl_ok")):
        try:
            force_close_position(
                exchange=exchange,
                symbol=symbol,
                side=side,
                qty=resolved_qty,
                td_mode=td_mode,
                margin_ccy=margin_ccy,
            )
        except Exception:
            pass
        raise RuntimeError(f"stop loss protection missing for {symbol}, position was force-closed")
    return {
        "plan": plan,
        "order": order,
        "protection": protection,
    }


def force_open_with_sl_only(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: Optional[float],
    stop_loss_price: float,
    leverage: int = 10,
    td_mode: str = "cross",
    margin_ccy: str = "USDT",
    verify_wait_sec: float = 1.0,
    client_order_id: Optional[str] = None,
    equity: Optional[float] = None,
    entry_price: Optional[float] = None,
    margin_pct: Optional[float] = DEFAULT_MARGIN_PCT,
    risk_pct: float = DEFAULT_RISK_PCT,
    min_margin_pct: float = MIN_MARGIN_PCT,
    max_margin_pct: float = MAX_MARGIN_PCT,
    require_stop_loss: bool = True,
) -> Dict[str, Any]:
    plan: Optional[Dict[str, Any]] = None
    resolved_qty = float(qty or 0)
    if resolved_qty <= 0:
        plan = build_forced_order_plan(
            exchange=exchange,
            symbol=symbol,
            side=side,
            stop_loss_price=stop_loss_price,
            take_profit_price=stop_loss_price,
            leverage=leverage,
            td_mode=td_mode,
            equity=equity,
            entry_price=entry_price,
            margin_pct=margin_pct,
            risk_pct=risk_pct,
            min_margin_pct=min_margin_pct,
            max_margin_pct=max_margin_pct,
        )
        resolved_qty = float(plan["qty"])
        if resolved_qty <= 0:
            raise RuntimeError("computed qty <= 0")

    order = force_market_order(
        exchange=exchange,
        symbol=symbol,
        side=side,
        qty=resolved_qty,
        leverage=int(((plan or {}).get("leverage")) or leverage),
        td_mode=td_mode,
        margin_ccy=margin_ccy,
        client_order_id=client_order_id,
    )
    protection = ensure_stop_loss_only(
        exchange=exchange,
        symbol=symbol,
        side=side,
        qty=resolved_qty,
        sl_price=stop_loss_price,
        td_mode=td_mode,
        verify_wait_sec=verify_wait_sec,
    )
    if require_stop_loss and not bool(protection.get("sl_ok")):
        try:
            force_close_position(
                exchange=exchange,
                symbol=symbol,
                side=side,
                qty=resolved_qty,
                td_mode=td_mode,
                margin_ccy=margin_ccy,
            )
        except Exception:
            pass
        raise RuntimeError(f"stop loss protection missing for {symbol}, position was force-closed")
    return {
        "plan": plan,
        "order": order,
        "protection": protection,
    }


def force_close_position(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    qty: float,
    td_mode: str = "cross",
    margin_ccy: str = "USDT",
) -> Dict[str, Any]:
    if qty <= 0:
        raise ValueError("qty must be > 0")

    open_side = normalize_side(side)
    close_side = "sell" if open_side == "buy" else "buy"
    pos_side = normalize_pos_side(side)
    td_mode = normalize_td_mode(td_mode)

    params = {
        "reduceOnly": True,
        "tdMode": td_mode,
        "posSide": pos_side,
        "ccy": margin_ccy,
    }
    return exchange.create_order(symbol, "market", close_side, float(qty), None, params)


if __name__ == "__main__":
    exchange = create_okx_exchange()

    result = force_open_with_tp_sl(
        exchange=exchange,
        symbol="BTC-USDT-SWAP",
        side="buy",
        qty=None,
        stop_loss_price=82000,
        take_profit_price=86000,
        leverage=10,
        td_mode="cross",
        margin_pct=0.04,
        risk_pct=0.01,
        client_order_id=f"codex-{int(time.time())}",
    )

    print(result)
