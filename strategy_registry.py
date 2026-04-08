from dataclasses import asdict, dataclass, field
from typing import Dict, List


@dataclass
class StrategyDataRequirement:
    key: str
    source: str
    timeframe: str
    lookback: int = 0
    note: str = ""


@dataclass
class StrategySpec:
    strategy_id: str
    name: str
    timezone: str
    market_type: str
    symbol_universe: str
    scan_interval_sec: int
    required_data: List[StrategyDataRequirement] = field(default_factory=list)
    decision_inputs: List[str] = field(default_factory=list)
    learning_targets: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    note: str = ""
    enabled: bool = True

    def to_dict(self) -> Dict:
        payload = asdict(self)
        payload["required_data"] = [asdict(item) for item in self.required_data]
        return payload


STRATEGY_REGISTRY: Dict[str, StrategySpec] = {}


def register_strategy(spec: StrategySpec) -> None:
    strategy_id = str(spec.strategy_id or "").strip()
    if not strategy_id:
        raise ValueError("strategy_id is required")
    if strategy_id in STRATEGY_REGISTRY:
        raise ValueError(f"strategy already exists: {strategy_id}")
    STRATEGY_REGISTRY[strategy_id] = spec


def upsert_strategy(spec: StrategySpec) -> None:
    strategy_id = str(spec.strategy_id or "").strip()
    if not strategy_id:
        raise ValueError("strategy_id is required")
    STRATEGY_REGISTRY[strategy_id] = spec


def get_strategy(strategy_id: str) -> StrategySpec:
    key = str(strategy_id or "").strip()
    if key not in STRATEGY_REGISTRY:
        raise KeyError(f"unknown strategy: {key}")
    return STRATEGY_REGISTRY[key]


def list_strategies() -> List[Dict]:
    return [spec.to_dict() for spec in STRATEGY_REGISTRY.values()]


def list_enabled_strategies() -> List[Dict]:
    return [spec.to_dict() for spec in STRATEGY_REGISTRY.values() if bool(spec.enabled)]
