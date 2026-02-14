from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    def load_dotenv():
        return None

load_dotenv()


@dataclass(frozen=True)
class Settings:
    gamma_base_url: str = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    clob_base_url: str = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
    cache_dir: str = os.getenv("CACHE_DIR", ".cache/polybet")
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "20"))
    enable_clob_reads: bool = os.getenv("ENABLE_CLOB_READS", "0") == "1"
    timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
    ev_min: float = float(os.getenv("EV_MIN", "0.02"))
    liq_min: float = float(os.getenv("LIQ_MIN", "2000"))
    spread_max: float = float(os.getenv("SPREAD_MAX", "0.05"))
    fractional_kelly: float = float(os.getenv("FRACTIONAL_KELLY", "0.25"))
    max_bet_pct: float = float(os.getenv("MAX_BET_PCT", "0.01"))
    max_daily_exposure: float = float(os.getenv("MAX_DAILY_EXPOSURE", "0.05"))
    default_bankroll: float = float(os.getenv("DEFAULT_BANKROLL", "5000"))


SETTINGS = Settings()
