# Bayse AI Trading Agent

An autonomous prediction-market trading agent for [Bayse Markets](https://bayse.markets). The agent analyses open markets, fetches live news, reasons about probability using an LLM, and places bets automatically — with configurable risk controls.

---

## Architecture

```
bayseAgent/
├── backend/          # FastAPI + APScheduler + SQLAlchemy
│   ├── app/
│   │   ├── main.py               # App factory, router registration, startup
│   │   ├── config.py             # All settings via pydantic-settings (.env)
│   │   ├── database.py           # Async SQLAlchemy engine, session, init_db
│   │   ├── dependencies.py       # JWT auth dependency (OAuth2)
│   │   ├── websocket_manager.py  # Broadcast hub for live frontend updates
│   │   ├── models/               # SQLAlchemy ORM models
│   │   │   ├── agent_config.py   # Configurable agent parameters (DB-stored)
│   │   │   ├── analysis_state.py # Per-market cooldown tracking
│   │   │   ├── event_market.py   # Event↔market relationship + status
│   │   │   ├── signal.py         # Generated trading signals
│   │   │   └── trade.py          # Executed orders
│   │   ├── routers/
│   │   │   ├── agent.py          # /agent/* — signals, config, approve, clear
│   │   │   ├── auth.py           # /auth/token, /auth/me
│   │   │   ├── markets.py        # /markets — proxies Bayse events list
│   │   │   ├── portfolio.py      # /portfolio, /portfolio/positions, /portfolio/assets
│   │   │   ├── trades.py         # /trades
│   │   │   ├── search.py         # /search
│   │   │   ├── websocket.py      # /ws/live — WebSocket endpoint
│   │   │   └── webhook.py        # /webhook — inbound Bayse webhooks
│   │   ├── services/
│   │   │   ├── ai_agent.py       # Core agent: analysis, prompt building, signal generation
│   │   │   ├── bayse_client.py   # Bayse REST API client (HMAC-signed)
│   │   │   ├── llm_client.py     # LLM abstraction (Groq/Gemini/OpenAI/Anthropic)
│   │   │   ├── rag.py            # ChromaDB RAG: ingest news, query context
│   │   │   ├── risk_guard.py     # Pre-trade risk checks (EV, confidence, balance)
│   │   │   ├── scheduler.py      # APScheduler jobs: agent cycle, order monitor
│   │   │   ├── sniper.py         # Short-interval market watcher + stop-loss
│   │   │   ├── storage.py        # Signal CRUD
│   │   │   ├── trade_executor.py # Place orders on Bayse, create Trade records
│   │   │   ├── web_search.py     # Tavily search wrapper
│   │   │   ├── analysis.py       # EV, Kelly, Sharpe, drawdown calculations
│   │   │   └── config_service.py # AgentConfig DB read/write
│   │   └── utils/
│   │       ├── auth.py           # JWT create/verify
│   │       ├── logger.py         # Structured logger
│   │       └── migrations.py     # Idempotent startup DDL migrations
│   ├── tests/
│   ├── requirements.txt
│   └── .env                      # Local secrets (never commit)
└── frontend/         # React + Vite + TailwindCSS
    └── src/
        ├── App.tsx               # Router setup
        ├── pages/
        │   ├── Dashboard.tsx     # Live overview: wallet, positions, activity
        │   ├── Markets.tsx       # Open markets browser
        │   ├── Signals.tsx       # Generated signals with approve/clear
        │   └── Settings.tsx      # Agent config editor
        ├── hooks/                # React Query data hooks
        └── lib/
            ├── api.ts            # Axios instance with JWT interceptor
            └── auth.ts           # Token storage helpers
```

---

## How the Agent Works

### 1. Market Discovery (every 15 min)
`scheduler.run_agent_cycle()` calls `populate_queue()` which fetches open events from a list of Bayse series slugs (crypto 5min/15min/1h, FX 1h, commodities). Each market is queued for analysis.

### 2. Cooldown Check
Before analysing a market, the agent checks:
- In-memory: was this market analysed in the last `AGENT_REANALYZE_MINUTES`?
- DB: same check via `AnalysisState` table

### 3. Context Gathering
For each market the agent collects:
- YES/NO prices from the event data
- Closing time → time remaining → timeframe classification (short/medium/long)
- Live web search via Tavily (time-aware query: "price now today intraday" for <2h markets)
- RAG retrieval: top-5 relevant chunks from ChromaDB (built from previous searches)
- Prior signal history for this market (last 5 signals with outcomes)
- Portfolio state: wallet balance, deployed capital, open positions, today's bet count, recent W/L record

### 4. LLM Decision
Everything is packed into a structured prompt and sent to the configured LLM. The system prompt instructs the model to:
1. Read portfolio state first
2. Consider the timeframe (ignore year-end forecasts for 1h markets)
3. Argue the NO case before the YES case
4. Only bet when EV > fee threshold AND confidence ≥ 65

The LLM returns a JSON signal with: `signal`, `confidence`, `estimated_probability`, `expected_value`, `reasoning`, `suggested_stake`, `risk_level`.

### 5. Post-LLM Normalization
The agent recalculates:
- Probability direction (flips for BUY_NO)
- EV using live prices: `(prob × (100 - price) - (1 - prob) × price) × stake / 100`
- Stake via fractional Kelly: `deployable_capital / max_open_positions` (1.5× for confidence ≥ 80)

### 6. Risk Guard
Two checks before saving:

**`risk_guard()`** (synchronous):
- Stake ≤ `AGENT_MAX_POSITION_SIZE`
- Balance reserve: stake must not exceed `balance × (1 - reserve_pct) - deployed`
- EV ≥ `(₦5 fee / stake) × 100` (scales with stake size)
- Confidence ≥ `min_confidence`

**`check_trade_limits()`** (async, uses live Bayse portfolio):
- `len(outcomeBalances) < max_open_positions`

### 7. Auto-Trade
If `auto_trade=true` and signal passes all checks, `execute_signal()` places a MARKET order on Bayse and creates a `Trade` record.

### 8. Sniper (every 30 sec)
Watches markets closing within `SNIPE_OBSERVE_SECONDS` (default 5 min). Uses a faster prompt with live ticker data. Adds `entry_timing` (ENTER_NOW / WAIT / SKIP). Prevents duplicate execution via `_executed_markets` set.

### 9. Stop-Loss (every 15 sec)
Reads `outcomeBalances` from Bayse portfolio for accurate current values. If `(cost - current_value) / cost ≥ STOP_LOSS_PCT`, places a SELL order.

### 10. Order Monitor (every 5 min)
Polls `GET /pm/orders/{id}` and `GET /pm/activities?type=payout` to resolve WIN/LOSS and update P&L.

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- API keys: Bayse, Groq (or Gemini/OpenAI/Anthropic), Tavily

---

## Setup

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt

cp .env.example .env           # fill in your keys
```

Create the database:
```sql
CREATE DATABASE agent_bayse;
```

Start the server:
```bash
uvicorn app.main:app --reload
```

The server runs on `http://localhost:8000`. On first startup it creates all tables and runs idempotent migrations automatically.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173`.

---

## Environment Variables

All settings live in `backend/.env`. Copy `.env.example` and fill in:

| Variable | Description | Default |
|---|---|---|
| `APP_SECRET_KEY` | JWT signing secret | required |
| `ADMIN_USERNAME` | Dashboard login username | required |
| `ADMIN_PASSWORD` | Dashboard login password | required |
| `DATABASE_URL` | PostgreSQL async URL | required |
| `BAYSE_PUBLIC_KEY` | Bayse API public key | required |
| `BAYSE_PRIVATE_KEY` | Bayse API private key (HMAC signing) | required |
| `BAYSE_DEFAULT_CURRENCY` | Trading currency | `NGN` |
| `AI_PROVIDER` | `groq` / `gemini` / `openai` / `anthropic` | `groq` |
| `GROQ_API_KEY` | Groq API key | — |
| `GROQ_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `GEMINI_API_KEY` | Google Gemini API key | — |
| `TAVILY_API_KEY` | Tavily search API key | — |
| `SEARCH_INCLUDE_DOMAINS` | Comma-separated preferred domains | finance.yahoo.com,reuters.com,... |
| `SEARCH_EXCLUDE_DOMAINS` | Comma-separated blocked domains | coincodex.com,... |
| `AGENT_AUTO_TRADE` | Enable autonomous order placement | `false` |
| `AGENT_MAX_OPEN_POSITIONS` | Max simultaneous open bets | `3` |
| `AGENT_MIN_CONFIDENCE` | Minimum LLM confidence to trade (0–100) | `65` |
| `AGENT_BALANCE_RESERVE_PCT` | Fraction of wallet kept untouched | `0.30` |
| `AGENT_MAX_POSITION_SIZE` | Max stake per bet (₦) | `5000` |
| `AGENT_SCAN_INTERVAL_SECONDS` | Agent cycle frequency | `900` |
| `AGENT_REANALYZE_MINUTES` | Cooldown before re-analysing a market | `50` |
| `AGENT_SERIES_SLUGS` | Comma-separated series to scan (empty = all) | — |
| `SNIPE_SERIES_SLUGS` | Series for the sniper | `crypto-btc-5min,...` |
| `SNIPE_OBSERVE_SECONDS` | How far out sniper starts watching | `300` |
| `STOP_LOSS_PCT` | Loss fraction to trigger sell | `0.35` |
| `MOCK_MODE` | Use mock responses (no real API calls) | `true` |
| `FRONTEND_ORIGIN` | CORS allowed origin | `http://localhost:5173` |

---

## API Reference

All endpoints are documented at `http://localhost:8000/docs` (Swagger UI).

Key endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/token` | Get JWT token (form: username/password) |
| `GET` | `/markets` | List open Bayse events |
| `GET` | `/portfolio` | Portfolio summary from Bayse |
| `GET` | `/portfolio/positions` | Live open positions (outcomeBalances) |
| `GET` | `/portfolio/assets` | Wallet balances per currency |
| `GET` | `/portfolio/activities` | Recent activity feed |
| `GET` | `/agent/signals` | Recent signals (active last 3h by default) |
| `POST` | `/agent/approve` | Manually execute a PENDING signal |
| `GET` | `/agent/config` | Read agent config |
| `POST` | `/agent/config` | Update agent config |
| `POST` | `/agent/signals/clear` | Delete all signals |
| `POST` | `/agent/trades/clear-stale` | Mark ghost DB trades as STALE |
| `GET` | `/health` | Health check |
| `WS` | `/ws/live` | WebSocket for real-time signal/trade events |

---

## Database Schema

| Table | Purpose |
|---|---|
| `signals` | Every generated signal (BUY_YES/BUY_NO/HOLD/AVOID) with EV, confidence, reasoning, resolution |
| `trades` | Every placed order with Bayse order ID, status, P&L |
| `agent_config` | Single-row config: auto_trade, max_open_positions, min_confidence, balance_reserve_pct |
| `analysis_state` | Per-market last-analysed timestamp (cooldown) |
| `event_market` | Event↔market pairs with PENDING/COMPLETED status |
| `market_snapshot` | (reserved) |
| `portfolio_snapshot` | (reserved) |

Migrations run automatically at startup via `utils/migrations.py` — no Alembic needed.

---

## LLM Providers

Switch provider via `AI_PROVIDER` in `.env`:

| Provider | Key variable | Notes |
|---|---|---|
| `groq` | `GROQ_API_KEY` | Recommended. Fast, free tier available. Use `llama-3.3-70b-versatile`. |
| `gemini` | `GEMINI_API_KEY` | Google Gemini 2.5 Flash. |
| `openai` | `OPENAI_API_KEY` | Uses `gpt-4o-mini`. |
| `anthropic` | `ANTHROPIC_API_KEY` | Uses `claude-3-5-haiku-latest`. |
| `mock` | — | Set `MOCK_MODE=true`. Returns hardcoded BUY_YES signal. |

---

## RAG Knowledge Base

The agent uses ChromaDB (in-process, persisted to `./chroma_db/`) as a vector store. On each analysis:

1. Tavily searches for `"{market title} price now today intraday"` (for short markets)
2. Results are scraped and chunked (400 words, 80-word overlap)
3. Forecast/prediction sites are filtered out before ingestion
4. Top-5 relevant chunks are retrieved and injected into the LLM prompt

The collection grows over time. To reset it, delete the `chroma_db/` directory.

---

## Risk Management

The agent enforces multiple layers before placing any bet:

1. **EV floor** — EV must exceed `(₦5 Bayse fee / stake) × 100`. Scales with stake size.
2. **Confidence threshold** — Default 65%. Configurable in Settings.
3. **Balance reserve** — 30% of wallet is always kept untouched. Configurable.
4. **Position cap** — Max 3 simultaneous open bets (uses live Bayse `outcomeBalances`).
5. **Stop-loss** — Sells if position loses ≥ 35% of cost (uses live portfolio values).
6. **50/50 skip** — Markets at exactly 50/50 with no useful news are skipped entirely.

---

## Running Tests

```bash
cd backend
pytest tests/ -v
```

---

