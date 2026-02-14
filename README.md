# Polymarket Sports Auto-Analyst

Deterministic chat-first analysis engine for Polymarket sports markets.

## Features
- Input: Polymarket event/market URL or plain match text (`Lakers vs Celtics`).
- Uses **Gamma API** for market discovery/resolution.
- Non-URL flow performs `/public-search` and auto-selects best active sports candidate by:
  1) active and not closed, 2) highest liquidity, 3) highest volume24hr, 4) nearest startDate.
- Always shows "Other top candidates (up to 5)" for ambiguity transparency.
- Optional CLOB read adapter for best bid/ask/mid/spread and optional fee rate.
- Structured markdown output focused on edge, costs, and risk-first sizing.
- Compliance-safe analysis-only messaging with geoblock placeholder.

## Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

## CLI
```bash
polybet analyze "https://polymarket.com/market/lakers-vs-celtics"
polybet analyze "Lakers vs Celtics"
polybet chat
```

## Optional HTTP server
```bash
polybet serve --host 0.0.0.0 --port 8787
curl -X POST http://localhost:8787/analyze -H "content-type: application/json" -d '{"text":"Lakers vs Celtics"}'
```

## Output sections
1. Market Snapshot (Asia/Seoul time formatting + fetch timestamp)
2. Market Quality + Costs (spread/fee/slippage)
3. Fair Probability Engine (baseline and manual de-vig reference blending)
4. Edge + EV gating (EV_MIN, LIQ_MIN, SPREAD_MAX)
5. Position sizing (fractional Kelly + max bet + daily exposure caps)
6. What would change the view

## Tests
```bash
pytest
```
