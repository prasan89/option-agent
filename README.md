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

## API endpoints

```text
GET  /ml/status
GET  /ml/latest?limit=25
POST /ml/start
POST /ml/stop
POST /ml/train
POST /ml/predict
```

## Phase 5 data flow

```text
F&O Scanner + Flow Engine
          |
          v
 dataset:observations
          |
          v
 Future Outcome Labeler
          |
          +---- 1m return/direction
          +---- 5m return/direction
          +---- 15m return/direction
          +---- 30m return/direction
          |
          v
   dataset:labels
          |
          v
 Baseline ML Predictor
          |
          +---- P(UP)
          +---- P(DOWN)
          +---- prediction
          |
          v
 Research / validation
```

## Local setup

1. Copy `.env.example` to `.env`.
2. Configure Groww credentials locally; never commit them.
3. Start the stack:

```bash
docker compose up --build
```

4. Open `http://localhost:8000/docs`.
5. Start the market feed, flow engine, scanner, intelligence score engine, dataset collector and ML engine.

## Important limitation

The model only learns from the observations supplied to it. Groww's public feed provides LTP and aggregated market depth rather than an exchange-wide public tape identifying every participant. Flow features are therefore inference, not direct institutional-order detection. Provider/API limits must be respected.

## Roadmap

0. Foundation — complete
1. Groww market-data connection + agent foundation — complete
2. Options-flow detection — complete
2B. OI/volume/Greeks fusion — implemented for option-chain intelligence
3. Flow intelligence and market-wide scoring — complete
4. Historical data and learning dataset — complete
5. ML prediction — **complete**
6. AI strategy discovery — next
7. Backtesting and walk-forward validation
8. Paper trading
9. Risk management
10. Broker execution
11. Controlled live trading
12. Autonomous research and optimization
