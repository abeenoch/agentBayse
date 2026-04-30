# Bayse AI Trading Agent

FastAPI backend + React/Vite frontend for browsing Bayse Markets, generating AI-backed signals, and optionally auto-executing trades.

## What is in this repo

- `backend/` contains the FastAPI app, scheduler, Bayse client, risk checks, and persistence layer.
- `frontend/` contains the React dashboard for markets, signals, portfolio, and agent settings.
- The backend starts an APScheduler job on startup so the agent can scan markets automatically.

## Local Setup

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend defaults to `http://localhost:5173` and calls the backend at `http://localhost:8000` unless `VITE_API_URL` is set.

## Configuration

The backend reads settings from `backend/.env` through `pydantic-settings`.

### Core

- `APP_SECRET_KEY`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `JWT_ISSUER` - defaults to `bayse-agent`
- `JWT_AUDIENCE` - defaults to `bayse-agent-web`
- `DATABASE_URL` - defaults to PostgreSQL at `postgresql+asyncpg://postgres:postgres@localhost:5432/agent_bayse`
- `FRONTEND_ORIGIN` - defaults to `http://localhost:5173`
- `WEBHOOK_SECRET`
- `MOCK_MODE` - defaults to `true`

### Bayse

- `BAYSE_API_BASE_URL` - defaults to `https://relay.bayse.markets/v1`
- `BAYSE_PUBLIC_KEY`
- `BAYSE_PRIVATE_KEY`
- `BAYSE_DEFAULT_CURRENCY` - defaults to `NGN`

### AI provider

- `AI_PROVIDER` - `mock`, `gemini`, `groq`, `openai`, or `anthropic`
- `GEMINI_API_KEY`
- `GEMINI_MODEL` - defaults to `gemini-2.5-flash`
- `GROQ_API_KEY`
- `GROQ_MODEL` - defaults to `llama-3.3-70b-versatile`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

### Search

- `SEARCH_PROVIDER` - defaults to `tavily`
- `TAVILY_API_KEY`
- `SERPAPI_KEY`
- `SEARCH_DEPTH` - defaults to `advanced`
- `SEARCH_MAX_RESULTS` - defaults to `8`
- `SEARCH_TIME_RANGE`
- `SEARCH_INCLUDE_DOMAINS`
- `SEARCH_EXCLUDE_DOMAINS`

### Agent controls

- `AGENT_AUTO_TRADE` - defaults to `false`
- `AGENT_MAX_POSITION_SIZE` - defaults to `5000`
- `AGENT_SCAN_INTERVAL_SECONDS` - defaults to `900`
- `AGENT_MAX_DAILY_TRADES` - defaults to `20`
- `AGENT_MIN_CONFIDENCE` - defaults to `20`
- `AGENT_MAX_OPEN_POSITIONS` - defaults to `3`
- `AGENT_BALANCE_RESERVE_PCT` - defaults to `0.30`
- `AGENT_IGNORE_BALANCE_CHECK` - defaults to `false`
- `AGENT_EVENT_PAGE_SIZE` - defaults to `50`
- `AGENT_EVENT_PAGES` - defaults to `4`
- `AGENT_REANALYZE_MINUTES` - defaults to `25`
- `AGENT_SERIES_SLUGS`

### Sniper and stop-loss

- `SNIPE_OBSERVE_SECONDS` - defaults to `300`
- `SNIPE_MIN_SECONDS` - defaults to `8`
- `SNIPE_SERIES_SLUGS`
- `STOP_LOSS_PCT` - defaults to `0.35`

## Backend API

Base URL: `http://localhost:8000`

### Health

- `GET /health` - returns a simple status payload.

### Auth

