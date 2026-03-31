#!/usr/bin/env python3
"""
MTG Metagame API Backup Bot
============================
Runs on a VPS (e.g. Mikrus 2.1).  Two modes:

FETCH mode  — call the real API, save raw JSON to ./backup/
  python api_backup_bot.py fetch --format modern --my-deck "Jeskai Blink"

SERVE mode  — a minimal Flask HTTP server that serves the cached JSON
              using the same URL structure as the real API
  python api_backup_bot.py serve --port 8080

Cron example (fetch every day at 05:00 UTC):
  0 5 * * * cd /home/user/mtg-backup && python api_backup_bot.py fetch \
    --format modern --my-deck "Jeskai Blink" >> cron.log 2>&1

Then set on your Windows machine:
  $env:MTG_BACKUP_URL = "http://your-mikrus-ip:8080"
  (or add to a .env file / Windows environment variables)

Requirements:
  pip install flask   (only needed for serve mode)
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_BASE = "https://api.videreproject.com"
BACKUP_DIR = Path(__file__).parent / "backup"
KEEP_DAYS = 30          # how many days of cache files to keep
REQUEST_TIMEOUT = 90    # seconds
RETRY_ATTEMPTS = 3
RETRY_DELAYS = [1, 2, 4]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cache_key(endpoint: str, params: dict) -> str:
    raw = endpoint + "?" + urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _cache_path(endpoint: str, params: dict) -> Path:
    safe_ep = endpoint.replace("/", "_")
    return BACKUP_DIR / f"{safe_ep}_{_cache_key(endpoint, params)}.json"


def _fetch(endpoint: str, params: dict) -> dict:
    qs = urlencode(params)
    url = f"{API_BASE}/{endpoint}?{qs}"
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "MTG-Backup-Bot/1.0"})
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as err:
            if attempt < RETRY_ATTEMPTS - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                print(f"  [WARN] Attempt {attempt + 1} failed ({err}), retrying in {delay}s...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Failed to fetch {url}: {err}") from err
    raise RuntimeError("All retries exhausted")


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    print(f"  [OK] Saved: {path.name}")


def _purge_old(keep_count: int = KEEP_DAYS * 2) -> None:
    """Remove oldest cache files if there are more than keep_count."""
    files = sorted(BACKUP_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    to_delete = files[:-keep_count] if len(files) > keep_count else []
    for f in to_delete:
        f.unlink()
        print(f"  [PURGE] Removed old cache: {f.name}")


# ---------------------------------------------------------------------------
# FETCH mode
# ---------------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace) -> None:
    today = date.today()
    week_end = today
    week_start = today - timedelta(days=args.days)

    print(f"=== MTG Backup Bot — FETCH  ({today}) ===")
    print(f"Format: {args.format}  |  Range: {week_start} → {week_end}")
    print()

    common_params = {
        "format": args.format,
        "min_date": week_start.isoformat(),
        "max_date": week_end.isoformat(),
        "limit": args.limit,
    }

    # 1. Full metagame snapshot (all decks in format)
    print(f"Fetching metagame [{week_start} to {week_end}]...")
    try:
        meta_data = _fetch("metagame", common_params)
        _save(_cache_path("metagame", common_params), meta_data)
    except RuntimeError as e:
        print(f"  [ERROR] metagame: {e}")

    # 2. Full matchup snapshot (all archetypes vs all archetypes)
    print(f"Fetching matchups [{week_start} to {week_end}]...")
    try:
        matchup_data = _fetch("matchups", common_params)
        _save(_cache_path("matchups", common_params), matchup_data)
    except RuntimeError as e:
        print(f"  [ERROR] matchups: {e}")

    _purge_old()
    print("\n[DONE]")


# ---------------------------------------------------------------------------
# SERVE mode
# ---------------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> None:
    try:
        from flask import Flask, jsonify, request as flask_request
    except ImportError:
        print("Flask is required for serve mode: pip install flask")
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/<endpoint>")
    def serve_endpoint(endpoint: str):
        params = dict(flask_request.args)
        path = _cache_path(endpoint, params)
        if not path.exists():
            return jsonify({"error": "Not in cache", "endpoint": endpoint}), 404
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return jsonify(data)
        except (OSError, json.JSONDecodeError) as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/health")
    def health():
        files = list(BACKUP_DIR.glob("*.json"))
        return jsonify({"status": "ok", "cached_files": len(files)})

    print(f"=== MTG Backup Server — port {args.port} ===")
    print(f"Cache dir: {BACKUP_DIR}")
    print(f"Cached files: {len(list(BACKUP_DIR.glob('*.json')))}")
    app.run(host="0.0.0.0", port=args.port)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="MTG API Backup Bot")
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    fetch_p = sub.add_parser("fetch", help="Fetch full format data from API and save cache")
    fetch_p.add_argument("--format", default="modern", help="MTG format (default: modern)")
    fetch_p.add_argument("--days", type=int, default=14,
                         help="Window size in days (default: 14)")
    fetch_p.add_argument("--limit", type=int, default=100,
                         help="Max archetypes per endpoint (default: 100)")

    # serve
    serve_p = sub.add_parser("serve", help="Serve cached data via HTTP")
    serve_p.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "serve":
        cmd_serve(args)


if __name__ == "__main__":
    main()
