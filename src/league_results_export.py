"""Event-level results export for the Pilot League site's weekly-presentation workflow.

The season-table CSV/JSON the site already publishes (league_engine/league_site_export) is a
season-to-date snapshot -- fine for "current standings," useless for "what changed this week"
beyond the single RankChange column. This module exports one row per pilot per event appearance
(outputs/league/results/<EventID>.csv, unaggregated) so any weekly window can be reconstructed by
filtering EventDate alone, plus a manifest recording which events are included and the result of
this module's own self-validation.

Two things this module deliberately does NOT do, both confirmed against the actual pipeline before
writing this (see this repo's session notes, not repeated here):

- It does not reimplement points-formula/threshold-monotonicity/event-reconciliation/rank-continuity/
  LoginID-uniqueness checks -- those already exist in league_engine.check_league_invariants and
  validate_league, and both already run (and would already have raised) inside run_league_update,
  which always runs before this module's export_results_and_manifest is called. This module's own
  validation is scoped to what's genuinely new: full 1..32 placement coverage per event (existing
  checks only guard against *duplicate* placements, not full coverage), and an independent
  reconciliation of this export's own row set against league_engine.build_season_table's live
  output for the same window -- proving THIS module's date-window filtering didn't drift from the
  canonical one, not re-litigating arithmetic already guarded upstream.
- It does not resolve LoginID through pilot_identity.csv's human-curated merges. The exported
  LoginID is the raw per-account id from the source data, on purpose -- "identity is LoginID and
  nothing else" for this export. The one place this module DOES resolve identity is internally,
  inside reconcile_with_season_table, so that check compares like with like against the season
  table (which does resolve identity) instead of spuriously failing on a legitimate recorded merge.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

try:
    from league_engine import (
        aggregate_pilot_table,
        build_season_table,
        load_all_league_results,
        season_filename_slug,
        _rank_table,
        _write_csv_lf,
    )
    from league_site_export import _event_label, _write_json
except ImportError:
    from .league_engine import (
        aggregate_pilot_table,
        build_season_table,
        load_all_league_results,
        season_filename_slug,
        _rank_table,
        _write_csv_lf,
    )
    from .league_site_export import _event_label, _write_json

RESULTS_EXPORT_COLS: List[str] = [
    "EventDate", "IngestedAt", "EventID", "EventName", "EventType", "Format", "Season",
    "LoginID", "Name", "Placement", "PointsAwarded",
]

INGESTION_LOG_COLS: List[str] = ["EventID", "IngestedAtUTC"]

# The season-aggregate columns compared between this module's own re-derivation and the canonical
# league_engine.build_season_table output -- deliberately excludes PrevRank/RankChange/DELTA_COLS
# (separate machinery, already independently validated elsewhere; coupling this check to
# prevrank_cutoff_days/snapshot_dir bookkeeping would test something this module doesn't own).
_RECONCILE_COLS: List[str] = [
    "Rank", "LoginID", "Points", "PremierPoints", "Wins", "Top2", "Top4", "Top8", "Top16", "Starts",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_ingestion_log(log_path: Path) -> Dict[str, str]:
    if not log_path.exists():
        return {}
    df = pd.read_csv(log_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    return dict(zip(df["EventID"], df["IngestedAtUTC"]))


def update_ingestion_log(results_dir: Path, log_path: Path) -> Dict[str, str]:
    """Write-once per EventID: the first time an EventID's result file is seen here, its
    IngestedAtUTC is recorded and never touched again on a later run -- idempotent by
    construction. Kept as its own log rather than a hook inside run_league_update (whose own
    docstring calls itself "the single code path that writes league data") -- this module only
    ever reads results_dir, never mutates run_league_update's own outputs.
    """
    log = load_ingestion_log(log_path)
    current_ids = {p.stem for p in results_dir.glob("*.csv")} if results_dir.exists() else set()
    new_ids = sorted(current_ids - log.keys())
    if new_ids:
        now = _utcnow_iso()
        for eid in new_ids:
            log[eid] = now
        df = pd.DataFrame(sorted(log.items()), columns=INGESTION_LOG_COLS)
        _write_csv_lf(df, log_path)
    return log


def _name_by_raw_login_id(current_results: pd.DataFrame) -> Dict[str, str]:
    """Each raw LoginID's own most-recent-EventDate Pilot string. Deliberately NOT resolved
    through pilot_identity.csv -- that would fold two distinct real accounts under one canonical
    name, contradicting this export's "identity is LoginID and nothing else" rule. Same
    latest-name-wins shape as league_engine.aggregate_pilot_table's display-name resolution, just
    keyed by raw LoginID instead of the resolved identity key.
    """
    work = current_results.copy()
    work["LoginID"] = work["LoginID"].astype(str).str.strip()
    work["Pilot"] = work["Pilot"].astype(str).str.strip()
    work = work[work["LoginID"] != ""]
    if work.empty:
        return {}
    work["_dt"] = pd.to_datetime(work["EventDate"], errors="coerce")
    first_pilot = work.groupby("LoginID")["Pilot"].first()
    dated = work.dropna(subset=["_dt"])
    if dated.empty:
        return first_pilot.to_dict()
    latest_idx = dated.groupby("LoginID")["_dt"].idxmax()
    latest_pilot = work.loc[latest_idx.values].set_index(latest_idx.index)["Pilot"]
    return latest_pilot.combine_first(first_pilot).to_dict()


def build_results_rows(
    results_dir: Path,
    season_start: date,
    season_end: date,
    season_name: str,
    format_name: str,
    ingestion_log: Dict[str, str],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (export_df, current_results) -- current_results is the raw, season-filtered slice
    of load_all_league_results(results_dir) that export_df was built from, handed back so
    reconcile_with_season_table can re-derive the season aggregate from the exact same rows
    without re-reading and re-filtering results_dir a second time.
    """
    all_results = load_all_league_results(results_dir)
    if all_results.empty:
        return pd.DataFrame(columns=RESULTS_EXPORT_COLS), all_results

    dates = pd.to_datetime(all_results["EventDate"], errors="coerce").dt.date
    mask = ((dates >= season_start) & (dates <= season_end)).fillna(False)
    current_results = all_results[mask].copy()
    if current_results.empty:
        return pd.DataFrame(columns=RESULTS_EXPORT_COLS), current_results

    login_id = current_results["LoginID"].astype(str).str.strip()
    name_by_login = _name_by_raw_login_id(current_results)

    out = pd.DataFrame({
        "EventDate": current_results["EventDate"],
        "IngestedAt": current_results["EventID"].map(ingestion_log).fillna(""),
        "EventID": current_results["EventID"],
        "EventName": [
            _event_label(t, c)
            for t, c in zip(current_results["Tier"], current_results["EventClass"])
        ],
        "EventType": current_results["EventClass"].map(
            {"Challenge": "challenge", "Premier": "premier"}
        ).fillna("challenge"),
        "Format": format_name,
        "Season": season_name,
        "LoginID": login_id,
        # Blank LoginID (pre-2026-07-13 legacy rows, same cutoff league_engine._identity_key's own
        # docstring documents) falls back to that row's own Pilot string -- never fabricated.
        "Name": login_id.map(name_by_login).fillna(current_results["Pilot"]),
        "Placement": pd.to_numeric(current_results["Place"], errors="coerce").astype("Int64"),
        "PointsAwarded": pd.to_numeric(current_results["LeaguePoints"], errors="coerce").fillna(0).astype(int),
    })
    out = out.sort_values(["EventDate", "EventID", "Placement"], kind="mergesort").reset_index(drop=True)
    return out, current_results


