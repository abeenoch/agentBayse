# Bayse AI Trading Agent

FastAPI backend + React/Vite frontend for running an autonomous Bayse Markets agent. This README is aimed at frontend devs who need to call the backend API as well as operators who configure the agent.

---

## Run locally

```bash
# Backend
cd backend
python -m venv venv
venv\Scripts\activate  # on Windows
pip install -r requirements.txt
cp .env.example .env   # fill keys
uvicorn app.main:app --reload --port 8000

# Frontend
cd ../frontend
npm install
npm run dev   # http://localhost:5173
```

Key env flags (backend/.env):
- `BAYSE_API_BASE_URL` (default `https://relay.bayse.markets/v1`)
- `BAYSE_PUBLIC_KEY`, `BAYSE_SECRET_KEY`, `BAYSE_DEFAULT_CURRENCY`
- `AI_PROVIDER=groq|gemini|mock`, plus `GROQ_API_KEY` or `GEMINI_API_KEY`
- `TAVILY_API_KEY` for real web search
- `MOCK_MODE` (false for live Bayse)
- Scheduler knobs: `AGENT_SCAN_INTERVAL_SECONDS` (default 900), `AGENT_EVENT_PAGE_SIZE` (50), `AGENT_EVENT_PAGES` (3), `AGENT_REANALYZE_MINUTES` (25)

---

## Backend API (base `http://localhost:8000`)

### Auth
- `POST /auth/token` — form data `username`, `password` (from env). Returns `{"access_token","token_type"}`.
- `GET /auth/me` — requires `Authorization: Bearer <token>`.
Note: most routes are open in this build; keep token handling if you re-enable auth.

### Markets
- `GET /markets` — list events; query: `category, status, keyword, page, size`. Returns `{events, pagination}` straight from Bayse.
- `GET /markets/{event_id}` — full event.
- `GET /markets/slug/{slug}` — event by slug.
- `GET /markets/trending` — trending events.
- `GET /markets/series` — list event series.
- `GET /markets/{event_id}/price-history` — params: `timePeriod=12H|24H|1W|1M|1Y`, `outcome=YES|NO`, optional `marketId[]` array.
- `GET /markets/orderbook` — params: `outcomeId[]` (one or more), `depth` (default 10). Returns Bayse `/pm/books`.
- `GET /markets/{market_id}/ticker` — params: `outcome` or `outcomeId`; returns last/ bid/ask/spread/vol.
- `GET /markets/{market_id}/trades` — params: `limit` (default 20). CLOB trades only.

### Trades / Orders
- `POST /trades` — body params via query: `event_id, market_id, side (BUY/SELL), outcome (YES/NO), amount, currency`. Places market order (Bayse signed).
- `GET /trades` — list orders; query `status, page, size`.
- `DELETE /trades/{order_id}` — cancel order.

### Portfolio
- `GET /portfolio` — holdings and P&L.
- `GET /portfolio/orders` — same as `/trades`.
- `GET /portfolio/activities` — trading history; query `type` (buys|sells|limits|payout), `page`, `size`.
- `GET /portfolio/assets` — wallet balances/assets.

### Agent controls
- `POST /agent/analyze` — params `event_id`, optional `market_id`; runs a one-off analysis and returns a signal JSON.
- `GET /agent/signals` — query `limit`; newest-first list of stored signals.
- `POST /agent/approve` — param `signal_id`; executes the signal order and marks executed.
- `POST /agent/signals/clear` — delete all stored signals.
- `GET /agent/status` — simple heartbeat.
- `GET /agent/config` — returns current autonomous settings: `auto_trade, categories[], max_trades_per_hour, max_trades_per_day, balance_floor`.
- `POST /agent/config` — update any of the above (JSON body). Changes take effect next scheduler cycle.

### Search
- `GET /search?q=...` — uses Tavily if API key is set; otherwise placeholder results.

### WebSocket
- `ws://localhost:8000/ws/live` — sends `{"type":"heartbeat"}` on ping; broadcasts `{"type":"new_signal","data":{...}}` when agent emits a signal.

---

## How the agent runs
- Scheduler starts on app startup. Interval: `AGENT_SCAN_INTERVAL_SECONDS` (default 15 min).
- It fetches up to `AGENT_EVENT_PAGES` pages of events (size `AGENT_EVENT_PAGE_SIZE`), applies category filter from `/agent/config`, and queues all markets.
- Before each market analysis it enforces trade caps: `max_trades_per_hour`, `max_trades_per_day`, and optional `balance_floor`.
- Analysis steps: lightweight web search → LLM JSON decision → risk guard (EV>0, confidence>=60, stake<=max position) → optional balance floor → store signal → broadcast WebSocket. If `agent_auto_trade` is false, cycle is skipped entirely.
- One-off analyses can be triggered via `POST /agent/analyze` regardless of scheduler.

---

## Frontend pointers
- Base API URL comes from `VITE_API_URL` (defaults to `http://localhost:8000`).
- Tabs: Dashboard (portfolio cards, latest signals, active markets, prediction history), Markets (search/browse, charts, orderbook, ticker, trades), Signals (approve/clear), Settings (autonomous config).
- WebSocket client can subscribe to `/ws/live` to get push updates for new signals.

---

## Troubleshooting
- No signals? Check `/agent/config` that `auto_trade` is true; watch backend logs for “Auto-trade disabled; skipping cycle.”
- Column errors? Delete `backend/bayse_agent.db` and restart; init_db will recreate and patch columns.
- Charts not rendering? Ensure `npm install` pulled `recharts`.
- Bayse 400 on orderbook: happens if outcomeId[] missing; frontend uses `/markets/orderbook` with outcome IDs from event.
