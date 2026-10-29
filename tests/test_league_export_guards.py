"""Offline tests for src/league_export_guards.py -- one test per guard, plus the two real failure
shapes seen on 2026-09-24: a stale results file (Cloudflare served the 2026-09-17 export next to
a fresh season table) and a null LoginID in both exports (12854500 place 13, "Jetpool").

Fixtures are built in a temp dir by _write_fixture: a small season of two Challenges and one
premier, 32 placements each, with standings derived from the same rows -- so the baseline passes
every guard and each test breaks exactly one thing.

Run directly: python tests/test_league_export_guards.py
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from league_export_guards import check_docs_data, check_season  # noqa: E402

RESULTS_COLS = [
    "EventDate", "IngestedAt", "EventID", "EventName", "EventType", "Format", "Season",
    "LoginID", "Name", "Placement", "PointsAwarded",
]
EVENTS = [
    ("20000001", "2026-09-02", "challenge"),
    ("20000002", "2026-09-06", "premier"),
    ("20000003", "2026-09-09", "challenge"),
]


def _points(place: int) -> int:
    return 5 if place == 1 else 4 if place == 2 else 3 if place <= 4 else 2 if place <= 8 else 1 if place <= 16 else 0


def _results_rows() -> List[Dict[str, str]]:
    rows = []
    for n, (event_id, day, kind) in enumerate(EVENTS):
        for place in range(1, 33):
            login = str(1000 + (place + 7 * n) % 40)  # overlapping rosters across events
            pts = _points(place) * (2 if kind == "premier" else 1)
            rows.append({
                "EventDate": day, "IngestedAt": f"{day}T12:00:00Z", "EventID": event_id,
                "EventName": "Modern Challenge 32" if kind == "challenge" else "RC Qualifier",
                "EventType": kind, "Format": "modern", "Season": "Autumn 2026",
                "LoginID": login, "Name": f"pilot{login}", "Placement": str(place), "PointsAwarded": str(pts),
            })
    return rows


def _standings_from(rows: List[Dict[str, str]], merges: Optional[Dict[str, str]] = None) -> List[dict]:
    merges = merges or {}
    agg: Dict[str, dict] = {}
    for r in rows:
        key = merges.get(r["LoginID"], r["LoginID"])
        p = agg.setdefault(key, {"loginId": key, "name": r["Name"], "points": 0, "premierPoints": 0,
                                 "wins": 0, "top2": 0, "top4": 0, "top8": 0, "top16": 0, "_events": set()})
        place, pts = int(r["Placement"]), int(r["PointsAwarded"])
        p["points"] += pts
        p["premierPoints"] += pts if r["EventType"] == "premier" else 0
        for col, cut in (("wins", 1), ("top2", 2), ("top4", 4), ("top8", 8), ("top16", 16)):
            p[col] += int(place <= cut)
        p["_events"].add(r["EventID"])
    pilots = []
    for rank, p in enumerate(sorted(agg.values(), key=lambda p: (-p["points"], p["loginId"])), start=1):
        p["starts"] = len(p.pop("_events"))
        p["rank"] = rank
        pilots.append(p)
    return pilots


def _write_fixture(
    root: Path,
    rows: Optional[List[Dict[str, str]]] = None,
    pilots: Optional[List[dict]] = None,
    pilot_event_dates: Optional[List[str]] = None,
) -> Path:
    rows = rows if rows is not None else _results_rows()
    pilots = pilots if pilots is not None else _standings_from(rows)
    root.mkdir(parents=True, exist_ok=True)
    (root / "season_Autumn_2026.json").write_text(
        json.dumps({"season": "Autumn 2026", "asOf": "2026-09-10", "pilots": pilots}), encoding="utf-8"
    )
    dates = pilot_event_dates if pilot_event_dates is not None else sorted({r["EventDate"] for r in rows})
    (root / "pilots_Autumn_2026.json").write_text(
        json.dumps({"season": "Autumn 2026", "pilots": {"id:1": {"results": [{"date": d} for d in dates]}}}),
        encoding="utf-8",
    )
    with open(root / "pilot_league_results_Autumn_2026.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULTS_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return root


def _check(root: Path, merges: Optional[Dict[str, str]] = None) -> List[str]:
    return check_season(
        root / "season_Autumn_2026.json",
        root / "pilot_league_results_Autumn_2026.csv",
        root / "pilots_Autumn_2026.json",
        identity_map=merges or {},
    )


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="export_guards_"))


def _has(failures: List[str], needle: str) -> bool:
    return any(needle in f for f in failures)


def test_consistent_exports_pass() -> None:
    assert _check(_write_fixture(_tmp())) == []


def test_stale_results_file_fails() -> None:
    # Standings built from all three events, results file still from before the third one.
    rows = _results_rows()
    stale = [r for r in rows if r["EventID"] != "20000003"]
    root = _write_fixture(
        _tmp(), rows=stale, pilots=_standings_from(rows),
        pilot_event_dates=sorted({r["EventDate"] for r in rows}),
    )
    failures = _check(root)
    for needle in ("sum(Wins) vs nunique(EventID)", "sum(Starts) vs results rows",
                   "sum(Points) vs sum(PointsAwarded)", "differ in bracket columns", "latest EventDate"):
        assert _has(failures, needle), (needle, failures)


def test_null_login_id_fails_in_both_files() -> None:
    rows = _results_rows()
    rows[12]["LoginID"] = ""
    pilots = _standings_from(rows)
    root = _write_fixture(_tmp(), rows=rows, pilots=[{**p, "loginId": None} if p["loginId"] == "" else p for p in pilots])
    failures = _check(root)
    assert _has(failures, "standings: 1 row(s) with null/non-integer LoginID"), failures
    assert _has(failures, "results: 1 row(s) with null/non-integer LoginID"), failures


def test_non_integer_login_id_fails() -> None:
    rows = _results_rows()
    rows[0]["LoginID"] = "1000.0"
    assert _has(_check(_write_fixture(_tmp(), rows=rows)), "results: 1 row(s) with null/non-integer LoginID")


def test_wins_vs_events_guard() -> None:
    pilots = _standings_from(_results_rows())
    pilots[-1]["wins"] += 1
    assert _has(_check(_write_fixture(_tmp(), pilots=pilots)), "sum(Wins) vs nunique(EventID)")


def test_starts_vs_rows_guard() -> None:
    pilots = _standings_from(_results_rows())
    pilots[-1]["starts"] += 1
    assert _has(_check(_write_fixture(_tmp(), pilots=pilots)), "sum(Starts) vs results rows")


def test_points_total_guard() -> None:
    pilots = _standings_from(_results_rows())
    pilots[0]["points"] += 1
    assert _has(_check(_write_fixture(_tmp(), pilots=pilots)), "sum(Points) vs sum(PointsAwarded)")


def test_event_points_must_be_31_or_62() -> None:
    rows = _results_rows()
    rows[0]["PointsAwarded"] = "6"  # a Challenge winner scored as 6 -> event sums to 32
    failures = _check(_write_fixture(_tmp(), rows=rows, pilots=_standings_from(rows)))
    assert _has(failures, "not 31 or 62"), failures


def test_bracket_columns_guard() -> None:
    pilots = _standings_from(_results_rows())
    a, b = pilots[0], pilots[1]
    a["top8"], b["top8"] = a["top8"] + 1, b["top8"] - 1  # totals unchanged, per-pilot wrong
    assert _has(_check(_write_fixture(_tmp(), pilots=pilots)), "differ in bracket columns")


def test_merge_map_is_applied() -> None:
    rows = _results_rows()
    merges = {"1001": "9001"}  # raw account 1001 resolves to canonical pilot id 9001
    root = _write_fixture(_tmp(), rows=rows, pilots=_standings_from(rows, merges))
    assert _check(root, merges) == []
    assert _has(_check(root, {}), "absent from")


def test_latest_event_date_guard() -> None:
    rows = _results_rows()
    root = _write_fixture(_tmp(), rows=rows, pilot_event_dates=["2026-09-02", "2026-09-06", "2026-09-09", "2026-09-12"])
    assert _has(_check(root), "latest EventDate: results 2026-09-09 != standings snapshot 2026-09-12")


def test_check_docs_data_reports_and_fails() -> None:
    rows = _results_rows()
    rows[3]["LoginID"] = ""
    root = _write_fixture(_tmp(), rows=rows)
    lines: List[str] = []
    assert check_docs_data(root, identity_map={}, log=lines.append) is False
    assert any("Autumn_2026: FAILED" in line for line in lines), lines
    assert check_docs_data(_write_fixture(_tmp()), identity_map={}, log=lines.append) is True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK: {name}")
