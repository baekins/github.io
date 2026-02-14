from __future__ import annotations

from dataclasses import dataclass

from .math_utils import slippage_heuristic
from .models import Outcome


@dataclass
class CostBreakdown:
    spread: float
    fee: float
    slippage: float

    @property
    def total(self) -> float:
        return self.spread + self.fee + self.slippage


def estimate_cost_for_outcome(outcome: Outcome, liquidity: float | None) -> CostBreakdown:
    spread = outcome.spread / 2 if outcome.spread is not None else 0.01
    fee = (outcome.fee_rate_bps or 0.0) / 10000.0
    slip = slippage_heuristic(liquidity)
    return CostBreakdown(spread=spread, fee=fee, slippage=slip)
