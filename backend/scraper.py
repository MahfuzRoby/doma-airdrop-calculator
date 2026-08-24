import time
import requests

URL = "https://api.doma.xyz/graphql"

QUERY = """query Leaderboard($skip: Int, $take: Int, $walletAddress: String, $weekNumber: Int, $scope: LeaderboardScope, $seasonNumber: Int, $sortOrder: SortOrderType) {
  leaderboards(
    skip: $skip
    take: $take
    walletAddress: $walletAddress
    weekNumber: $weekNumber
    scope: $scope
    seasonNumber: $seasonNumber
    sortOrder: $sortOrder
  ) {
    currentPage
    hasPreviousPage
    hasNextPage
    totalCount
    items {
      points
      rank
      previousDayRank
      referralCount
      walletAddress
      totalEntries
      weekNumber
      seasonPoints
      weeklyPoints
      level
    }
  }
}"""


def get_headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        ),
        "Api-Key": api_key,
        "Origin": "https://app.doma.xyz",
        "Referer": "https://app.doma.xyz/",
    }


def fetch_all_wallets(api_key: str, week_number: int = None, take: int = 100,
                       delay: float = 0.25, max_retries: int = 4, timeout: int = 60):
    """Fetch every wallet for a single week (or the current week if
    week_number is None), paginated. Retries on both bad HTTP status codes
    AND network-level failures (timeouts, connection errors) - Doma's API
    can be slow on heavy weeks (some have 30,000+ entries).
    """
    headers = get_headers(api_key)
    all_items = []
    skip = 0

    while True:
        variables = {
            "take": take,
            "skip": skip,
            "scope": "WEEKLY",
            "sortOrder": "DESC",
        }
        if week_number is not None:
            variables["weekNumber"] = week_number

        payload = {
            "operationName": "Leaderboard",
            "query": QUERY,
            "variables": variables,
        }

        resp = None
        last_error = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(URL, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    break
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.exceptions.RequestException as e:
                resp = None
                last_error = f"{type(e).__name__}: {e}"
            time.sleep(2 + attempt * 2)  # backoff: 2s, 4s, 6s, 8s...
        else:
            raise RuntimeError(f"Failed after {max_retries} retries: {last_error}")

        result = resp.json()
        if "errors" in result:
            raise RuntimeError(f"GraphQL errors: {result['errors']}")

        data = result["data"]["leaderboards"]
        all_items.extend(data["items"])

        if not data["hasNextPage"]:
            break

        skip += take
        time.sleep(delay)

    return all_items


def fetch_full_history(api_key: str, take: int = 100, delay: float = 0.2, on_progress=None):
    """Walk every week from 1 to the current week and merge results, keeping
    each wallet's record from the *latest* week it appears in (seasonPoints
    is already cumulative, so the most recent sighting of a wallet has its
    correct up-to-date total). This recovers wallets that were active in
    earlier weeks but have since gone inactive and dropped out of the
    "current standings" view that the fast hourly sync uses.

    on_progress, if given, is called after each week as
    on_progress(week, total_weeks, unique_wallets_so_far).
    """
    # figure out the current week number from a single current-standings call
    probe = fetch_all_wallets(api_key, week_number=None, take=1, delay=delay)
    if not probe:
        return []
    current_week = probe[0]["weekNumber"]
    print(f"  [deep sync] current week is {current_week}, walking weeks 1..{current_week}")

    merged = {}
    for week in range(1, current_week + 1):
        items = fetch_all_wallets(api_key, week_number=week, take=take, delay=delay)
        for item in items:
            addr = item["walletAddress"].lower()
            merged[addr] = item  # later weeks overwrite earlier ones
        print(f"  [deep sync] week {week}/{current_week} done - {len(items)} wallets this week, "
              f"{len(merged)} unique wallets so far")
        if on_progress:
            on_progress(week, current_week, len(merged))
        time.sleep(delay)

    return list(merged.values())
