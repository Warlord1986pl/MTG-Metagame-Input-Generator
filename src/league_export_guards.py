"""Export-time consistency guards between the two published league exports.

The site publishes, per season, the standings (docs/data/season_<slug>.json -- the browser turns it
into pilot_league_<slug>_<AsOf>.csv) and the event-level results
(docs/data/pilot_league_results_<slug>.csv, one row per pilot per event, raw LoginIDs). A downstream
weekly edition rebuilds the table from results and reconciles it against standings per LoginID,
so the two must describe exactly the same set of events. This module checks that on the files as
written to disk, immediately before deploy, and is the gate the Mikrus collector runs before it
mirrors or pushes anything: any failure means a non-zero exit and no deploy.

Checks, per season:
  1. no null or non-integer LoginID in either file;
  2. sum(standings.Wins) == nunique(results.EventID);
  3. sum(standings.Starts) == len(results);
  4. sum(standings.Points) == sum(results.PointsAwarded);
  5. every event's PointsAwarded sums to 31 (Challenge) or 62 (premier, x2);
  6. per LoginID, Wins/Top2/Top4/Top8/Top16/Starts in standings equal those rebuilt from results
     after resolving raw LoginIDs through the pipeline's own merge map (data/pilot_identity.csv);
  7. the latest EventDate in results equals the latest event in the standings snapshot (the
     per-pilot event lists in docs/data/pilots_<slug>.json, built in the same run as standings).

Read-only. Never fixes anything, never back-fills by name.

CLI: python src/league_export_guards.py --docs-data docs/data   (exit 0 = all seasons pass)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    import identity
except ImportError:  # pragma: no cover
    from . import identity

VALID_EVENT_POINT_SUMS = (31, 62)
BRACKET_COLS = ["wins", "top2", "top4", "top8", "top16", "starts"]
_INT_RE = re.compile(r"^\d+$")


def _is_int_id(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value >= 0
    return bool(_INT_RE.match(str(value).strip()))


def check_season(
    season_json: Path,
    results_csv: Path,
    pilots_json: Optional[Path] = None,
    identity_map: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Returns a list of human-readable failures for one season; empty means all checks passed."""
    if not results_csv.exists():
        return [f"results file missing: {results_csv.name}"]
    season = json.loads(season_json.read_text(encoding="utf-8"))
    pilots = season.get("pilots") or []
    results = pd.read_csv(results_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    failures: List[str] = []

    # 1. LoginID present and integer in both files.
    bad_std = [(p.get("rank"), p.get("name"), p.get("loginId")) for p in pilots if not _is_int_id(p.get("loginId"))]
    if bad_std:
        failures.append(
            f"standings: {len(bad_std)} row(s) with null/non-integer LoginID (Rank, Name, LoginID): {bad_std[:10]}"
        )
    id_ok = results["LoginID"].str.strip().str.match(_INT_RE)
    if not id_ok.all():
        sample = results.loc[~id_ok, ["EventID", "Placement", "Name", "LoginID"]].head(10).to_dict("records")
        failures.append(f"results: {int((~id_ok).sum())} row(s) with null/non-integer LoginID: {sample}")

    points_awarded = pd.to_numeric(results["PointsAwarded"], errors="coerce")
    placement = pd.to_numeric(results["Placement"], errors="coerce")

    # 2-4. Totals.
    totals = {
        "sum(Wins) vs nunique(EventID)": (sum(int(p.get("wins") or 0) for p in pilots), results["EventID"].nunique()),
        "sum(Starts) vs results rows": (sum(int(p.get("starts") or 0) for p in pilots), len(results)),
        "sum(Points) vs sum(PointsAwarded)": (
            sum(int(p.get("points") or 0) for p in pilots), int(points_awarded.fillna(0).sum())
        ),
    }
    for label, (std, res) in totals.items():
        if std != res:
            failures.append(f"{label}: standings {std} != results {res} (delta {std - res})")

    # 5. Per-event points.
    per_event = points_awarded.groupby(results["EventID"]).sum()
    odd = per_event[~per_event.isin(VALID_EVENT_POINT_SUMS)]
    if len(odd):
        failures.append(f"events whose PointsAwarded is not 31 or 62: {odd.astype(int).to_dict()}")

    # 6. Per-LoginID bracket columns, after the pipeline's own merge map.
    idmap = identity_map if identity_map is not None else identity.load_identity()
    work = results.assign(_place=placement)[id_ok]
    work = work.assign(_key=[identity.resolve(lid, idmap) for lid in work["LoginID"].str.strip()])
    grp = work.groupby("_key")
    rebuilt = pd.DataFrame({
        "wins": grp["_place"].apply(lambda s: int((s == 1).sum())),
        "top2": grp["_place"].apply(lambda s: int((s <= 2).sum())),
        "top4": grp["_place"].apply(lambda s: int((s <= 4).sum())),
        "top8": grp["_place"].apply(lambda s: int((s <= 8).sum())),
        "top16": grp["_place"].apply(lambda s: int((s <= 16).sum())),
        "starts": grp["EventID"].nunique(),
    })
    std_rows = [p for p in pilots if _is_int_id(p.get("loginId"))]
    standings = pd.DataFrame(
        [{c: int(p.get(c) or 0) for c in BRACKET_COLS} for p in std_rows],
        index=[str(p["loginId"]).strip() for p in std_rows],
        columns=BRACKET_COLS,
    )
    if standings.index.duplicated().any():
        failures.append(f"standings: duplicate LoginID {sorted(set(standings.index[standings.index.duplicated()]))}")
        standings = standings[~standings.index.duplicated()]
    only_std = sorted(set(standings.index) - set(rebuilt.index))
    only_res = sorted(set(rebuilt.index) - set(standings.index))
    if only_std:
        failures.append(f"{len(only_std)} standings LoginID(s) absent from results: {only_std[:10]}")
    if only_res:
        failures.append(f"{len(only_res)} results LoginID(s) absent from standings: {only_res[:10]}")
    common = sorted(set(standings.index) & set(rebuilt.index))
    diff = standings.loc[common, BRACKET_COLS].ne(rebuilt.loc[common, BRACKET_COLS]).any(axis=1)
    if diff.any():
        ids = list(diff[diff].index)
        sample = {
            i: {"standings": standings.loc[i].to_dict(), "results": rebuilt.loc[i].astype(int).to_dict()}
            for i in ids[:5]
        }
        failures.append(f"{len(ids)} LoginID(s) differ in bracket columns/Starts: {sample}")

    # 7. Latest event date.
    if pilots_json is not None:
        pilots_doc = json.loads(pilots_json.read_text(encoding="utf-8"))
        dates = [
            r.get("date")
            for p in (pilots_doc.get("pilots") or {}).values()
            for r in (p.get("results") or [])
            if r.get("date")
        ]
        std_latest = max(dates) if dates else None
        res_latest = results["EventDate"].max() if len(results) else None
        if std_latest != res_latest:
            failures.append(f"latest EventDate: results {res_latest} != standings snapshot {std_latest}")

    return failures


def check_docs_data(
    docs_data_dir: Path,
    identity_map: Optional[Dict[str, str]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Runs check_season for every season_<slug>.json in *docs_data_dir*. True if all pass."""
    emit = log or print
    seasons = sorted(docs_data_dir.glob("season_*.json"))
    if not seasons:
        emit("[export-guards] no season_*.json found -- nothing to check")
        return False
    ok = True
    for season_json in seasons:
        slug = season_json.stem[len("season_"):]
        pilots_json = docs_data_dir / f"pilots_{slug}.json"
        failures = check_season(
            season_json,
            docs_data_dir / f"pilot_league_results_{slug}.csv",
            pilots_json if pilots_json.exists() else None,
            identity_map,
        )
        if failures:
            ok = False
            emit(f"[export-guards] {slug}: FAILED ({len(failures)})")
            for f in failures:
                emit(f"[export-guards]   {f}")
        else:
            emit(f"[export-guards] {slug}: all checks passed")
    return ok


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Consistency guards between standings and results exports.")
    ap.add_argument("--docs-data", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "data")
    args = ap.parse_args(argv)
    return 0 if check_docs_data(args.docs_data) else 1


if __name__ == "__main__":
    sys.exit(main())
