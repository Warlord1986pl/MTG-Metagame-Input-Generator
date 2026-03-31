#!/usr/bin/env python3
"""
MTG Metagame API Backup Bot
============================
Runs on a VPS (e.g. Mikrus 2.1).  Three commands:

FETCH  — fetch full format data from API and save as JSON cache
  python api_backup_bot.py fetch --format modern
  python api_backup_bot.py fetch --format modern --retry   # retries hourly on failure

PING   — lightweight API health check, records uptime stats
  python api_backup_bot.py ping --format modern

SERVE  — minimal HTTP server serving cached JSON (same URL structure as real API)
  python api_backup_bot.py serve --port 8080

STATS  — print uptime statistics
  python api_backup_bot.py stats

Cron setup (run as root in crontab -e):
  # Daily fetch at 05:00 UTC — retries hourly until success
  0 5 * * * cd /root/MTG-Metagame-Input-Generator && git pull -q && python3 scripts/api_backup_bot.py fetch --format modern --days 180 --retry >> /root/mtg-fetch.log 2>&1

  # Hourly ping for uptime monitoring
  0 * * * * cd /root/MTG-Metagame-Input-Generator && python3 scripts/api_backup_bot.py ping --format modern >> /root/mtg-ping.log 2>&1

On your Windows machine set:
  MTG_BACKUP_URL = http://your-mikrus-ip:8080

Requirements:
  pip install flask   (only needed for serve mode)
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_BASE = "https://api.videreproject.com"
BACKUP_DIR = Path(__file__).parent / "backup"
STATS_FILE = BACKUP_DIR / "stats.json"
KEEP_DAYS = 60           # keep cache files for 60 days
REQUEST_TIMEOUT = 90     # seconds per HTTP request
RETRY_ATTEMPTS = 3       # quick retries within single fetch attempt
RETRY_DELAYS = [1, 2, 4]
HOURLY_RETRY_INTERVAL = 3600  # seconds between hourly retries on failure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _cache_key(endpoint: str, params: dict) -> str:
    raw = endpoint + "?" + urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _cache_path(endpoint: str, params: dict) -> Path:
    safe_ep = endpoint.replace("/", "_")
    return BACKUP_DIR / f"{safe_ep}_{_cache_key(endpoint, params)}.json"


def _fetch(endpoint: str, params: dict) -> dict:
    """Single HTTP GET with quick retries. Raises RuntimeError on final failure."""
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
            raise RuntimeError(f"Failed: {err}") from err
    raise RuntimeError("All retries exhausted")


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    print(f"  [OK] Saved: {path.name}")


def _purge_old() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in BACKUP_DIR.glob("*.json"):
        if f.name == "stats.json":
            continue
        if f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"  [PURGE] {f.name}")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def _load_stats() -> dict:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"pings": 0, "ping_ok": 0, "fetches": 0, "fetch_ok": 0,
            "last_success": None, "last_failure": None, "consecutive_failures": 0}


def _save_stats(s: dict) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _record_ping(ok: bool) -> None:
    s = _load_stats()
    s["pings"] += 1
    if ok:
        s["ping_ok"] += 1
        s["last_success"] = _now_iso()
        s["consecutive_failures"] = 0
    else:
        s["last_failure"] = _now_iso()
        s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1
    _save_stats(s)


def _record_fetch(ok: bool) -> None:
    s = _load_stats()
    s["fetches"] += 1
    if ok:
        s["fetch_ok"] += 1
        s["last_success"] = _now_iso()
        s["consecutive_failures"] = 0
    else:
        s["last_failure"] = _now_iso()
        s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1
    _save_stats(s)


# ---------------------------------------------------------------------------
# FETCH command
# ---------------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace) -> None:
    today = date.today()
    week_end = today
    week_start = today - timedelta(days=args.days)

    common_params = {
        "format": args.format,
        "min_date": week_start.isoformat(),
        "max_date": week_end.isoformat(),
        "limit": args.limit,
    }

    attempt_number = 0
    while True:
        attempt_number += 1
        print(f"\n=== MTG Backup Bot — FETCH  ({_now_iso()}, attempt #{attempt_number}) ===")
        print(f"Format: {args.format}  |  Range: {week_start} → {week_end}  |  Days: {args.days}")

        meta_ok = False
        matchup_ok = False

        print(f"\nFetching metagame...")
        try:
            meta_data = _fetch("metagame", common_params)
            _save(_cache_path("metagame", common_params), meta_data)
            meta_ok = True
        except RuntimeError as e:
            print(f"  [ERROR] metagame: {e}")

        print(f"Fetching matchups...")
        try:
            matchup_data = _fetch("matchups", common_params)
            _save(_cache_path("matchups", common_params), matchup_data)
            matchup_ok = True
        except RuntimeError as e:
            print(f"  [ERROR] matchups: {e}")

        success = meta_ok and matchup_ok
        _record_fetch(success)

        if success:
            _purge_old()
            print("\n[DONE] Backup complete.")
            break

        if not args.retry:
            print("\n[FAIL] Fetch failed. Use --retry to enable hourly retries.")
            sys.exit(1)

        print(f"\n[RETRY] API unavailable. Next attempt in 60 minutes...")
        time.sleep(HOURLY_RETRY_INTERVAL)


# ---------------------------------------------------------------------------
# PING command
# ---------------------------------------------------------------------------
def cmd_ping(args: argparse.Namespace) -> None:
    today = date.today()
    params = {
        "format": args.format,
        "min_date": (today - timedelta(days=1)).isoformat(),
        "max_date": today.isoformat(),
        "limit": 1,
    }
    try:
        _fetch("metagame", params)
        _record_ping(ok=True)
        print(f"[PING {_now_iso()}] OK — API is reachable.")
    except RuntimeError as e:
        _record_ping(ok=False)
        print(f"[PING {_now_iso()}] FAIL — {e}")


# ---------------------------------------------------------------------------
# STATS command
# ---------------------------------------------------------------------------
def cmd_stats(_args: argparse.Namespace) -> None:
    s = _load_stats()
    print("=== MTG Backup Bot — API Uptime Stats ===")
    print()

    pings = s.get("pings", 0)
    ping_ok = s.get("ping_ok", 0)
    fetches = s.get("fetches", 0)
    fetch_ok = s.get("fetch_ok", 0)

    ping_uptime = (ping_ok / pings * 100) if pings else 0
    fetch_uptime = (fetch_ok / fetches * 100) if fetches else 0

    print(f"  Pings total:          {pings}")
    print(f"  Pings OK:             {ping_ok}  ({ping_uptime:.1f}% uptime)")
    print(f"  Fetches total:        {fetches}")
    print(f"  Fetches OK:           {fetch_ok}  ({fetch_uptime:.1f}% success)")
    print(f"  Last success:         {s.get('last_success') or 'never'}")
    print(f"  Last failure:         {s.get('last_failure') or 'never'}")
    print(f"  Consecutive failures: {s.get('consecutive_failures', 0)}")
    print()
    cached = [f for f in BACKUP_DIR.glob("*.json") if f.name != "stats.json"]
    print(f"  Cached files:         {len(cached)}")


# ---------------------------------------------------------------------------
# SERVE command
# ---------------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> None:
    try:
        from flask import Flask, jsonify, request as flask_request
    except ImportError:
        print("Flask is required: apt install python3-pip && pip3 install flask")
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
        s = _load_stats()
        cached = [f for f in BACKUP_DIR.glob("*.json") if f.name != "stats.json"]
        pings = s.get("pings", 0)
        ping_ok = s.get("ping_ok", 0)
        return jsonify({
            "status": "ok",
            "cached_files": len(cached),
            "api_uptime_pct": round(ping_ok / pings * 100, 1) if pings else None,
            "last_success": s.get("last_success"),
            "consecutive_failures": s.get("consecutive_failures", 0),
        })

    print(f"=== MTG Backup Server — port {args.port} ===")
    cached = [f for f in BACKUP_DIR.glob("*.json") if f.name != "stats.json"]
    print(f"Cached files: {len(cached)}")
    app.run(host="0.0.0.0", port=args.port)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="MTG API Backup Bot")
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    fetch_p = sub.add_parser("fetch", help="Fetch full format data and save cache")
    fetch_p.add_argument("--format", default="modern")
    fetch_p.add_argument("--days", type=int, default=180,
                         help="Window size in days (default: 180)")
    fetch_p.add_argument("--limit", type=int, default=100)
    fetch_p.add_argument("--retry", action="store_true",
                         help="Retry every 60 min until success (for cron use)")

    # ping
    ping_p = sub.add_parser("ping", help="Lightweight API health check + uptime tracking")
    ping_p.add_argument("--format", default="modern")

    # stats
    sub.add_parser("stats", help="Show API uptime statistics")

    # serve
    serve_p = sub.add_parser("serve", help="Serve cached data via HTTP")
    serve_p.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "ping":
        cmd_ping(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "serve":
        cmd_serve(args)


if __name__ == "__main__":
    main()