def check_placement_integrity(export_df: pd.DataFrame) -> List[dict]:
    """The one genuinely new per-event check (see module docstring): each EventID must have
    exactly 32 rows, Placement 1..32 each present exactly once. league_engine.check_league_invariants
    already guards against *duplicate* (EventID, Place) pairs but not full 1..32 *coverage* -- this
    is the coverage half of that same guarantee.
    """
    offending = []
    expected = list(range(1, 33))
    for event_id, grp in export_df.groupby("EventID"):
        placements = sorted(int(p) for p in grp["Placement"].dropna())
        if placements != expected:
            missing = sorted(set(expected) - set(placements))
            dupes = sorted({p for p in placements if placements.count(p) > 1})
            offending.append({
                "EventID": str(event_id), "row_count": int(len(grp)),
                "missing_placements": missing, "duplicate_placements": dupes,
            })
    return offending


def reconcile_with_season_table(
    current_results: pd.DataFrame,
    results_dir: Path,
    season_start: date,
    season_end: date,
    as_of: date,
) -> List[dict]:
    """Independently re-derives the season aggregate from the SAME filtered raw rows this export
    was built from (aggregate_pilot_table + _rank_table, both imported/reused, never
    reimplemented -- this is "hardcode the rule in one place and derive the sort from it"), and
    diffs it row-for-row against league_engine.build_season_table's live output for the identical
    window. Both paths call the same two functions internally on the same underlying files, so a
    mismatch here means THIS module's date-window filtering diverged from build_season_table's --
    exactly the class of bug this check exists to catch, not a re-test of arithmetic already
    guarded by check_league_invariants/validate_league upstream.
    """
    mine = _rank_table(aggregate_pilot_table(current_results))
    canonical = build_season_table(results_dir, season_start, season_end, as_of=as_of)
    if canonical.empty and mine.empty:
        return []
    mine_cmp = mine[_RECONCILE_COLS].reset_index(drop=True) if not mine.empty else mine
    canon_cmp = canonical[_RECONCILE_COLS].reset_index(drop=True) if not canonical.empty else canonical
    if len(mine_cmp) != len(canon_cmp):
        return [{
            "error": "row count mismatch between this export's re-derivation and the season table",
            "mine_rows": int(len(mine_cmp)), "canonical_rows": int(len(canon_cmp)),
        }]
    diff_mask = (mine_cmp != canon_cmp).any(axis=1)
    if not diff_mask.any():
        return []

    def _row_to_jsonable(row: pd.Series) -> dict:
        # numpy scalars (int64/float64/...) aren't JSON-serializable as-is -- .item() unwraps to
        # the native Python type; anything without .item() (e.g. a plain str) passes through.
        return {k: (v.item() if hasattr(v, "item") else v) for k, v in row.items()}

    return [
        {
            "Rank": int(mine_cmp.loc[idx, "Rank"]),
            "mine": _row_to_jsonable(mine_cmp.loc[idx]),
            "canonical": _row_to_jsonable(canon_cmp.loc[idx]),
        }
        for idx in mine_cmp.index[diff_mask]
    ]


