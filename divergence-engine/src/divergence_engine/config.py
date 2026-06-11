"""Engine configuration. Defaults mirror the build spec v0.1; everything is
overridable from a YAML file so calibration (theta etc.) never means a code edit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

HORIZONS_DAYS = (1, 3, 7, 15)


@dataclass
class TriggerConfig:
    velocity_z_min: float = 2.0           # stage 1(a)
    velocity_lookback_days: int = 30
    flip_coverage_min: float = 0.5        # stage 1(b): 'moderate' coverage
    contradiction_low: float = 0.2        # stage 1(c)
    contradiction_high: float = 0.5
    contradiction_lookback_days: int = 7


@dataclass
class GateConfig:
    priced_z_kill: float = 1.5            # §5.2 already-priced kill
    structure_conflict_threshold_mult: float = 1.5   # §5.3
    structure_conflict_size_mult: float = 0.5


@dataclass
class ScoreConfig:
    lambda_priced: float = 0.5            # D = P_narr - lambda * priced_z
    theta: float | None = None            # None => top-decile calibration
    theta_decile: float = 0.9             # quantile used while theta is None
    narrative_shift_kappa: float = 0.02   # return shift per unit P_narr for targets


@dataclass
class RiskConfig:
    paper_nav: float = 100_000.0
    per_trade_risk_frac: float = 0.005    # 0.5% of paper NAV
    per_instrument_max_tickets: int = 2
    theatre_risk_cap_frac: float = 0.60
    cluster_overlap_min: float = 0.50     # weighted feed overlap => same story
    kill_switch_consecutive_cluster_losses: int = 3
    kill_switch_drawdown_frac: float = 0.10
    stop_sigma_mult: float = 1.2          # stop = 1.2 * sigma_K(h)
    time_stop_horizon_mult: float = 1.5


@dataclass
class KronosConfig:
    model: str = "kronos-small"
    n_paths: int = 64
    context_len: int = 512
    device: str = "cpu"
    refresh_hours: int = 4


@dataclass
class EngineConfig:
    triggers: TriggerConfig = field(default_factory=TriggerConfig)
    gates: GateConfig = field(default_factory=GateConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    kronos: KronosConfig = field(default_factory=KronosConfig)
    feeds_dir: str = "feeds"
    db_dsn: str = "postgresql://localhost/divergence"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "EngineConfig":
        cfg = cls()
        if path is None:
            return cfg
        raw = yaml.safe_load(Path(path).read_text()) or {}
        for section, values in raw.items():
            target = getattr(cfg, section, None)
            if target is None:
                raise KeyError(f"unknown config section: {section}")
            if isinstance(values, dict):
                for k, v in values.items():
                    if not hasattr(target, k):
                        raise KeyError(f"unknown config key: {section}.{k}")
                    setattr(target, k, v)
            else:
                setattr(cfg, section, values)
        return cfg
