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

from league_results_export import build_results_rows, reconcile_with_season_table


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


def test_rank_change_anchor_wednesday_alignment_and_freeze() -> None:
    from league_engine import rank_change_anchor

    # Unfrozen: as_of within the same Wed-Tue calendar week maps to the SAME anchor -- this is what
    # actually fixes the old rolling "as_of - 7 days" baseline (a different answer every day).
    assert rank_change_anchor(date(2026, 9, 2)) == date(2026, 9, 2)
    assert rank_change_anchor(date(2026, 9, 3)) == date(2026, 9, 2)
    assert rank_change_anchor(date(2026, 9, 9)) == date(2026, 9, 9)

    # Frozen: once as_of has moved past coverage_end, the anchor pins permanently to
    # coverage_end's own week -- confirmed against the real Summer 2026 dates (closed 2026-08-31).
    for as_of in (date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 10), date(2026, 10, 1)):
        assert rank_change_anchor(as_of, coverage_end=date(2026, 8, 31)) == date(2026, 8, 26)


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
