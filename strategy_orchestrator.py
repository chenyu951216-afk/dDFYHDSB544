from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from openai_learning_sync import run_weekly_ai_learning_cycle
from strategy_ai_generated_meta_engine import (
    AIGeneratedMetaStrategyConfig,
    run_cycle as run_ai_generated_cycle,
)
from strategy_dual_sma_pullback_2h_engine import (
    DualSmaPullbackStrategyConfig,
    run_cycle as run_dual_sma_cycle,
)
from strategy_mean_reversion_atr_2h_daily_engine import (
    MeanReversionAtrStrategyConfig,
    run_cycle as run_mean_rev_cycle,
)
from strategy_naked_k_reversal_1h_engine import (
    NakedKReversalStrategyConfig,
    run_cycle as run_naked_k_cycle,
)
from strategy_burst_sma_channel_1h_engine import (
    BurstSMAChannelStrategyConfig,
    run_cycle as run_burst_cycle,
)
from strategy_ma_breakout_4h_engine import MABreakoutStrategyConfig, run_cycle as run_ma_breakout_cycle
from strategy_bollinger_width_4h_engine import BollingerWidthStrategyConfig, run_cycle as run_bbw_cycle
from okx_force_order import create_okx_exchange
from strategy_larry_breakout_cmo_engine import LarryStrategyConfig, run_cycle as run_larry_cycle
from strategy_portfolio import get_per_strategy_allocated_equity
from strategy_runtime_state import list_active_positions
from strategy_trend_hma_std_engine import TrendStrategyConfig, run_cycle as run_trend_cycle


@dataclass
class OrchestratorConfig:
    trend_hma_std_enabled: bool = True
    larry_breakout_enabled: bool = True
    bollinger_width_enabled: bool = True
    ma_breakout_enabled: bool = True
    burst_sma_channel_enabled: bool = True
    naked_k_reversal_enabled: bool = True
    mean_reversion_atr_enabled: bool = True
    dual_sma_pullback_enabled: bool = True
    ai_generated_enabled: bool = True
    trend_hma_std: TrendStrategyConfig = field(default_factory=TrendStrategyConfig)
    larry_breakout: LarryStrategyConfig = field(default_factory=LarryStrategyConfig)
    bollinger_width: BollingerWidthStrategyConfig = field(default_factory=BollingerWidthStrategyConfig)
    ma_breakout: MABreakoutStrategyConfig = field(default_factory=MABreakoutStrategyConfig)
    burst_sma_channel: BurstSMAChannelStrategyConfig = field(default_factory=BurstSMAChannelStrategyConfig)
    naked_k_reversal: NakedKReversalStrategyConfig = field(default_factory=NakedKReversalStrategyConfig)
    mean_reversion_atr: MeanReversionAtrStrategyConfig = field(default_factory=MeanReversionAtrStrategyConfig)
    dual_sma_pullback: DualSmaPullbackStrategyConfig = field(default_factory=DualSmaPullbackStrategyConfig)
    ai_generated: AIGeneratedMetaStrategyConfig = field(default_factory=AIGeneratedMetaStrategyConfig)


def _run_strategy(strategy_id: str, runner, exchange, config_obj) -> Dict[str, Any]:
    try:
        return {
            "strategy_id": strategy_id,
            "result": runner(exchange=exchange, config=config_obj),
        }
    except Exception as exc:
        return {
            "strategy_id": strategy_id,
            "result": {
                "phase": "error",
                "result": {
                    "status": "runner_exception",
                    "error": str(exc),
                },
            },
        }


def run_all_strategies(
    exchange=None,
    config: Optional[OrchestratorConfig] = None,
) -> Dict[str, Any]:
    exchange = exchange or create_okx_exchange()
    config = config or OrchestratorConfig()

    capital_state = get_per_strategy_allocated_equity(exchange=exchange)
    results: List[Dict[str, Any]] = []

    if bool(config.trend_hma_std_enabled):
        results.append(_run_strategy("trend_hma_std_4h_v1", run_trend_cycle, exchange, config.trend_hma_std))

    if bool(config.larry_breakout_enabled):
        results.append(_run_strategy("larry_breakout_cmo_2h_4h_v1", run_larry_cycle, exchange, config.larry_breakout))

    if bool(config.bollinger_width_enabled):
        results.append(_run_strategy("bollinger_width_4h_v1", run_bbw_cycle, exchange, config.bollinger_width))

    if bool(config.ma_breakout_enabled):
        results.append(_run_strategy("ma_breakout_4h_v1", run_ma_breakout_cycle, exchange, config.ma_breakout))

    if bool(config.burst_sma_channel_enabled):
        results.append(_run_strategy("burst_sma_channel_1h_v1", run_burst_cycle, exchange, config.burst_sma_channel))

    if bool(config.naked_k_reversal_enabled):
        results.append(_run_strategy("naked_k_reversal_1h_v1", run_naked_k_cycle, exchange, config.naked_k_reversal))

    if bool(config.mean_reversion_atr_enabled):
        results.append(_run_strategy("mean_reversion_atr_2h_daily_v1", run_mean_rev_cycle, exchange, config.mean_reversion_atr))

    if bool(config.dual_sma_pullback_enabled):
        results.append(_run_strategy("dual_sma_pullback_2h_v1", run_dual_sma_cycle, exchange, config.dual_sma_pullback))

    if bool(config.ai_generated_enabled):
        results.append(_run_strategy("ai_generated_meta_v1", run_ai_generated_cycle, exchange, config.ai_generated))

    try:
        weekly_ai_sync = run_weekly_ai_learning_cycle()
    except Exception as exc:
        weekly_ai_sync = {
            "status": "error",
            "error": str(exc),
        }

    return {
        "capital_state": capital_state,
        "active_positions": list_active_positions(),
        "results": results,
        "weekly_ai_sync": weekly_ai_sync,
        "config": {
            "trend_hma_std_enabled": bool(config.trend_hma_std_enabled),
            "larry_breakout_enabled": bool(config.larry_breakout_enabled),
            "bollinger_width_enabled": bool(config.bollinger_width_enabled),
            "ma_breakout_enabled": bool(config.ma_breakout_enabled),
            "burst_sma_channel_enabled": bool(config.burst_sma_channel_enabled),
            "naked_k_reversal_enabled": bool(config.naked_k_reversal_enabled),
            "mean_reversion_atr_enabled": bool(config.mean_reversion_atr_enabled),
            "dual_sma_pullback_enabled": bool(config.dual_sma_pullback_enabled),
            "ai_generated_enabled": bool(config.ai_generated_enabled),
            "trend_hma_std": asdict(config.trend_hma_std),
            "larry_breakout": asdict(config.larry_breakout),
            "bollinger_width": asdict(config.bollinger_width),
            "ma_breakout": asdict(config.ma_breakout),
            "burst_sma_channel": asdict(config.burst_sma_channel),
            "naked_k_reversal": asdict(config.naked_k_reversal),
            "mean_reversion_atr": asdict(config.mean_reversion_atr),
            "dual_sma_pullback": asdict(config.dual_sma_pullback),
            "ai_generated": asdict(config.ai_generated),
        },
    }


if __name__ == "__main__":
    output = run_all_strategies()
    print(output)
