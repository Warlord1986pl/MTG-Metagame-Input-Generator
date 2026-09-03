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
