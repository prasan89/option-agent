# AI Options Flow Agent

AI-assisted quantitative research and options-flow analysis platform for Indian index derivatives.

> **Safety:** Live trading is disabled in Phase 0. API credentials and secrets must never be committed to Git.

## Phase 0 status

- FastAPI backend foundation
- Environment-based configuration
- Structured application logging
- Docker-based local development
- PostgreSQL and Redis services
- System health/version/status endpoints
- Basic automated tests
- Groww and LLM credentials reserved for later phases

## Local setup

1. Copy `.env.example` to `.env`.
2. Start the stack:

```bash
docker compose up --build
```

3. Check the API:

- `http://localhost:8000/health`
- `http://localhost:8000/version`
- `http://localhost:8000/system/status`
- `http://localhost:8000/docs`

4. Run tests locally after installing backend dependencies:

```bash
cd backend
python -m pytest
```

## Architecture

```text
Groww market data
       |
       v
Market Data Engine --> Flow Detection --> ML/AI --> Strategy --> Risk --> Execution
       |                    |              |          |           |
       +--------------------+--------------+----------+-----------+
                              |
                         PostgreSQL
                              |
                           Redis
                              |
                         React UI

AI Agent = research/orchestration layer; deterministic Python services remain authoritative for calculations and risk.
```

## Roadmap

0. Foundation
1. Groww market-data connection + AI agent
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
