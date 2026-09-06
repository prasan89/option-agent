# AI Options Flow Agent

AI-assisted quantitative research and options-flow analysis platform for Indian derivatives.

> **Safety:** The system is read-only research infrastructure. It infers market flow from public market-data observations, does not identify institutional traders, and does not place orders. API credentials and secrets must never be committed to Git.

## Phase 1 — Groww market data

Implemented:

- Groww Python SDK integration
- Access-token or API-key/secret authentication
- Read-only profile connectivity
- NIFTY option-chain retrieval with Greeks, OI and volume
- F&O instrument-master access
- LTP retrieval
- Groww live LTP and aggregated market-depth feed
- Raw observations in Redis Stream `market:raw`

## Phase 2 — Options-flow detection

Implemented:

- Redis-stream flow engine
- LTP-vs-best-bid/ask aggression inference
- Bid/ask depth imbalance
- Price-momentum confirmation
- Explainable 0–100 flow score
- BUY/SELL directional classification
- Evidence labels
- Redis Stream `flow:signals`

## Phase 3 — Flow intelligence and scoring

Implemented:

- Complete active NSE F&O instrument-master scanning, not NIFTY-only
- One-minute F&O scanner cadence
- Batched LTP collection
- One-minute price-movement/activity ranking
- Redis Stream `fno:rankings`
- Flow intelligence scoring engine consuming `flow:signals`
- Confidence-weighted intelligence score from -100 to +100
- BULLISH / BEARISH / NEUTRAL bias
- Explainable evidence enrichment
- `GET /scanner/latest` for the latest F&O activity ranking
- `GET /intelligence-score/top` for highest-scoring flow signals
- Start/stop/status APIs for scanner and intelligence engine

Phase 3 remains research-only. The one-minute scanner establishes the market-wide candidate universe; the flow engine provides microstructure evidence; the intelligence score ranks signals for later backtesting and ML work.

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

GET  /scanner/status
GET  /scanner/latest?limit=25
POST /scanner/start
POST /scanner/stop

GET  /intelligence/status
POST /intelligence/start
POST /intelligence/stop
GET  /intelligence-score/status
GET  /intelligence-score/top?limit=20
POST /intelligence-score/start
POST /intelligence-score/stop
```

## Phase 3 data flow

```text
                 Groww F&O Instrument Master
                           |
                           v
                  Complete NSE F&O Universe
                           |
                           v
                    One-minute Scanner
                           |
                 +---------+---------+
                 |                   |
                 v                   v
             LTP batches       fno:rankings
                                     |
Groww Live Feed                    ranking
     |                               |
     v                               |
 market:raw                          |
     |                               |
     v                               |
 Flow Engine                         |
     |                               |
     v                               |
 flow:signals -----------------------+
                 |
                 v
        Intelligence Score Engine
                 |
                 v
        intelligence:signals
                 |
                 v
          Research / AI layer
```

## Local setup

1. Copy `.env.example` to `.env`.
2. Configure Groww credentials locally; never commit them.
3. Start the stack:

```bash
docker compose up --build
```

4. Open `http://localhost:8000/docs`.
5. Start the feed, flow engine, F&O scanner and intelligence score engine from the API.

## Important Groww limitation

Groww's public feed provides LTP and aggregated market depth rather than an exchange-wide public tape identifying every market participant. Flow signals are therefore inference from observable market data, not direct institutional-order detection. Provider/API limits must be respected when scanning the complete F&O universe; the scanner uses batching and remains read-only.

## Roadmap

0. Foundation — complete
1. Groww market-data connection + agent foundation — complete in code
2. Options-flow detection — complete
2B. OI/volume/Greeks fusion — implemented for option-chain intelligence
3. Flow intelligence and market-wide scoring — **complete**
4. Historical data and learning dataset — next
5. ML prediction
6. AI strategy discovery
7. Backtesting and walk-forward validation
8. Paper trading
9. Risk management
10. Broker execution
11. Controlled live trading
12. Autonomous research and optimization