def build_manifest(
    season_name: str,
    season_start: date,
    season_end: date,
    as_of: date,
    export_df: pd.DataFrame,
    placement_issues: List[dict],
    reconciliation_issues: List[dict],
) -> dict:
    event_groups = (
        export_df.groupby(["EventID", "EventDate", "EventType"], as_index=False)
        .size()
        .rename(columns={"size": "RowCount"})
    )
    events_list = sorted(
        (
            {
                "event_id": str(r.EventID), "date": str(r.EventDate),
                "type": str(r.EventType), "rows": int(r.RowCount),
            }
            for r in event_groups.itertuples(index=False)
        ),
        key=lambda e: (e["date"], e["event_id"]),
    )
    events_challenge = sum(1 for e in events_list if e["type"] == "challenge")
    events_premier = sum(1 for e in events_list if e["type"] == "premier")

    points_by_login = export_df.groupby("LoginID")["PointsAwarded"].sum()

    checks = [
        {"name": "placement_integrity", "passed": not placement_issues, "offending": placement_issues[:50]},
        {
            "name": "reconciliation_with_season_table", "passed": not reconciliation_issues,
            "offending": reconciliation_issues[:50],
        },
    ]

    return {
        "season": season_name,
        "season_start": season_start.isoformat(),
        "season_end": season_end.isoformat(),
        "generated_at": _utcnow_iso(),
        "as_of": as_of.isoformat(),
        "events_total": len(events_list),
        "events_challenge": events_challenge,
        "events_premier": events_premier,
        "events": events_list,
        "pilots_total": int(export_df["LoginID"].nunique()),
        "pilots_scoring": int((points_by_login > 0).sum()),
        "points_total": int(export_df["PointsAwarded"].sum()),
        "starts_total": int(len(export_df)),
        "validation": {"checks": checks, "all_passed": all(c["passed"] for c in checks)},
    }


def export_results_and_manifest(
    results_dir: Path,
    season_config_csv: Path,
    docs_data_dir: Path,
    format_name: str,
    ingestion_log_path: Path,
    as_of: date,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Runs for every season on record (same "every season, not just the one this run touched"
    convention league_site_export.export_league_site already uses), independently per season: a
    validation failure for one season is logged and that season's files are simply not written --
    it never blocks publishing an already-valid season's export. Returns False if ANY season
    failed validation (so the caller can surface it), never raises.
    """
    def emit(msg: str) -> None:
        if log:
            log(f"[results-export] {msg}")

    if not season_config_csv.exists():
        emit("no season_config.csv yet -- nothing to export")
        return True

    seasons_df = pd.read_csv(season_config_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    ingestion_log = update_ingestion_log(results_dir, ingestion_log_path)

    overall_ok = True
    for _, row in seasons_df.iterrows():
        season_name = row["Season"]
        try:
            season_start = date.fromisoformat(row["StartDate"])
            season_end = date.fromisoformat(row["EndDate"])
        except ValueError:
            emit(f"WARN skipping {season_name!r}: unparseable StartDate/EndDate")
            continue
        if season_end < season_start:
            continue

        export_df, current_results = build_results_rows(
            results_dir, season_start, season_end, season_name, format_name, ingestion_log,
        )
        if export_df.empty:
            emit(f"{season_name}: no results yet -- skipping")
            continue

        placement_issues = check_placement_integrity(export_df)
        reconciliation_issues = reconcile_with_season_table(
            current_results, results_dir, season_start, season_end, as_of,
        )
        manifest = build_manifest(
            season_name, season_start, season_end, as_of, export_df,
            placement_issues, reconciliation_issues,
        )

        slug = season_filename_slug(season_name)
        csv_path = docs_data_dir / f"pilot_league_results_{slug}.csv"
        manifest_path = docs_data_dir / f"pilot_league_results_{slug}_manifest.json"

        if not manifest["validation"]["all_passed"]:
            overall_ok = False
            emit(f"{season_name}: VALIDATION FAILED -- not publishing")
            for check in manifest["validation"]["checks"]:
                if not check["passed"]:
                    emit(f"  {check['name']}: {check['offending']}")
            continue

        export_out = export_df.copy()
        export_out["Placement"] = export_out["Placement"].astype(int)
        _write_csv_lf(export_out[RESULTS_EXPORT_COLS], csv_path)
        _write_json(manifest, manifest_path)
        emit(f"{season_name}: {len(export_df)} row(s) -> {csv_path.name} + {manifest_path.name}")

    return overall_ok
