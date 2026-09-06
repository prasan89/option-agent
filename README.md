# AI Options Flow Agent

AI-assisted quantitative research and options-flow analysis platform for Indian index derivatives.

> **Safety:** Phase 1 is read-only market-data collection. Order placement is deliberately not implemented. API credentials and secrets must never be committed to Git.

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
- Trading remains disabled

Groww documents that its Feed supports live LTP and market-depth subscriptions for derivatives and up to 1,000 subscribed instruments. The option-chain API exposes LTP, OI, volume and Greeks. See the official Groww API docs for the current limits and fields.

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
```

### Important Groww authentication note

Groww's API-key/secret flow requires the current daily approval/token process described in its documentation. Access tokens also have an expiry. Do not hard-code or commit tokens. For local development, put the current token/credentials in `.env`; for SAP BTP, configure them as environment secrets.

## Phase 1 flow

```text
Groww API / WebSocket Feed
          |
          v
   GrowwFeedService
          |
          +------> LTP snapshots
          |
          +------> Aggregated market depth
          |
          v
     Redis Stream
      market:raw
          |
          v
  Phase 2: Flow Detection
```

### API endpoints

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
```

The `/groww/feed/start` endpoint accepts up to 1,000 Groww instrument subscriptions and starts the read-only LTP + market-depth collector. No order API is exposed by the Phase 1 application.

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
                                v
                         ML / AI Agent
                                |
                                v
                       Strategy / Risk
                                |
                                v
                         Paper Trading
                                |
                                v
                    Future broker execution
```

## Important limitation for our strategy

Groww's public feed currently gives us **LTP and aggregated market depth**, not a direct exchange-wide public trade tape identifying every market participant's buy/sell aggressor. Therefore Phase 2 must infer aggressive flow from quote/depth/LTP changes rather than claim that we can see institutional orders directly. We will validate the signal empirically before using it for any strategy.

## Roadmap

0. Foundation — complete
1. Groww market-data connection + agent foundation — complete in code; requires your Groww credentials to run live
2. Options-flow detection
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
