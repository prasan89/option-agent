# AI Options Flow Agent

AI-assisted quantitative research and options-flow analysis platform for Indian index derivatives.

> **Safety:** Phase 2 remains read-only. The system infers market flow from public market-data observations; it does not identify institutional traders and does not place orders. API credentials and secrets must never be committed to Git.

## Phase 1 — Groww market data

Implemented:

- Groww Python SDK integration (`growwapi`)
- Groww API-key/secret or access-token authentication
- Read-only user-profile connectivity check
- NIFTY option-chain retrieval with Greeks, OI and volume
- NIFTY F&O instrument-master filtering
- LTP retrieval
- Groww live LTP feed subscription
- Groww aggregated market-depth feed subscription
- Raw feed snapshots persisted to Redis Stream `market:raw`
- FastAPI endpoints for connectivity and market data

## Phase 2 — Options-flow detection

Implemented:

- Redis-stream flow engine consuming Phase-1 observations
- LTP-vs-best-bid/ask aggression inference
- Bid/ask depth imbalance calculation
- Price-momentum confirmation
- Explainable 0–100 flow score
- BUY/SELL directional classification with MEDIUM/HIGH confidence
- Evidence labels for each signal
- Output Redis Stream `flow:signals`
- Start/stop/status API for the flow engine
- Unit tests for aggressive buy/sell and noise filtering

The Phase 2 detector is intentionally deterministic and explainable. It does **not** claim to see institutional orders. Groww's public feed provides LTP and aggregated market depth; option-chain data separately provides OI, volume and Greeks. OI/volume fusion is the next Phase 2 increment after the microstructure signal path is validated.

## Local setup

1. Copy `.env.example` to `.env`.
2. Add either a valid `GROWW_ACCESS_TOKEN` or your Groww `GROWW_API_KEY` + `GROWW_API_SECRET`.
3. Start the stack:

```bash
docker compose up --build
```

4. Open the API docs:

```text
http://localhost:8000/docs
```

5. Verify system status:

```bash
curl http://localhost:8000/system/status
curl http://localhost:8000/groww/status
curl http://localhost:8000/flow/status
```

### Starting Phase 2

After the Groww feed has been started, start the flow engine:

```bash
curl -X POST http://localhost:8000/flow/start
```

Signals are published to Redis Stream `flow:signals`. Stop it with:

```bash
curl -X POST http://localhost:8000/flow/stop
```

Groww's API-key/secret flow requires the current token process described in its documentation. Do not hard-code or commit tokens. For SAP BTP, configure credentials as environment secrets.

## API endpoints

```text
GET  /health
GET  /version
GET  /system/status

GET  /groww/status
GET  /groww/profile
GET  /groww/option-chain?expiry_date=YYYY-MM-DD
GET  /groww/ltp?exchange_symbols=NSE_SYMBOL
GET  /groww/nifty/instruments?expiry_date=YYYY-MM-DD&strike_min=...
POST /groww/feed/start

GET  /flow/status
POST /flow/start
POST /flow/stop
```

## Architecture

```text
                         Groww
                           |
               +-----------+-----------+
               |                       |
          Option Chain             Live Feed
               |                       |
               v                       v
          GrowwClient          GrowwFeedService
               |                       |
               |                +------+------+
               |                |             |
               |               LTP       Market Depth
               |                |             |
               +----------------+-------------+
                                |
                                v
                         Redis Stream
                         market:raw
                                |
                                v
                     Phase 2 Flow Engine
                                |
                         +------+------+
                         |             |
                         v             v
                  Flow Detector   flow:signals
                         |
                         v
                  AI / ML research
```

## Important Groww limitation

Groww's public feed currently gives **LTP and aggregated market depth**, not a direct exchange-wide public trade tape identifying every market participant's buy/sell aggressor. Therefore our flow engine infers aggression from observable quote/depth/LTP changes rather than claiming direct institutional-order visibility. Groww documents up to 1,000 live-feed subscriptions and exposes option-chain OI, volume and Greeks through its APIs.

## Roadmap

0. Foundation — complete
1. Groww market-data connection + agent foundation — complete in code; requires your Groww credentials to run live
2. Options-flow detection — microstructure layer complete
2B. OI/volume/Greeks fusion + option-chain polling — next
3. Flow intelligence and scoring
4. Historical data and learning dataset
5. ML prediction
6. AI strategy discovery
7. Backtesting and walk-forward validation
8. Paper trading
9. Risk management
10. Broker execution
11. Controlled live trading
12. Autonomous research and optimization
