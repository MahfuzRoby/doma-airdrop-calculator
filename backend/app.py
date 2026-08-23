import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_conn
from scraper import fetch_all_wallets

# ---------------- CONFIG ----------------
# The API key below was captured from Doma's own frontend (app.doma.xyz) via
# browser devtools. It is a public key their web app sends on every visitor's
# leaderboard request, not a personal credential. It can stop working at any
# time if Doma rotates it, in which case re-capture it the same way and
# update it here (or set the DOMA_API_KEY environment variable instead).
API_KEY = os.environ.get(
    "DOMA_API_KEY",
    "v1.c6e3f41019fb97237b7f192d49adb2ae464f2ba7ca6c0737fd6eab71ee01d1d4",
)

TOTAL_SUPPLY = 1_000_000_000  # assumed fixed 1B token supply
SYNC_INTERVAL_HOURS = 1
# -----------------------------------------

app = FastAPI(title="Doma Airdrop Calculator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your Vercel domain once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)


def sync_data():
    """Pull the full current leaderboard from Doma and upsert into SQLite."""
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Starting sync...")
    try:
        items = fetch_all_wallets(API_KEY)
    except Exception as e:
        print("Sync failed:", e)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (synced_at, total_wallets, total_season_points, status, error) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, 0, 0, "error", str(e)),
            )
            conn.commit()
        return

    total_season_points = sum(i["seasonPoints"] for i in items)
    total_weekly_points = sum(i["weeklyPoints"] for i in items)

    with get_conn() as conn:
        for i in items:
            conn.execute(
                """
                INSERT INTO wallets (
                    wallet_address, points, rank, previous_day_rank, referral_count,
                    total_entries, week_number, season_points, weekly_points, level, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    points=excluded.points,
                    rank=excluded.rank,
                    previous_day_rank=excluded.previous_day_rank,
                    referral_count=excluded.referral_count,
                    total_entries=excluded.total_entries,
                    week_number=excluded.week_number,
                    season_points=excluded.season_points,
                    weekly_points=excluded.weekly_points,
                    level=excluded.level,
                    updated_at=excluded.updated_at
                """,
                (
                    i["walletAddress"], i["points"], i["rank"], i["previousDayRank"],
                    i["referralCount"], i["totalEntries"], i["weekNumber"],
                    i["seasonPoints"], i["weeklyPoints"], i["level"], now,
                ),
            )

        conn.execute(
            "INSERT INTO sync_log (synced_at, total_wallets, total_season_points, status, error) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, len(items), total_season_points, "ok", None),
        )
        conn.execute(
            "INSERT INTO points_history (recorded_at, total_wallets, total_season_points, total_weekly_points) "
            "VALUES (?, ?, ?, ?)",
            (now, len(items), total_season_points, total_weekly_points),
        )
        conn.commit()

    print(f"Sync complete: {len(items)} wallets, {total_season_points:,.0f} total season points")


@app.on_event("startup")
def startup():
    init_db()
    sync_data()  # populate immediately on boot so the frontend isn't empty
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(sync_data, "interval", hours=SYNC_INTERVAL_HOURS, id="hourly_sync")
    scheduler.start()


@app.get("/api/status")
def status():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else {"status": "no_data_yet"}


@app.post("/api/sync-now")
def sync_now():
    """Manually trigger a sync instead of waiting for the hourly job."""
    sync_data()
    return {"status": "triggered"}


@app.get("/api/stats")
def stats():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as wallet_count, SUM(season_points) as total_season_points, "
            "SUM(weekly_points) as total_weekly_points, MAX(updated_at) as last_updated FROM wallets"
        ).fetchone()
        level_rows = conn.execute(
            "SELECT level, COUNT(*) as count FROM wallets GROUP BY level"
        ).fetchall()
    result = dict(row)
    result["by_level"] = {r["level"] or "UNRANKED": r["count"] for r in level_rows}
    return result


@app.get("/api/history")
def history(limit: int = Query(168, le=2000)):
    """Time series of total points, for charting growth over time (hourly snapshots)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM points_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


@app.get("/api/calculator")
def calculator(
    airdrop_percent: float = Query(5.0, description="Percent of 1B total supply allocated to the airdrop"),
    fdv: float = Query(100_000_000, description="Assumed fully diluted valuation in USD"),
):
    with get_conn() as conn:
        row = conn.execute("SELECT SUM(season_points) as total_points FROM wallets").fetchone()
    total_points = row["total_points"] or 0
    if total_points == 0:
        raise HTTPException(status_code=404, detail="No data yet - wait for the first sync to finish")

    token_price = fdv / TOTAL_SUPPLY
    airdrop_pool_tokens = TOTAL_SUPPLY * (airdrop_percent / 100)
    airdrop_pool_usd = airdrop_pool_tokens * token_price

    return {
        "total_supply": TOTAL_SUPPLY,
        "fdv_usd": fdv,
        "token_price_usd": token_price,
        "airdrop_percent": airdrop_percent,
        "airdrop_pool_tokens": airdrop_pool_tokens,
        "airdrop_pool_usd": airdrop_pool_usd,
        "total_points_all_wallets": total_points,
        "tokens_per_point": airdrop_pool_tokens / total_points,
        "value_per_point_usd": airdrop_pool_usd / total_points,
    }


@app.get("/api/wallet/{address}")
def wallet_lookup(
    address: str,
    airdrop_percent: float = Query(5.0),
    fdv: float = Query(100_000_000),
):
    with get_conn() as conn:
        wallet = conn.execute(
            "SELECT * FROM wallets WHERE lower(wallet_address) = lower(?)", (address,)
        ).fetchone()
        total_row = conn.execute("SELECT SUM(season_points) as total_points FROM wallets").fetchone()

    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found on the leaderboard")

    total_points = total_row["total_points"] or 0
    token_price = fdv / TOTAL_SUPPLY
    airdrop_pool_tokens = TOTAL_SUPPLY * (airdrop_percent / 100)

    share = (wallet["season_points"] / total_points) if total_points else 0
    est_tokens = share * airdrop_pool_tokens
    est_usd = est_tokens * token_price

    result = dict(wallet)
    result.update(
        {
            "share_of_total_points_pct": share * 100,
            "estimated_tokens": est_tokens,
            "estimated_usd_value": est_usd,
            "assumptions": {
                "airdrop_percent": airdrop_percent,
                "fdv_usd": fdv,
                "token_price_usd": token_price,
            },
        }
    )
    return result


@app.get("/api/leaderboard")
def leaderboard(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("season_points", pattern="^(season_points|weekly_points|rank)$"),
):
    order = "ASC" if sort_by == "rank" else "DESC"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM wallets ORDER BY {sort_by} {order} LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return [dict(r) for r in rows]
