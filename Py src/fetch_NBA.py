"""
Data acquisition script for the balldontlie NBA API.

Design goals (per project guide):
- Respect the free-tier rate limit (5 requests/minute)
- Paginate using meta.next_cursor
- Save raw JSON responses before any transformation
- Retry gracefully on rate-limit (429) and transient errors

Usage:
    export BALLDONTLIE_API_KEY="your-key-here"
    python fetch_balldontlie_data.py
"""

import os
import time
import json
import requests
from pathlib import Path

BASE_URL = "https://api.balldontlie.io/v1"
API_KEY = os.environ.get("BALLDONTLIE_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "Missing API key. Set it with: export BALLDONTLIE_API_KEY='your-key-here'"
    )

HEADERS = {"Authorization": API_KEY}

# Free tier: 5 requests/minute -> space requests out safely (12s = 5/min exactly,
# add a small buffer so you don't ride the edge of the limit)
SECONDS_BETWEEN_REQUESTS = 13
MAX_RETRIES = 5

RAW_DATA_DIR = Path("data/raw")
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_with_retry(url, params):
    """Make a GET request with retry/backoff on rate limits or transient errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(url, headers=HEADERS, params=params)

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429:
            # Rate limited — back off longer than usual, respect Retry-After if present
            wait = int(response.headers.get("Retry-After", 30))
            print(f"  Rate limited. Waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue

        if 500 <= response.status_code < 600:
            # Server-side error — short backoff and retry
            wait = 5 * attempt
            print(f"  Server error {response.status_code}. Retrying in {wait}s...")
            time.sleep(wait)
            continue

        # Any other error (400, 401, 404, etc.) — not worth retrying blindly
        response.raise_for_status()

    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")


def fetch_all_pages(endpoint, params=None, per_page=100):
    """
    Fetch every page of a paginated endpoint, respecting rate limits.
    Returns a list of all records across pages.
    """
    params = dict(params or {})
    params["per_page"] = per_page

    all_records = []
    cursor = None
    page_num = 1

    while True:
        if cursor is not None:
            params["cursor"] = cursor

        print(f"Fetching {endpoint} — page {page_num} (cursor={cursor})...")
        data = get_with_retry(f"{BASE_URL}/{endpoint}", params)

        records = data.get("data", [])
        all_records.extend(records)

        cursor = data.get("meta", {}).get("next_cursor")
        page_num += 1

        if not cursor:
            break

        # Throttle between requests to stay under 5/min
        time.sleep(SECONDS_BETWEEN_REQUESTS)

    return all_records


def save_raw(endpoint_name, records):
    """Save the raw pulled records to disk before any transformation."""
    out_path = RAW_DATA_DIR / f"{endpoint_name}.json"
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Saved {len(records)} records -> {out_path}")


def main():
    # Example: pull all teams (small, single-page endpoint — good for testing the flow)
    teams = fetch_all_pages("teams")
    save_raw("teams", teams)

    # Example: pull players (larger, multi-page — exercises the cursor pagination + throttling)
    # Uncomment when ready — this will take a while at 5 req/min
    # players = fetch_all_pages("players")
    # save_raw("players", players)

    # Example: pull games for a specific season
    # games = fetch_all_pages("games", params={"seasons[]": 2023})
    # save_raw("games_2023", games)


if __name__ == "__main__":
    main()