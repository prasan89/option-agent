# Architecture

## Phase 0

The repository starts with a small, portable foundation:

- `backend`: FastAPI service and configuration
- PostgreSQL: persistent application data
- Redis: real-time state/message transport for later market-data ingestion
- Docker Compose: reproducible local development

## Design principles

1. **Data first:** retain raw market events before deriving signals.
2. **Deterministic calculations:** prices, Greeks, scores, P&L and risk limits are computed by code, not by an LLM.
3. **AI as an agent:** the LLM orchestrates research tools, interprets evidence and generates hypotheses.
4. **Paper before live:** execution remains disabled until backtesting, walk-forward validation and paper trading establish evidence.
5. **Secrets out of source control:** Groww/API/LLM credentials are environment or platform secrets.
6. **Portable services:** local Docker development should remain deployable to SAP BTP Cloud Foundry and movable to another cloud later.

## Target E2E flow

```text
Groww WebSocket/API
        |
        v
Market Data Collector
        |
        +--> Redis (real-time state)
        |
        +--> PostgreSQL/TimescaleDB (historical data)
        |
        v
Flow Detector
        |
        v
Flow Scoring + Context
        |
        +--> ML prediction
        |
        v
AI Research/Strategy Agent
        |
        v
Backtest -> Walk-forward -> Paper Trading
        |
        v
Risk Engine
        |
        v
Broker Execution (future; disabled initially)
```