- `POST /auth/token` - OAuth2 password login using `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
- `GET /auth/me` - returns the current username when a bearer token is present.
- Protected routes require `Authorization: Bearer <token>` after login.

### Markets

- `GET /markets` - lists events. Query params: `category`, `status`, `keyword`, `page`, `size`.
- `GET /markets/trending`
- `GET /markets/series`
- `GET /markets/orderbook` - query uses repeated `outcomeId[]` values and optional `depth`.
- `GET /markets/slug/{slug}`
- `GET /markets/{event_id}`
- `GET /markets/{event_id}/price-history` - params: `timePeriod`, `outcome`, repeated `marketId[]`
- `GET /markets/{market_id}/ticker` - params: `outcome` or `outcomeId`
- `GET /markets/{market_id}/trades` - params: `limit`

Note: when `category` is omitted, the backend defaults the markets list to `finance`.

### Trades

- `POST /trades` - places a Bayse order. Query/body fields used by the handler: `event_id`, `market_id`, `side`, `outcome`, `amount`, `currency`.
- `GET /trades` - lists orders. Query params: `status`, `page`, `size`.
- `DELETE /trades/{order_id}` - cancels an order.

### Portfolio

- `GET /portfolio` - full portfolio payload from Bayse.
- `GET /portfolio/orders` - convenience alias for orders.
- `GET /portfolio/activities` - params: `type`, `page`, `size`.
- `GET /portfolio/positions` - normalized open positions derived from portfolio outcome balances.
- `GET /portfolio/assets` - wallet balances and assets.

### Agent

- `POST /agent/analyze` - params: `event_id`, optional `market_id`; runs one-off analysis.
- `GET /agent/signals` - params: `limit`, `page`, `event_id`, `all`.
- `POST /agent/approve` - params: `signal_id`, optional `amount`; executes a stored signal.
- `POST /agent/trades/clear-stale` - marks stale executed trades that have no resolution.
- `GET /agent/status`
- `GET /agent/config`
- `POST /agent/config`

### Search

- `GET /search?q=...` - uses Tavily when configured; otherwise returns placeholder results.

### WebSocket

- `ws://localhost:8000/ws/live` - returns `{"type":"heartbeat"}` when pinged and broadcasts events such as `new_signal`, `order_resolved`, `snipe_executed`, and `stop_loss_triggered`.

### Webhook

- `POST /webhook/order` - Bayse order resolution webhook used to update trade and signal state.
- Requires `X-Webhook-Secret` to match `WEBHOOK_SECRET`.

## How the agent works

- On startup, the app creates the database schema and starts the scheduler.
- The regular agent cycle runs every `AGENT_SCAN_INTERVAL_SECONDS`.
- It loads configured series slugs, applies the category filter from `/agent/config`, and queues market analyses.
- Before analyzing each market, it enforces the open-position cap and balance checks.
- Each analysis combines live Bayse data, web search results, RAG context, portfolio state, and an LLM decision.
- Actionable `BUY_YES` and `BUY_NO` signals are stored and broadcast over WebSocket.
- If `auto_trade` is enabled in agent config, saved actionable signals can be executed automatically after passing the risk checks.
- A separate sniper loop watches short-interval markets every 30 seconds.
- A stop-loss monitor checks open positions every 15 seconds and exits when the configured loss threshold is hit.

## Frontend

The UI has four routes:

- `Dashboard` - portfolio summary, active positions, recent markets, activity feed, and signal details.
- `Markets` - browse events, filter by category, inspect ticker, order book, recent trades, and price history.
- `Signals` - review stored signals, inspect rationale and sources, and approve trades manually.
- `Settings` - adjust autonomous trading config such as categories, max open positions, balance floor, and minimum confidence.

The frontend uses `VITE_API_URL` when set, otherwise it talks to `http://localhost:8000`.

## Testing

Backend:

```bash
cd backend
pytest
```

Frontend:

```bash
cd frontend
npm run build
```

## Notes

- `MOCK_MODE=true` keeps the Bayse client and LLM flow offline-friendly for local development.
- In non-mock deployments, set `WEBHOOK_SECRET` so the order webhook is accepted.
- The UI now shows a login screen and stores the bearer token locally after `POST /auth/token`.
- The `Signals` page still calls `POST /agent/signals/clear`, but the backend currently exposes `POST /agent/trades/clear-stale` instead. If you want a clear-signals action, that endpoint needs to be added or the UI needs to be updated.
- The backend markets endpoint only defaults to `finance` when no category is supplied. The frontend can still request other categories explicitly.
- Security review documents:
  - [Audit report](docs/security-audit-report.md)
  - [Remediation plan](docs/remediation-plan.md)

## Troubleshooting

- If login fails, check `APP_SECRET_KEY`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD`.
- If the app returns mostly empty data, confirm `MOCK_MODE` is set the way you expect and the Bayse credentials are valid.
- If the agent does not generate signals, verify `MOCK_MODE`, `AGENT_MIN_CONFIDENCE`, and the open-position caps in `/agent/config`.
- If the frontend does not connect, confirm `FRONTEND_ORIGIN` on the backend and `VITE_API_URL` on the frontend.
