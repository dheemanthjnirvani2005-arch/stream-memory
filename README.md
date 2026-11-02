# Stream Memory

Stream Memory is a Twitch community memory system. This repository currently contains the Phase 1 application foundation: a FastAPI API, Supabase health integration, and a responsive React dashboard shell.

## What is implemented

- Typed FastAPI application with modular `routes`, `services`, and `database` packages.
- Pydantic settings loaded from `backend/.env`; server secrets are never passed to the frontend.
- Supabase client factory and a non-destructive database health check.
- `GET /health` reports `ok` when Supabase is reachable and `degraded` when it is unavailable or unconfigured.
- CORS restricted to the configured frontend origin and credential-aware frontend API requests.
- Dark, responsive dashboard with connection, database, and empty-memory states.
- Initial PostgreSQL schema with row-level security enabled.

Twitch OAuth, EventSub chat reception, memory capture and moderation, approved-memory retrieval, and Helix chat replies are implemented.

## Prerequisites

- Python 3.11 or newer
- Node.js LTS and npm
- A Supabase project for database-enabled health checks

## Configure environment

Copy the example file into the backend directory and fill in only the services you are ready to use:

```powershell
Copy-Item .env.example backend\.env
```

Keep `AI_PROVIDER=mock` during foundation work. Never commit `backend/.env`. Generate `SESSION_SECRET` with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and `TOKEN_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

## Set up Supabase

1. Create a Supabase project.
2. Run `001_initial_schema.sql`, `002_twitch_credentials.sql`, `003_memory_workflow.sql`, and `004_approved_memory_search.sql` in that order in the Supabase SQL Editor. If some have already been applied, inspect the schema and apply only missing operations.
3. Put its URL and backend-only secret key in `backend/.env` as `SUPABASE_URL` and `SUPABASE_SECRET_KEY`.

The browser never receives this key. The in-memory chat buffer is lost when the backend restarts; approved memories remain in Supabase.

## Run locally

Create and activate a virtual environment from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

Start the API:

```powershell
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, install and start the dashboard:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. API docs are at `http://localhost:8000/docs`; API health is at `http://localhost:8000/health`.

## Twitch local test

Register an app in Twitch Developer Console with redirect URI exactly `http://localhost:8000/auth/twitch/callback`. Set `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_REDIRECT_URI`, `SESSION_SECRET`, and `TOKEN_ENCRYPTION_KEY` in `backend/.env`. The default OAuth scopes are `user:read:chat user:write:chat`. Start the API, then visit `http://localhost:8000/auth/twitch/login` and authorize the broadcaster account that owns the test channel. The callback stores encrypted credentials, sets a signed HTTP-only session cookie, and starts EventSub. Send `!remember <topic>` as the broadcaster to request a pending suggestion.

The listener and Send Chat Message API use the broadcaster's User Access Token. After `!remember Kevin` creates a proposal, approve it using `POST /api/memories/{id}/approve` with the signed streamer session. A viewer can then ask `!lore Who is Kevin?`; only approved memories from that broadcaster are searched. The backend replies from retrieved evidence or uses the fixed no-memory response. Apply `004_approved_memory_search.sql` to enable the database search function.

## Test

Run backend tests from the repository root after activating the virtual environment:

```powershell
$env:PYTHONPATH = "backend"
pytest backend/tests
```

Run frontend tests:

```powershell
cd frontend
npm test
```

## Project layout

- `backend/app/config.py` — environment configuration
- `backend/app/database/` — Supabase client and health check
- `backend/app/routes/` — FastAPI endpoint modules
- `backend/app/services/` — application services
- `backend/sql/` — Supabase migrations
- `frontend/src/` — React dashboard, components, API client, and hooks
- `docs/PRD.md` — product requirements
