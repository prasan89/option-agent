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

## Phase 4 — Historical data and learning dataset

Implemented:
- Historical observation collector
- Normalized model-ready observation records
- Explicit `UNLABELED` status to prevent future-information leakage
- Redis Stream `dataset:observations`
- Dataset status/start/stop/recent APIs

## Phase 5 — ML prediction

Implemented:
- Forward outcome labeler for 1/5/15/30-minute horizons
- Return and direction labels generated from future observations only
- Baseline supervised classifier using flow and market features
- Probability of UP/DOWN prediction
- Explicit model readiness state
- ML engine consuming historical observations
- Training API and prediction API
- ML status integrated into `/system/status`
- `numpy` and `scikit-learn` dependencies

The ML layer is intentionally research-only. It does not place orders and does not imply that a prediction is profitable. Training must use chronological, leakage-free datasets and should be followed by walk-forward validation before any trading decision.

## Phase 6 — Autonomous signal dashboard

Implemented:
- PostgreSQL signal persistence
- Five-minute signal monitor
- Duplicate-safe signal keys
- Automatic startup of the complete research pipeline
- Active nearest-expiry NIFTY option feed selection, capped at Groww's 1,000-instrument subscription limit
- Pipeline status API at `/pipeline/status`
- Manual pipeline controls at `/pipeline/start` and `/pipeline/stop`
- Research dashboard at `/dashboard`
- Dashboard auto-refresh and persisted signal history
- Database availability and pipeline-stage visibility
- Trading remains disabled

## Data flow

```text
Groww live feed
      |
      v
 market:raw
      |
      v
 Flow Engine
      |
      v
 flow:signals
      |
      v
 Intelligence Score
      |
      v
 intelligence:signals
      |
      +--------------------+
      |                    |
      v                    v
Dataset Collector     5-min Signal Monitor
      |                    |
      v                    v
ML / labels          PostgreSQL
                           |
                           v
                       Dashboard
```

## API endpoints

```text
GET  /health
GET  /version
GET  /system/status
GET  /pipeline/status
POST /pipeline/start
POST /pipeline/stop
GET  /dashboard
GET  /signals
GET  /signals/status
POST /signals/start
POST /signals/stop
POST /signals/run-once
GET  /ml/status
GET  /ml/latest?limit=25
POST /ml/start
POST /ml/stop
POST /ml/train
POST /ml/predict
```

## Local setup

1. Copy `.env.example` to `.env`.
2. Configure Groww credentials locally; never commit them.
3. Start the stack:

```bash
docker compose up --build
```

4. Open `http://localhost:8000/dashboard`.
5. The local configuration starts the research pipeline automatically.

## BTP deployment

The Cloud Foundry app requires reachable Redis and PostgreSQL services. Do not put credentials in Git or in this README. Configure them as Cloud Foundry environment variables:

```bash
cf set-env option-agent-api DATABASE_URL '<POSTGRES_CONNECTION_STRING>'
cf set-env option-agent-api REDIS_URL '<REDIS_CONNECTION_STRING>'
cf set-env option-agent-api AUTO_START_PIPELINE true
cf restart option-agent-api
```

Verify the application and pipeline:

```bash
cf logs option-agent-api --recent
curl https://<APP_ROUTE>/health
curl https://<APP_ROUTE>/system/status
curl https://<APP_ROUTE>/pipeline/status
```

Then open `/dashboard` on the application route.

## Important limitations

Groww's public feed provides LTP and aggregated market depth rather than an exchange-wide public tape identifying every participant. Flow features are therefore inference, not direct institutional-order detection. Provider/API limits must be respected.

The signal threshold is currently an absolute intelligence score of 60 with non-LOW confidence. Persisted signals are research observations, not trade recommendations.

The application intentionally keeps order placement disabled. Paper trading, risk controls, backtesting and walk-forward validation must be completed before considering any execution integration.

## Roadmap

0. Foundation — complete
1. Groww market-data connection + agent foundation — complete
2. Options-flow detection — complete
2B. OI/volume/Greeks fusion — implemented for option-chain intelligence
3. Flow intelligence and market-wide scoring — complete
4. Historical data and learning dataset — complete
5. ML prediction — complete
6. Autonomous signal dashboard — complete
7. AI strategy discovery
8. Backtesting and walk-forward validation
9. Paper trading
10. Risk management
11. Broker execution
12. Controlled live trading
13. Autonomous research and optimization
