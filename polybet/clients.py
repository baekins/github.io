from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None

try:
    from diskcache import Cache
except ModuleNotFoundError:  # pragma: no cover
    class Cache:  # type: ignore
        def __init__(self, *_args, **_kwargs):
            self._cache = {}

        def get(self, key):
            return self._cache.get(key)

        def set(self, key, value, expire=None):
            self._cache[key] = value

try:
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
except ModuleNotFoundError:  # pragma: no cover
    def retry(*_args, **_kwargs):
        def dec(func):
            return func
        return dec

    def retry_if_exception_type(*_args, **_kwargs):
        return None

    def stop_after_attempt(*_args, **_kwargs):
        return None

    def wait_exponential_jitter(*_args, **_kwargs):
        return None

from .config import SETTINGS
from .models import Candidate, MarketSnapshot, Outcome

SPORT_HINTS = {"sport", "sports", "nba", "nfl", "nhl", "mlb", "soccer", "football", "tennis", "ufc"}


class RateLimitError(Exception):
    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__("Rate limited")


class GammaClient:
    def __init__(self) -> None:
        self.base = SETTINGS.gamma_base_url.rstrip("/")
        self.cache = Cache(SETTINGS.cache_dir)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = f"gamma::{path}::{params}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        data = await self._request(path, params)
        self.cache.set(key, data, expire=SETTINGS.cache_ttl_seconds)
        return data

    @retry(
        retry=retry_if_exception_type(((httpx.HTTPError if httpx else Exception), RateLimitError)),
        wait=wait_exponential_jitter(initial=0.5, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if httpx is None:
            raise RuntimeError("httpx is required for network calls")
        async with httpx.AsyncClient(timeout=SETTINGS.timeout_seconds) as client:
            r = await client.get(f"{self.base}{path}", params=params)
            if r.status_code == 429:
                retry_after = self._retry_after_seconds(r)
                if retry_after:
                    await asyncio.sleep(retry_after)
                raise RateLimitError(retry_after)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _retry_after_seconds(resp: httpx.Response) -> float | None:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                return None

    async def fetch_event_by_slug(self, slug: str) -> dict[str, Any]:
        return await self._get(f"/events/slug/{slug}")

    async def fetch_market_by_slug(self, slug: str) -> dict[str, Any]:
        return await self._get(f"/markets/slug/{slug}")

    async def search(self, query: str) -> dict[str, Any]:
        return await self._get(
            "/public-search",
            {
                "q": query,
                "events_status": "active",
                "limit_per_type": 5,
                "keep_closed_markets": 0,
                "optimized": "true",
            },
        )


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_outcomes(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return []
    return []


def _normalize_prices(raw: Any) -> list[float]:
    if isinstance(raw, list):
        return [max(0.0, min(1.0, _to_float(x) or 0.0)) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [max(0.0, min(1.0, _to_float(x) or 0.0)) for x in parsed]
        except json.JSONDecodeError:
            return []
    return []


def parse_market_payload(payload: dict[str, Any], source: str = "gamma") -> MarketSnapshot:
    outcomes_raw = _normalize_outcomes(payload.get("outcomes") or payload.get("tokens") or [])
    prices = _normalize_prices(payload.get("outcomePrices") or payload.get("prices") or [])
    token_ids_raw = payload.get("clobTokenIds") or payload.get("token_ids") or []
    token_ids = token_ids_raw if isinstance(token_ids_raw, list) else []

    outcomes: list[Outcome] = []
    for idx, name in enumerate(outcomes_raw):
        price = prices[idx] if idx < len(prices) else 0.0
        token_id = str(token_ids[idx]) if idx < len(token_ids) else None
        outcomes.append(Outcome(name=name, price=price, token_id=token_id))

    return MarketSnapshot(
        source=source,
        title=payload.get("question") or payload.get("title") or "Unknown",
        slug=payload.get("slug") or "unknown",
        event_slug=payload.get("eventSlug"),
        active=bool(payload.get("active", False)),
        closed=bool(payload.get("closed", True)),
        start_date=_to_dt(payload.get("startDate") or payload.get("startTime")),
        end_date=_to_dt(payload.get("endDate") or payload.get("endTime")),
        outcomes=outcomes,
        liquidity=_to_float(payload.get("liquidity") or payload.get("liquidityNum")),
        volume24hr=_to_float(payload.get("volume24hr") or payload.get("volume24Hr")),
        open_interest=_to_float(payload.get("openInterest")),
        fetched_at=datetime.now(timezone.utc),
        raw=payload,
    )


def _is_sports_related(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(item.get(k, ""))
        for k in ("category", "groupItemTitle", "title", "question", "sport", "league")
    ).lower()
    if any(h in text for h in SPORT_HINTS):
        return True
    tags = item.get("tags") or []
    if isinstance(tags, list):
        joined = " ".join(str(t).lower() for t in tags)
        if any(h in joined for h in SPORT_HINTS):
            return True
    return False


def collect_search_candidates(search_payload: dict[str, Any]) -> list[Candidate]:
    items: list[dict[str, Any]] = []
    for key in ("markets", "events", "data", "results"):
        value = search_payload.get(key)
        if isinstance(value, list):
            items.extend([x for x in value if isinstance(x, dict)])

    candidates: list[Candidate] = []
    for item in items:
        candidates.append(
            Candidate(
                title=item.get("question") or item.get("title") or "Unknown",
                slug=item.get("slug") or "",
                type=item.get("type") or ("event" if item.get("markets") else "market"),
                active=bool(item.get("active", False)),
                closed=bool(item.get("closed", True)),
                liquidity=_to_float(item.get("liquidity") or item.get("liquidityNum")) or 0.0,
                volume24hr=_to_float(item.get("volume24hr") or item.get("volume24Hr")) or 0.0,
                start_date=_to_dt(item.get("startDate") or item.get("startTime")),
                sports_related=_is_sports_related(item),
            )
        )
    return candidates


def choose_best_candidate(candidates: list[Candidate]) -> Candidate | None:
    pool = [c for c in candidates if c.active and not c.closed and c.slug]
    sports_pool = [c for c in pool if c.sports_related]
    active_pool = sports_pool or pool
    if not active_pool:
        return None

    now = datetime.now(timezone.utc)
    return sorted(
        active_pool,
        key=lambda c: (
            -c.liquidity,
            -c.volume24hr,
            abs((c.start_date - now).total_seconds()) if c.start_date else float("inf"),
            c.slug,
        ),
    )[0]


class ClobClient:
    def __init__(self) -> None:
        self.base = SETTINGS.clob_base_url.rstrip("/")

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError if httpx else Exception,)),
        wait=wait_exponential_jitter(initial=0.5, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def fetch_book(self, token_id: str) -> dict[str, Any]:
        if httpx is None:
            raise RuntimeError("httpx is required for network calls")
        async with httpx.AsyncClient(timeout=SETTINGS.timeout_seconds) as client:
            r = await client.get(f"{self.base}/book", params={"token_id": token_id})
            r.raise_for_status()
            return r.json()

    async def fetch_fee_rate(self, token_id: str) -> float | None:
        if httpx is None:
            return None
        async with httpx.AsyncClient(timeout=SETTINGS.timeout_seconds) as client:
            r = await client.get(f"{self.base}/fee-rate", params={"token_id": token_id})
            if r.is_error:
                return None
            data = r.json()
            raw = data.get("fee_rate_bps")
            try:
                return float(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None
