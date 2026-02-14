from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Outcome:
    name: str
    price: float
    best_bid: float | None = None
    best_ask: float | None = None
    mid: float | None = None
    spread: float | None = None
    token_id: str | None = None
    fee_rate_bps: float | None = None


@dataclass
class MarketSnapshot:
    source: str
    title: str
    slug: str
    event_slug: str | None
    active: bool
    closed: bool
    start_date: datetime | None
    end_date: datetime | None
    outcomes: list[Outcome]
    liquidity: float | None
    volume24hr: float | None
    open_interest: float | None
    fetched_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    title: str
    slug: str
    type: str = "market"
    active: bool = False
    closed: bool = True
    liquidity: float = 0.0
    volume24hr: float = 0.0
    start_date: datetime | None = None
    sports_related: bool = True
