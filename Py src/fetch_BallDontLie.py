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


START_SEASON = 2021
END_SEASON = 2025  # balldontlie labels a season by its starting year (e.g. 2025 = 2025-26)

# Shortened params for quicker processing (2yr)
# MIN_START_SEASON = 2024
# MIN_STATS_END_SEASON = 2025


def raw_file_exists(name):
    return (RAW_DATA_DIR / f"{name}.json").exists()


def fetch_players():
    """
    Players endpoint doesn't filter by season — it returns the full player
    database (current + historical). Pull once; filter down to players who
    actually appear in your games/stats data later, at the SQL layer.
    """
    if raw_file_exists("players"):
        print("players.json already exists — skipping (delete the file to re-pull)")
        return

    players = fetch_all_pages("players")
    save_raw("players", players)


def fetch_games_by_season(start_year, end_year):
    """
    Pull games one season at a time and save each to its own raw file.
    This makes the multi-hour pull resumable: if it stops partway through,
    just re-run — completed seasons are skipped automatically.
    """
    for year in range(start_year, end_year + 1):
        file_name = f"games_{year}"

        if raw_file_exists(file_name):
            print(f"{file_name}.json already exists — skipping")
            continue

        print(f"\n=== Fetching games for season {year} ===")
        games = fetch_all_pages("games", params={"seasons[]": year})
        save_raw(file_name, games)


def fetch_stats_by_season(start_year, end_year):
    """
    Pull player box-score stats one season at a time and save each to its
    own raw file. One row per player per game — much larger volume than
    games, so this is the slowest part of acquisition. Resumable like
    fetch_games_by_season: completed seasons are skipped on rerun.
    """
    for year in range(start_year, end_year + 1):
        file_name = f"stats_{year}"

        if raw_file_exists(file_name):
            print(f"{file_name}.json already exists — skipping")
            continue

        print(f"\n=== Fetching player stats for season {year} ===")
        stats = fetch_all_pages("stats", params={"seasons[]": year})
        save_raw(file_name, stats)


def main():
    # Teams — small, single-page (already validated)
    teams = fetch_all_pages("teams")
    save_raw("teams", teams)

    # Players — full database, no season filter available
    fetch_players()

    # Games — one file per season, resumable
    fetch_games_by_season(START_SEASON, END_SEASON)

    # Player box-score stats — one file per season, resumable
    # Largest pull by far (~10k+ rows/season) — expect this to take the longest
    fetch_stats_by_season(START_SEASON, END_SEASON)


if __name__ == "__main__":
    main()