from strategy_registry import StrategySpec, upsert_strategy
from learning_store import get_ai_strategy_profile

STRATEGY_ID = "ai_generated_meta_v1"


def refresh_strategy_spec() -> None:
    profile = get_ai_strategy_profile() or {}
    enabled = bool(profile.get("enabled"))
    upsert_strategy(
        StrategySpec(
            strategy_id=STRATEGY_ID,
            name="AI Generated Meta Strategy",
            timezone="UTC",
            market_type="USDT perpetual swap",
            symbol_universe="derived from learned source strategies",
            scan_interval_sec=60,
            decision_inputs=[
                "learned source-strategy weights",
                "candidate RR and learned win rate",
                "source strategy historical performance",
            ],
            learning_targets=[
                "self-performance after auto-applied weekly OpenAI suggestions",
            ],
            tags=["ai", "meta", "adaptive"],
            note="This slot is enabled only after every enabled human strategy has at least 30 closed trades and an AI profile has been generated.",
            enabled=enabled,
        )
    )


refresh_strategy_spec()
