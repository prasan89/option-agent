# AI Options Flow Agent

AI-assisted quantitative research and options-flow analysis platform for Indian derivatives.

> **Safety:** The system is read-only research infrastructure. It infers market flow from public market-data observations, does not identify institutional traders, and does not place orders. API credentials and secrets must never be committed to Git.

## Phase 7 — Market-wide F&O opportunity engine — implemented on branch

The strategy layer now ranks candidates across the active F&O scanner universe instead of treating NIFTY as the only instrument. The existing scanner already consumes the active NSE F&O instrument master, live LTP/depth and targeted option-chain enrichment. fileciteturn7file0

Implemented:
- Market-wide candidate ranking API under `/fno`
- Top-10 candidate request with portfolio-aware selection
- Direction/option-type agreement filter
- Existing upstream opportunity-score preservation
- Minimum risk/reward filter
- Dynamic lot-size-aware position sizing from instrument metadata
- ₹10 lakh research capital model: ₹7 lakh active / ₹3 lakh reserve
- ₹5,000 maximum planned loss per candidate
- ₹15,000 daily loss circuit breaker
- Maximum 7 simultaneous research positions
- Maximum 2 positions per sector when sector metadata is available
- Paper-fill and paper-close endpoints for later paper-trading workflows
- No broker order placement

### Research flow

```text
Active NSE F&O universe
        |
        v
Live LTP / depth scanner
        |
        v
Flow + activity ranking
        |
        v
Option-chain / liquidity enrichment
        |
        v
Opportunity score
        |
        v
F&O Opportunity Engine
  - direction filter
  - R:R filter
  - lot-size sizing
  - sector concentration
  - portfolio risk cap
        |
        v
Top research candidates
```

### New endpoints

```text
GET  /fno/status
POST /fno/rank
POST /fno/paper/reset
POST /fno/paper/fill
POST /fno/paper/close
```

The existing `/strategy/results` endpoint now routes its candidates through the market-wide risk-aware engine.

## Existing completed phases

### Phase 1 — Groww market data
- Groww Python SDK integration and authentication
- F&O instrument-master access
- LTP and aggregated market-depth feed
- Raw events published to the in-process event bus

### Phase 2 — Options-flow detection
- LTP-vs-best-bid/ask aggression inference
- Bid/ask depth imbalance
- Price-momentum confirmation
- Explainable flow score and directional classification

### Phase 3 — Flow intelligence and scoring
- Complete active NSE F&O instrument-master scanning
- One-minute F&O scanner cadence
- Batched LTP collection
- One-minute price-movement/activity ranking
- Confidence-weighted intelligence score

### Phase 4 — Historical data and learning dataset
- PostgreSQL-backed observations
- Leakage-safe `UNLABELED` status
- Durable dataset persistence
- Dataset status/start/stop/recent APIs

### Phase 5 — ML prediction
- Forward 1/5/15/30-minute outcome labels
- Baseline supervised classifier
- Probability of UP/DOWN prediction
- Chronological, leakage-free training architecture

### Phase 6 — Autonomous signal dashboard
- PostgreSQL signal persistence
- Five-minute signal monitor
- Dashboard and persisted signal history
- Pipeline status and controls
- Trading remains disabled

### Phase 6A — BTP Trial Redis-free architecture

PostgreSQL provides durable persistence while a bounded in-memory event bus provides asynchronous pub/sub for the single application instance.

## Deployment

The BTP Trial architecture requires PostgreSQL only. If PostgreSQL is bound to Cloud Foundry, the app reads credentials from `VCAP_SERVICES`.

```bash
cf push option-agent-api
cf restart option-agent-api
curl https://<APP_ROUTE>/health
curl https://<APP_ROUTE>/system/status
curl https://<APP_ROUTE>/fno/status
```

## Important limitations

The F&O opportunity engine is **research/paper-trading infrastructure**. It does not prove profitability, does not guarantee ₹10K per stock, and does not place orders. The ₹10K target is an objective for position construction, not an expected or guaranteed return. Historical backtesting, realistic costs/slippage, walk-forward validation and paper trading remain required before live execution.

The current event bus is in-memory and the deployment is intentionally one-instance. A restart does not preserve transient in-flight events, but durable observations, labels and signals already written to PostgreSQL remain available.

## Roadmap

0. Foundation — complete
1. Groww market-data connection + agent foundation — complete
2. Options-flow detection — complete
2B. OI/volume/Greeks fusion — implemented for option-chain intelligence
3. Flow intelligence and market-wide scoring — complete
4. Historical data and learning dataset — complete
5. ML prediction — complete
6. Autonomous signal dashboard — complete
6A. BTP Trial Redis-free pipeline — complete
7. Market-wide F&O opportunity engine — implemented on `fno-full-phase-engine`
8. AI strategy discovery — next
9. Backtesting and walk-forward validation — next
10. Paper trading — next
11. Risk management hardening — next
12. Broker execution — disabled
13. Controlled live trading — disabled
14. Autonomous research and optimization — future
