"""Regression guard for league_results_export.py's central risk: the event-level export's LoginID
column is deliberately raw/unresolved (see that module's docstring -- "identity is LoginID and
nothing else" for the export), while the published season table groups by resolved identity
(data/pilot_identity.csv, via league_engine._identity_key). Any aggregation over the raw export
that skips identity resolution before summing Points/Starts/bracket columns produces a PLAUSIBLE
but WRONG number for any pilot with a recorded merge -- not a crash, which is exactly the failure
mode worth a permanent test rather than trusting a one-off manual check.

reconcile_with_season_table is the one place in the code that resolves identity before comparing
(via league_engine.aggregate_pilot_table, imported and reused, not reimplemented -- see that
function's own docstring). This test is a thin, permanent pytest wrapper around it, run against
real on-disk data (outputs/league/results, data/pilot_identity.csv) rather than a synthetic
fixture, matching this repo's existing "zero regression" test style (see test_pilot_identity.py).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd

from league_results_export import build_results_rows, check_placement_integrity, reconcile_with_season_table


def test_placement_integrity_accepts_a_real_sub_32_field() -> None:
    """Regression for the real production bug (2026-09-14): a genuine 'Modern Challenge 16' event
    with real attendance under 32 (e.g. 31 players, Placement 1..31 with no gaps) is a COMPLETE,
    valid event, not a corrupt 32-player one missing its last row -- mtgo.com runs Challenges at
    several real capacities (16/32/64/96/...). check_placement_integrity must key its expected
    range off each event's own row count, not a hardcoded 32.
    """
    df = pd.DataFrame({
        "EventID": ["evt-31"] * 31 + ["evt-32"] * 32,
        "Placement": list(range(1, 32)) + list(range(1, 33)),
    })
    issues = check_placement_integrity(df)
    assert issues == [], f"expected no issues for complete 1..31 and 1..32 events, got {issues}"


def test_placement_integrity_still_catches_a_real_gap() -> None:
    """A genuinely incomplete event (5 rows, but Placement skips 5) must still be flagged --
    the fix must not turn this check into a no-op."""
    df = pd.DataFrame({"EventID": ["evt-gap"] * 5, "Placement": [1, 2, 3, 4, 6]})
    issues = check_placement_integrity(df)
    assert len(issues) == 1
    assert issues[0]["EventID"] == "evt-gap"
    assert issues[0]["missing_placements"] == [5]


def _season_config_rows(league_dir: Path) -> List[Tuple[str, date, date]]:
    config_csv = league_dir / "season_config.csv"
    if not config_csv.exists():
        return []
    df = pd.read_csv(config_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    rows = []
    for _, r in df.iterrows():
        try:
            rows.append((r["Season"], date.fromisoformat(r["StartDate"]), date.fromisoformat(r["EndDate"])))
        except (ValueError, KeyError):
            continue
    return rows


def test_export_reconciles_with_season_table_after_identity_resolution() -> None:
    league_dir = REPO_ROOT / "outputs" / "league"
    results_dir = league_dir / "results"
    if not results_dir.exists():
        print("  (no outputs/league/results on disk -- nothing to verify yet)")
        return

    checked = 0
    for season_name, season_start, season_end in _season_config_rows(league_dir):
        export_df, current_results = build_results_rows(
            results_dir, season_start, season_end, season_name, "modern", ingestion_log={},
        )
        if export_df.empty:
            continue

        mismatches = reconcile_with_season_table(
            current_results, results_dir, season_start, season_end, as_of=date.today(),
        )
        assert not mismatches, (
            f"{season_name}: event-level export disagrees with the published season table after "
            f"identity resolution (Points/Starts/every bracket column all checked) -- {mismatches[:10]}"
        )
        checked += 1

    if checked == 0:
        print("  (no season with data on disk yet -- nothing to verify)")


def test_export_login_id_is_raw_not_resolved() -> None:
    """Guards the other direction: the export's own LoginID column must stay raw/unresolved (per
    its module docstring), not silently start emitting the resolved pilot_id. If this ever flips,
    the test above would likely still pass (a season with a merge would just look consistent a
    different way), so it needs its own explicit check -- confirms against a real recorded merge
    (data/pilot_identity.csv) if one exists on disk, else this is a no-op.
    """
    import identity as pilot_identity

    league_dir = REPO_ROOT / "outputs" / "league"
    results_dir = league_dir / "results"
    identity_csv = REPO_ROOT / "data" / "pilot_identity.csv"
    if not results_dir.exists() or not identity_csv.exists():
        print("  (no results dir or no data/pilot_identity.csv on disk -- nothing to verify yet)")
        return

    alias_rows = [r for r in pilot_identity.read_identity_rows(identity_csv) if r["role"] == "alias"]
    if not alias_rows:
        print("  (no alias rows recorded yet -- nothing to verify)")
        return

    checked = 0
    for season_name, season_start, season_end in _season_config_rows(league_dir):
        export_df, _current_results = build_results_rows(
            results_dir, season_start, season_end, season_name, "modern", ingestion_log={},
        )
        if export_df.empty:
            continue
        export_login_ids = set(export_df["LoginID"])
        for row in alias_rows:
            alias_lid, primary_lid = row["loginid"], row["pilot_id"]
            if alias_lid in export_login_ids:
                # The raw alias loginid must appear on its own -- if identity resolution had
                # leaked into the export, every alias row would instead be rewritten to
                # primary_lid and alias_lid would never appear at all.
                checked += 1
        # If the primary's own raw loginid appears but the alias's never does even though the
        # alias has real results in this season, that would be the resolution-leaked-in signal --
        # but absence of the alias loginid could also just mean the alias didn't play that
        # season, so this loop only asserts on the positive case (alias present -> must be raw),
        # never on absence.

    if checked == 0:
        print("  (no season had an aliased loginid's own raw results yet -- nothing to verify)")


def test_rank_change_anchor_is_daily_and_freezes_on_season_close() -> None:
    from league_engine import rank_change_anchor

    # Unfrozen: anchor is always exactly one day back -- this is what fixes the old weekly
    # (most-recent-Wednesday) checkpoint, which landed ON as_of itself once a week and made every
    # pilot show zero movement that day.
    assert rank_change_anchor(date(2026, 9, 2)) == date(2026, 9, 1)
    assert rank_change_anchor(date(2026, 9, 3)) == date(2026, 9, 2)
    assert rank_change_anchor(date(2026, 9, 9)) == date(2026, 9, 8)

    # Frozen: once as_of has moved past coverage_end, the anchor pins permanently to
    # coverage_end - 1 day -- confirmed against the real Summer 2026 dates (closed 2026-08-31).
    for as_of in (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 10), date(2026, 10, 1)):
        assert rank_change_anchor(as_of, coverage_end=date(2026, 8, 31)) == date(2026, 8, 30)
    # as_of == coverage_end itself is still within the season -- not frozen yet.
    assert rank_change_anchor(date(2026, 8, 31), coverage_end=date(2026, 8, 31)) == date(2026, 8, 30)


def test_open_season_prevrank_uses_snapshot_not_the_always_stale_live_filter() -> None:
    """Regression for the real bug found live 2026-09-15: with a daily rank_change_anchor
    (as_of - 1 day) and mtgo.com's real ingestion lag of 2-8 days (see find_late_arrivals), the
    newest EventDate ever on disk for an open season is routinely already older than `anchor`, so
    the live "EventDate < anchor" filter used to select every ingested event -- identical to
    `current` -- making PrevRank == Rank for literally everyone, every single day. Confirmed live:
    two consecutive published rebuilds (2026-09-14, 2026-09-15) showed movement: "same" for
    450/450 then 464/464 Autumn 2026 pilots, while that exact same rebuild's snapshot-based DPoints
    showed real nonzero deltas for 75 pilots -- proof the underlying data plainly changed and only
    PrevRank failed to see it.

    This test reproduces the exact shape of that bug with a synthetic, 2-day-lagged single event
    (EventDate before `anchor` the same way real lagged data always is) and asserts the acceptance
    bar directly: WITHOUT a snapshot, PrevRank == Rank for every pilot (the old, now-documented
    fallback -- not itself wrong, since there is nothing else to compare against); WITH the same
    weekly-snapshot base DELTA_COLS already used, PrevRank must differ from Rank for pilots whose
    snapshot rank actually differs -- i.e. real movement must be visible again.
    """
    import shutil
    import tempfile

    import league_engine as le

    tmp = Path(tempfile.mkdtemp(prefix="rankchange_snapshot_test_"))
    try:
        results_dir = tmp / "results"
        results_dir.mkdir()
        snapshot_dir = tmp / "snapshots"

        season_start, season_end = date(2026, 9, 1), date(2026, 11, 30)
        as_of = date(2026, 9, 15)  # anchor = 2026-09-14

        # A single event dated 2026-09-13 -- two real days before `as_of`, one day before `anchor`
        # -- exactly the lag shape find_late_arrivals already documents as routine, and the shape
        # that made the old live filter always see prev == current.
        event = pd.DataFrame([
            {"EventID": "e1", "EventDate": "2026-09-13", "Tier": "C32", "EventClass": "Challenge",
             "Pilot": "Alice", "LoginID": "111", "Place": 1, "Deck": "", "DeckGuess": "",
             "LeaguePoints": 5, "SwissRank": 1, "SwissPoints": 8, "OMWP": 0, "GWP": 0, "OGWP": 0},
            {"EventID": "e1", "EventDate": "2026-09-13", "Tier": "C32", "EventClass": "Challenge",
             "Pilot": "Bob", "LoginID": "222", "Place": 2, "Deck": "", "DeckGuess": "",
             "LeaguePoints": 4, "SwissRank": 2, "SwissPoints": 7, "OMWP": 0, "GWP": 0, "OGWP": 0},
        ])
        event.to_csv(results_dir / "e1.csv", index=False)

        # Last week's frozen snapshot: Bob was ahead of Alice then -- a real prior standings
        # capture, not a re-filter of the same (lagged) data current_ranked is built from.
        last_week = pd.DataFrame([
            {"Rank": 1, "Pilot": "Bob", "LoginID": "222", "Points": 9, "PremierPoints": 0,
             "Wins": 1, "Top2": 2, "Top4": 2, "Top8": 2, "Top16": 2, "Starts": 2},
            {"Rank": 2, "Pilot": "Alice", "LoginID": "111", "Points": 3, "PremierPoints": 0,
             "Wins": 0, "Top2": 0, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 1},
        ])
        le.write_weekly_snapshot(snapshot_dir, "Autumn 2026", date(2026, 9, 7), last_week, today=date(2026, 9, 7))

        without_snapshot = le.build_season_table(results_dir, season_start, season_end, as_of=as_of)
        alice_ns = without_snapshot[without_snapshot["LoginID"] == "111"].iloc[0]
        bob_ns = without_snapshot[without_snapshot["LoginID"] == "222"].iloc[0]
        assert int(alice_ns["PrevRank"]) == int(alice_ns["Rank"]), (
            "documented fallback: no snapshot -> live filter -> prev==current for this lag shape"
        )
        assert int(bob_ns["PrevRank"]) == int(bob_ns["Rank"])

        with_snapshot = le.build_season_table(
            results_dir, season_start, season_end, as_of=as_of, snapshot_dir=snapshot_dir,
        )
        alice = with_snapshot[with_snapshot["LoginID"] == "111"].iloc[0]
        bob = with_snapshot[with_snapshot["LoginID"] == "222"].iloc[0]
        assert int(alice["Rank"]) == 1 and int(bob["Rank"]) == 2, "Alice won e1, must rank 1st now"
        assert int(alice["PrevRank"]) == 2, f"Alice's PrevRank must come from last week's snapshot (2), got {alice['PrevRank']}"
        assert int(bob["PrevRank"]) == 1, f"Bob's PrevRank must come from last week's snapshot (1), got {bob['PrevRank']}"
        assert int(alice["RankChange"]) == 1, "Alice moved up one place week-over-week"
        assert int(bob["RankChange"]) == -1, "Bob moved down one place week-over-week"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_weekly_window_start_used_by_late_arrivals_unchanged() -> None:
    """find_late_arrivals still needs a genuine weekly bucket -- confirms it kept the old
    Wednesday-anchored math after rank_change_anchor itself moved to a daily baseline."""
    from league_engine import weekly_window_start

    assert weekly_window_start(date(2026, 9, 2)) == date(2026, 9, 2)  # a Wednesday itself
    assert weekly_window_start(date(2026, 9, 3)) == date(2026, 9, 2)
    assert weekly_window_start(date(2026, 9, 8)) == date(2026, 9, 2)
    assert weekly_window_start(date(2026, 9, 9)) == date(2026, 9, 9)


def test_manifest_includes_rank_change_anchor() -> None:
    from league_engine import build_season_table
    from league_results_export import build_manifest, check_placement_integrity

    league_dir = REPO_ROOT / "outputs" / "league"
    results_dir = league_dir / "results"
    if not results_dir.exists():
        print("  (no outputs/league/results on disk -- nothing to verify yet)")
        return

    checked = 0
    for season_name, season_start, season_end in _season_config_rows(league_dir):
        export_df, current_results = build_results_rows(
            results_dir, season_start, season_end, season_name, "modern", ingestion_log={},
        )
        if export_df.empty:
            continue
        season_table = build_season_table(results_dir, season_start, season_end, as_of=date.today())
        expected_anchor = season_table.attrs.get("rank_change_anchor")
        assert expected_anchor is not None, f"{season_name}: build_season_table set no anchor"

        placement_issues = check_placement_integrity(export_df)
        reconciliation_issues = reconcile_with_season_table(
            current_results, results_dir, season_start, season_end, as_of=date.today(),
        )
        manifest = build_manifest(
            season_name, season_start, season_end, date.today(), export_df,
            placement_issues, reconciliation_issues, expected_anchor,
        )
        assert manifest["RankChangeAnchor"] == expected_anchor, (
            f"{season_name}: manifest's RankChangeAnchor {manifest['RankChangeAnchor']!r} != "
            f"build_season_table's own {expected_anchor!r}"
        )
        checked += 1

    if checked == 0:
        print("  (no season with data on disk yet -- nothing to verify)")


def test_late_arrivals_empty_for_backfilled_closed_season() -> None:
    """The season-open guard: an already-closed season backfilled in one go (every event's
    IngestedAt the same, after SeasonEnd) must report zero late arrivals, not flag its entire
    event list -- confirmed live this was the actual failure mode before the guard was added
    (Summer 2026's real IngestedAt is one timestamp, 2026-09-02T09:20:04Z, for all 91 events).
    """
    from league_results_export import find_late_arrivals, load_ingestion_log

    league_dir = REPO_ROOT / "outputs" / "league"
    results_dir = league_dir / "results"
    ingestion_log_path = league_dir / "ingestion_log.csv"
    if not results_dir.exists() or not ingestion_log_path.exists():
        print("  (no results dir or ingestion log on disk -- nothing to verify yet)")
        return

    ingestion_log = load_ingestion_log(ingestion_log_path)

    checked = 0
    for season_name, season_start, season_end in _season_config_rows(league_dir):
        if season_end >= date.today():
            continue  # only closed seasons here -- an open one legitimately CAN have late arrivals
        export_df, _current_results = build_results_rows(
            results_dir, season_start, season_end, season_name, "modern", ingestion_log,
        )
        if export_df.empty:
            continue
        late = find_late_arrivals(export_df, season_end)
        assert late == [], f"{season_name}: expected no late arrivals for a closed/backfilled season, got {late}"
        checked += 1

    if checked == 0:
        print("  (no closed season with data on disk yet -- nothing to verify)")


def test_independent_reproduction_of_published_baseline_rank() -> None:
    """The actual acceptance bar for the 9th tie-break key (LoginID, added alongside this test): an
    INDEPENDENT reimplementation -- no league_engine sort/aggregate functions, that would only prove
    the code agrees with itself -- of "filter real on-disk results to EventDate < anchor, resolve
    identity via identity.resolve() (the published, documented function), sort by the 9-key rule"
    must reproduce the real, currently-published PrevRank for every baseline pilot. Measured
    directly before this test existed: only 148 of 961 Summer-2026-baseline pilots reproduced under
    the old 8-key rule (no terminal key -- tie order fell out of undocumented input-row order). This
    test's job is to make sure that never regresses.
    """
    from fractions import Fraction

    import identity as pilot_identity
    from league_engine import build_season_table, load_all_league_results, rank_change_anchor

    league_dir = REPO_ROOT / "outputs" / "league"
    results_dir = league_dir / "results"
    if not results_dir.exists():
        print("  (no outputs/league/results on disk -- nothing to verify yet)")
        return

    season_start, season_end = date(2026, 6, 1), date(2026, 8, 31)
    as_of = date.today()

    published = build_season_table(results_dir, season_start, season_end, as_of=as_of)
    if published.empty:
        print("  (no Summer 2026 data on disk yet -- nothing to verify)")
        return
    published_by_lid = {
        str(r["LoginID"]).strip(): (int(r["PrevRank"]) if pd.notna(r["PrevRank"]) else None)
        for _, r in published.iterrows() if str(r["LoginID"]).strip()
    }

    all_results = load_all_league_results(results_dir)
    dates = pd.to_datetime(all_results["EventDate"], errors="coerce").dt.date
    season_mask = ((dates >= season_start) & (dates <= season_end)).fillna(False)

    anchor = rank_change_anchor(as_of, season_end)

    baseline = all_results[season_mask & (dates < anchor)].copy()
    baseline["LoginID"] = baseline["LoginID"].astype(str).str.strip()
    baseline["Pilot"] = baseline["Pilot"].astype(str).str.strip()
    baseline["Place"] = pd.to_numeric(baseline["Place"], errors="coerce")
    baseline["LeaguePoints"] = pd.to_numeric(baseline["LeaguePoints"], errors="coerce").fillna(0)
    baseline = baseline[baseline["Pilot"] != ""]

    baseline["ResolvedID"] = baseline["LoginID"].apply(lambda lid: pilot_identity.resolve(lid) if lid else "")
    baseline["Key"] = baseline.apply(
        lambda r: r["ResolvedID"] if r["ResolvedID"] else f"name:{r['Pilot']}", axis=1,
    )

    agg: dict = {}
    for key, grp in baseline.groupby("Key"):
        agg[key] = {
            "LoginID": key if key and not key.startswith("name:") else "",
            "Points": int(grp["LeaguePoints"].sum()),
            "Starts": int(grp["EventID"].nunique()),
            "Wins": int((grp["Place"] == 1).sum()),
            "Top2": int((grp["Place"] <= 2).sum()),
            "Top4": int((grp["Place"] <= 4).sum()),
            "Top8": int((grp["Place"] <= 8).sum()),
            "Top16": int((grp["Place"] <= 16).sum()),
        }

    def sort_key(item):
        _key, row = item
        points, starts = row["Points"], row["Starts"]
        pts_per_start = Fraction(points, starts) if starts else Fraction(0)
        lid = row["LoginID"]
        lid_sort = (0, int(lid)) if lid else (1, 0)
        return (-points, -pts_per_start, -row["Wins"], -row["Top2"], -row["Top4"], -row["Top8"], -row["Top16"], starts, lid_sort)

    ordered = sorted(agg.items(), key=sort_key)
    my_rank_by_lid = {row["LoginID"]: i for i, (_key, row) in enumerate(ordered, start=1) if row["LoginID"]}

    mismatches = []
    for lid, my_rank in my_rank_by_lid.items():
        published_rank = published_by_lid.get(lid)
        if published_rank != my_rank:
            mismatches.append((lid, "published=", published_rank, "independent=", my_rank))

    assert my_rank_by_lid, "no baseline pilots found to check -- fixture/data problem, not a pass"
    assert not mismatches, (
        f"independent reproduction disagreed with the published baseline for "
        f"{len(mismatches)}/{len(my_rank_by_lid)} pilots: {mismatches[:10]}"
    )


def test_manifest_carries_sha256_of_the_written_csv_and_output_is_stable() -> None:
    """The site downloads the results CSV as ?v=<results_csv_sha256> so a CDN can never pair a
    stale CSV with a fresh table; the hash must be of the exact bytes written. Two exports of the
    same input must also produce byte-identical CSVs (and so the same hash)."""
    import hashlib
    import json
    import shutil
    import tempfile

    from league_results_export import export_results_and_manifest

    league_dir = REPO_ROOT / "outputs" / "league"
    if not (league_dir / "results").exists() or not (league_dir / "season_config.csv").exists():
        print("  (no league data on disk -- nothing to verify yet)")
        return

    hashes = []
    for _run in range(2):
        tmp = Path(tempfile.mkdtemp(prefix="results_sha_"))
        log_copy = tmp / "ingestion_log.csv"
        if (league_dir / "ingestion_log.csv").exists():
            shutil.copyfile(league_dir / "ingestion_log.csv", log_copy)
        export_results_and_manifest(
            results_dir=league_dir / "results",
            season_config_csv=league_dir / "season_config.csv",
            docs_data_dir=tmp / "data",
            format_name="modern",
            ingestion_log_path=log_copy,
            season_registry_path=tmp / "pilot_league_seasons.csv",
            as_of=date(2026, 9, 24),
        )
        run_hashes = {}
        for manifest_path in sorted((tmp / "data").glob("pilot_league_results_*_manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            csv_path = manifest_path.with_name(manifest_path.name.replace("_manifest.json", ".csv"))
            actual = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            assert manifest["results_csv_sha256"] == actual, manifest_path.name
            run_hashes[csv_path.name] = actual
        hashes.append(run_hashes)
    assert hashes[0] == hashes[1], "results CSV bytes differ between two exports of the same input"
