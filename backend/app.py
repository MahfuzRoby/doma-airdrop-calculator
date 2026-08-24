import os
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_conn
from scraper import fetch_all_wallets, fetch_full_history

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
FAST_SYNC_INTERVAL_HOURS = 1   # current-standings only, cheap
DEEP_SYNC_INTERVAL_HOURS = 24  # walks every week 1..current, expensive
# -----------------------------------------

app = FastAPI(title="Doma Airdrop Calculator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your Vercel domain once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)


def _upsert_items(conn, items, now):
    """Shared upsert logic used by both the fast and deep syncs. Wallet
    addresses are normalized to lowercase so the same wallet never ends up
    stored as two different rows due to casing differences between API
    responses.
    """
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
                i["walletAddress"].lower(), i["points"], i["rank"], i["previousDayRank"],
                i["referralCount"], i["totalEntries"], i["weekNumber"],
                i["seasonPoints"], i["weeklyPoints"], i["level"], now,
            ),
        )


def sync_data():
    """Fast sync: current standings only (~1 API round trip per 100 wallets).
    Good for keeping ranks/weekly points fresh hourly, but MISSES wallets
    that earned points in earlier weeks and have since gone inactive - those
    only get picked up by deep_sync().
    """
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Starting fast sync...")
    try:
        items = fetch_all_wallets(API_KEY)
    except Exception as e:
        print("Fast sync failed:", e)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (synced_at, total_wallets, total_season_points, status, error) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, 0, 0, "error", str(e)),
            )
            conn.commit()
        return

    with get_conn() as conn:
        _upsert_items(conn, items, now)

        row = conn.execute(
            "SELECT COUNT(*) as c, SUM(season_points) as sp, SUM(weekly_points) as wp FROM wallets"
        ).fetchone()

        conn.execute(
            "INSERT INTO sync_log (synced_at, total_wallets, total_season_points, status, error) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, row["c"], row["sp"], "ok", None),
        )
        conn.execute(
            "INSERT INTO points_history (recorded_at, total_wallets, total_season_points, total_weekly_points) "
            "VALUES (?, ?, ?, ?)",
            (now, row["c"], row["sp"], row["wp"]),
        )
        conn.commit()

    print(f"Fast sync complete: {len(items)} wallets touched this pass, "
          f"{row['c']} total wallets tracked, {row['sp']:,.0f} total season points")


def deep_sync():
    """Deep sync: walks every week from 1 to the current week and merges, so
    wallets that were active early in the season but have since gone quiet
    are still counted toward the true total points distributed. Slower
    (roughly 20x the API calls of a fast sync once there are ~20 weeks), so
    this runs on its own daily schedule instead of hourly.
    """
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Starting deep sync (all weeks)...")
    try:
        items = fetch_full_history(API_KEY)
    except Exception as e:
        print("Deep sync failed:", e)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (synced_at, total_wallets, total_season_points, status, error) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, 0, 0, "deep_error", str(e)),
            )
            conn.commit()
        return

    with get_conn() as conn:
        _upsert_items(conn, items, now)

        row = conn.execute(
            "SELECT COUNT(*) as c, SUM(season_points) as sp, SUM(weekly_points) as wp FROM wallets"
        ).fetchone()

        conn.execute(
            "INSERT INTO sync_log (synced_at, total_wallets, total_season_points, status, error) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, row["c"], row["sp"], "deep_ok", None),
        )
        conn.execute(
            "INSERT INTO points_history (recorded_at, total_wallets, total_season_points, total_weekly_points) "
            "VALUES (?, ?, ?, ?)",
            (now, row["c"], row["sp"], row["wp"]),
        )
        conn.commit()

    print(f"Deep sync complete: {len(items)} unique wallets across all weeks, "
          f"{row['c']} total wallets tracked, {row['sp']:,.0f} total season points")


@app.on_event("startup")
def startup():
    init_db()
    sync_data()  # fast sync immediately (blocking) so the frontend isn't empty

    # Kick off the first deep sync in a background thread rather than relying
    # on APScheduler's next_run_time="now" trick, which can silently miss its
    # misfire-grace window after the blocking fast sync above eats a couple
    # minutes. A plain thread just runs it, no timing edge cases.
    threading.Thread(target=deep_sync, daemon=True).start()

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(sync_data, "interval", hours=FAST_SYNC_INTERVAL_HOURS, id="fast_sync")
    scheduler.add_job(deep_sync, "interval", hours=DEEP_SYNC_INTERVAL_HOURS, id="deep_sync")
    scheduler.start()


@app.get("/api/status")
def status():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else {"status": "no_data_yet"}


@app.post("/api/sync-now")
def sync_now():
    """Manually trigger a fast (current-standings) sync."""
    sync_data()
    return {"status": "triggered", "type": "fast"}


@app.post("/api/deep-sync-now")
def deep_sync_now():
    """Manually trigger a deep sync (walks every week 1..current). Slow -
    can take several minutes depending on how many weeks exist."""
    deep_sync()
    return {"status": "triggered", "type": "deep"}


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
