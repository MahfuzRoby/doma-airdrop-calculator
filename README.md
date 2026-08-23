# Domalytics — Doma Airdrop Calculator

An unofficial backend + frontend for tracking the Doma leaderboard and estimating
airdrop allocations. The backend syncs the full leaderboard (all wallets) from
Doma's GraphQL API every hour into a local SQLite database. The frontend is a
single HTML dashboard that reads from the backend to show total points
distributed, an adjustable airdrop calculator, wallet search, and a
leaderboard table.

## What it assumes

- **Total token supply: 1,000,000,000 (1B)** — fixed, per your request.
- **Airdrop % of supply** and **FDV (fully diluted valuation)** are *not*
  published by Doma anywhere. The calculator treats both as adjustable sliders
  so you can model different scenarios — it is not a guarantee of the real
  airdrop terms.
- Wallet share = `wallet's season points / sum of season points across all
  tracked wallets`.

## Project structure

```
doma-airdrop-calculator/
  backend/
    app.py            FastAPI app, hourly scheduler, all API routes
    scraper.py         Pulls the full leaderboard from api.doma.xyz/graphql
    database.py         SQLite schema + connection helper
    requirements.txt
  frontend/
    index.html          Single-page dashboard (no build step needed)
```

## Backend setup

1. Install Python 3.9+ if you don't already have it.
2. From the `backend/` folder:
   ```
   pip install -r requirements.txt
   ```
3. Run the server:
   ```
   uvicorn app:app --reload --port 8000
   ```
4. On startup it immediately runs one full sync (this can take a couple of
   minutes since it paginates through every wallet), then re-syncs every hour
   automatically in the background. Leave this terminal running.

You can also trigger a manual sync at any time (useful for testing) by
sending a POST request:
```
curl -X POST http://localhost:8000/api/sync-now
```

### API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/status` | Last sync result (success/error, timestamp, wallet count) |
| `GET /api/stats` | Aggregate totals: wallet count, total season/weekly points, breakdown by level |
| `GET /api/history` | Hourly snapshots of total points, for charting growth over time |
| `GET /api/calculator?airdrop_percent=5&fdv=100000000` | Computes token price, pool size, value per point |
| `GET /api/wallet/{address}?airdrop_percent=5&fdv=100000000` | Single wallet's stats + estimated allocation |
| `GET /api/leaderboard?limit=50&sort_by=season_points` | Paginated leaderboard |

## Frontend setup

No build tools needed — it's a static HTML file that calls the backend over
`http://localhost:8000`.

Just open `frontend/index.html` directly in your browser (double-click it, or
right-click → Open with → your browser), **while the backend is running**.

If you'd rather serve it properly instead of opening the file directly:
```
cd frontend
python -m http.server 5500
```
then visit `http://localhost:5500`.

## Deploying (Vercel frontend + hosted backend)

Vercel works well for the frontend since it's static HTML/JS. But the
backend needs an always-on process (for the hourly APScheduler sync and the
persistent SQLite file) — Vercel's serverless functions spin down between
requests, so the scheduler wouldn't reliably fire. Use a host built for
long-running processes instead: **Railway** or **Render** are the easiest.

### 1. Deploy the backend (example: Railway)

1. Push this project to a GitHub repo.
2. In Railway, "New Project" → "Deploy from GitHub repo" → select the repo,
   set the **root directory to `backend/`**.
3. Railway auto-detects the `Procfile` and `requirements.txt`. It builds and
   starts the app automatically.
4. Add an environment variable `DOMA_API_KEY` with your captured key
   (don't leave it hardcoded in `app.py` for a public deploy).
5. **Important**: Railway's filesystem is ephemeral on redeploys unless you
   attach a persistent volume — add one and mount it at `/app` (or wherever
   `doma_data.db` lives) so your synced data survives restarts.
6. Once deployed, you'll get a URL like `https://your-app.up.railway.app`.
   Test it: `https://your-app.up.railway.app/api/stats`.

Render works almost identically (New → Web Service → same root dir/start
command from the Procfile, plus a persistent disk for the SQLite file).

### 2. Deploy the frontend to Vercel

1. In `frontend/index.html`, replace:
   ```html
   <script>window.DOMA_API_BASE = "http://localhost:8000";</script>
   ```
   with your real backend URL:
   ```html
   <script>window.DOMA_API_BASE = "https://your-app.up.railway.app";</script>
   ```
2. Push to GitHub (or drag-and-drop the `frontend/` folder into Vercel's
   dashboard).
3. In Vercel: "New Project" → import the repo → set **root directory to
   `frontend/`** (or use the included `vercel.json` at the project root if
   deploying the whole repo as one project) → Deploy. No build step needed,
   it's static.

### 3. Lock down CORS

Once both are live, go back to `backend/app.py` and change:
```python
allow_origins=["*"],
```
to your actual Vercel domain, e.g.:
```python
allow_origins=["https://your-project.vercel.app"],
```
Right now it's wide open (`*`) for local dev — fine while testing, but worth
narrowing once this is public so random sites can't hit your API from a
browser.

## Important notes

- **API key**: the backend uses an `Api-Key` header value that was captured
  from Doma's own frontend via browser devtools — it's the same key their web
  app sends for every visitor, not a personal credential. If Doma rotates
  this key, syncs will start failing with an "API Key is missing/invalid"
  error in the terminal. To fix: repeat the devtools steps (Network tab →
  find a `graphql` request → Headers → `Api-Key`) and either update the
  default in `backend/app.py` or set it via environment variable:
  ```
  set DOMA_API_KEY=v1....        (Windows)
  export DOMA_API_KEY=v1....     (Mac/Linux)
  ```
- **Rate limiting / ToS**: this pulls the full leaderboard (thousands of
  wallets) once an hour. That's much gentler than one-off bulk scraping, but
  it's still automated access to a third-party API using a key not
  officially issued to you for this purpose. If Doma's terms of service
  prohibit this, or if you plan to publish/host this for other people to use
  (rather than just running it locally for yourself), it's worth checking
  with Doma directly first, since automated access patterns like this can
  get an API key or IP blocked.
- This is **not affiliated with Doma** — it's a personal analytics tool
  built on public leaderboard data, with no promise the estimated numbers
  will match any real airdrop.
