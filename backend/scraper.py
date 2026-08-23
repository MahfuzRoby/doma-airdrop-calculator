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


def fetch_all_wallets(api_key: str, take: int = 100, delay: float = 0.25, max_retries: int = 3):
    """Fetch the full current leaderboard (all wallets), paginated.

    Uses scope=WEEKLY with no weekNumber, which the live app calls for the
    "current standings" view. Each returned item still includes seasonPoints,
    so this single pass gives us both current-week and season-to-date totals.
    """
    headers = get_headers(api_key)
    all_items = []
    skip = 0

    while True:
        payload = {
            "operationName": "Leaderboard",
            "query": QUERY,
            "variables": {
                "take": take,
                "skip": skip,
                "scope": "WEEKLY",
                "sortOrder": "DESC",
            },
        }

        resp = None
        for attempt in range(max_retries):
            resp = requests.post(URL, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                break
            time.sleep(1 + attempt)
        else:
            raise RuntimeError(
                f"Failed after {max_retries} retries: "
                f"{resp.status_code if resp is not None else 'no response'} "
                f"{resp.text[:200] if resp is not None else ''}"
            )

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
