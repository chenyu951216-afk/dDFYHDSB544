import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from learning_store import (
    apply_ai_strategy_patch,
    build_weekly_summary,
    ensure_ai_strategy_profile,
    get_ai_strategy_profile,
    save_weekly_ai_review,
    should_run_weekly_sync,
)
from strategy_registry import list_enabled_strategies


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _json_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "week_key": {"type": "string"},
            "overall_observations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "strategy_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "strategy_id": {"type": "string"},
                        "headline": {"type": "string"},
                        "strengths": {"type": "array", "items": {"type": "string"}},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "recommendations": {"type": "array", "items": {"type": "string"}},
                        "parameter_patch": {"type": "object"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["strategy_id", "headline", "strengths", "risks", "recommendations", "parameter_patch", "confidence"],
                },
            },
            "ai_generated_strategy_patch": {"type": "object"},
        },
        "required": ["week_key", "overall_observations", "strategy_reviews", "ai_generated_strategy_patch"],
    }


def _build_prompt(summary_payload: Dict[str, Any]) -> str:
    return (
        "You are reviewing a multi-strategy crypto futures trading system. "
        "Use the compact weekly summary below. Give short, practical advice. "
        "Do not rewrite all strategies. Keep manual-strategy recommendations as suggestions only. "
        "For the AI-generated strategy, you may propose a direct profile patch. "
        "Focus on expectancy, risk concentration, over-leverage, unstable exits, and symbol concentration.\n\n"
        f"Enabled strategies:\n{json.dumps(list_enabled_strategies(), ensure_ascii=False)}\n\n"
        f"Weekly summary:\n{json.dumps(summary_payload, ensure_ascii=False)}"
    )


def request_openai_weekly_review(summary_payload: Dict[str, Any]) -> Dict[str, Any]:
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return {"status": "missing_api_key"}

    try:
        from openai import OpenAI
    except Exception as exc:
        return {"status": "missing_openai_package", "error": str(exc)}

    model = _env("OPENAI_WEEKLY_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "text", "text": "Return valid JSON only."}]},
                {"role": "user", "content": [{"type": "text", "text": _build_prompt(summary_payload)}]},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "weekly_trading_review",
                    "strict": True,
                    "schema": _json_schema(),
                }
            },
        )
        raw_text = getattr(response, "output_text", "") or ""
        parsed = json.loads(raw_text)
        return {"status": "ok", "model": model, "review": parsed}
    except Exception as exc:
        return {"status": "request_failed", "model": model, "error": str(exc)}


def run_weekly_ai_learning_cycle(now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    ensure_result = ensure_ai_strategy_profile()
    if not should_run_weekly_sync(now):
        return {"status": "skip", "reason": "not_sunday_or_already_synced", "ai_profile": ensure_result}

    summary_payload = build_weekly_summary(now)
    review_result = request_openai_weekly_review(summary_payload)
    if review_result.get("status") != "ok":
        return {"status": "review_not_saved", "summary": summary_payload, "review_result": review_result, "ai_profile": ensure_result}

    review = dict(review_result["review"])
    week_key = str(review.get("week_key") or summary_payload["week_key"])
    ai_patch = dict(review.get("ai_generated_strategy_patch") or {})
    applied_patch = None
    profile = get_ai_strategy_profile()
    if ai_patch and profile and bool(profile.get("auto_apply_suggestions")):
        applied_patch = apply_ai_strategy_patch(ai_patch, source_week_key=week_key)

    stored = save_weekly_ai_review(
        {
            "week_key": week_key,
            "requested_at_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "summary": summary_payload,
            "review": review,
            "applied_ai_strategy_patch": applied_patch,
        }
    )
    return {
        "status": "saved",
        "week_key": week_key,
        "stored_review": stored,
        "review_result": review_result,
        "ai_profile": ensure_result,
    }
