"""Pilot identity merge/audit CLI -- the only way `data/pilot_identity.csv`,
`data/pilot_profile.csv`, and `data/pilot_merge_log.csv` are meant to be edited (never by hand,
never by the GUI). See src/identity.py for the overlay itself and how it's wired into
league_engine._identity_key / challenge_history_engine._identity_key.

`merge` defaults to dry-run: it always prints the full before/after report (loginid -> pilot_id
mapping, season leaderboard before/after, row-count deltas, validations a-e), but writes nothing
unless --apply is also given. On any validation failure nothing is written -- not pilot_identity.csv,
not pilot_profile.csv, not pilot_merge_log.csv -- and the process exits non-zero, in both dry-run
and --apply mode.

Usage:
  python src/pilot_identity_cli.py merge --primary 2903591 --alias 3263693 \
      --source self_request_x --evidence "DM 2026-08-25" \
      --evidence-type direct_confirmation --confirmed-on 2026-08-25 [--primary-name MeninoNey]

  python src/pilot_identity_cli.py merge --primary 2903591 --alias 3263693 \
      --source self_request_x --evidence "DM 2026-08-25" \
      --evidence-type direct_confirmation --confirmed-on 2026-08-25 --apply

  python src/pilot_identity_cli.py --show 2903591
  python src/pilot_identity_cli.py --show MeninoNey

Common options (--format/--outputs-base/--docs-dir/--data-dir) must be given before the `merge`
token, since they belong to the top-level parser, not the `merge` subcommand.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd  # noqa: E402

import identity as pilot_identity  # noqa: E402
from league_engine import (  # noqa: E402
    build_season_table,
    write_season_league_csv,
    load_all_league_results,
    _identity_key as league_identity_key,
)
from league_site_export import export_league_site  # noqa: E402
from challenge_history_engine import (  # noqa: E402
    load_challenge_history,
    _identity_key as challenge_identity_key,
    _pilot_table,
)
from metagame_input_generator import _format_slug_for_url  # noqa: E402


# --------------------------------------------------------------------------------------------
# Shared read helpers
# --------------------------------------------------------------------------------------------

def _season_config_rows(league_dir: Path) -> List[Tuple[str, date, date]]:
    config_csv = league_dir / "season_config.csv"
    if not config_csv.exists():
        return []
    config = pd.read_csv(config_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if config.empty:
        return []
    config = config.sort_values("StartDate", kind="mergesort")
    out = []
    for _, row in config.iterrows():
        season = str(row["Season"]).strip()
        s = pd.to_datetime(row["StartDate"]).date()
        e = pd.to_datetime(row["EndDate"]).date()
        out.append((season, s, e))
    return out


def _compute_season_tables(league_dir: Path, as_of: date) -> Dict[str, pd.DataFrame]:
    results_dir = league_dir / "results"
    tables = {}
    for season, s, e in _season_config_rows(league_dir):
        tables[season] = build_season_table(results_dir, s, e, as_of=as_of, prevrank_cutoff_days=7)
    return tables


def _history_paths(outputs_base: Path, format_name: str) -> Tuple[Path, Path]:
    slug = _format_slug_for_url(format_name)
    return outputs_base / f"challenge_history_{slug}.csv", outputs_base / f"premier_history_{slug}.csv"


# --------------------------------------------------------------------------------------------
# Validation (d): totals conserved before/after -- a merge only regroups existing rows, it must
# never create or drop points/wins/top8/top32.
# --------------------------------------------------------------------------------------------

def _season_sums(table: pd.DataFrame) -> Dict[str, int]:
    if table.empty:
        return {"Points": 0, "Wins": 0, "Top2": 0, "Top4": 0, "Top8": 0, "Top16": 0}
    return {
        col: int(pd.to_numeric(table[col], errors="coerce").fillna(0).sum())
        for col in ("Points", "Wins", "Top2", "Top4", "Top8", "Top16")
    }


def _bestpilots_sums(hist_df: pd.DataFrame) -> Dict[str, int]:
    tbl = _pilot_table(hist_df)
    if tbl.empty:
        return {"Wins": 0, "Top8": 0, "Top32": 0}
    return {col: int(pd.to_numeric(tbl[col], errors="coerce").fillna(0).sum()) for col in ("Wins", "Top8", "Top32")}


# --------------------------------------------------------------------------------------------
# Validation (e): no pilot_id may have two results in the same EventID -- the one check that
# actually detects an erroneous merge/account split, run over the FULL history, every season.
# --------------------------------------------------------------------------------------------

def _event_collisions(df: pd.DataFrame, identity_key_fn) -> List[dict]:
    if df.empty or "EventID" not in df.columns or "LoginID" not in df.columns:
        return []
    work = df.copy()
    work["EventID"] = work["EventID"].astype(str).str.strip()
    work["LoginID"] = work["LoginID"].astype(str).str.strip()
    work["Pilot"] = work["Pilot"].astype(str).str.strip() if "Pilot" in work.columns else ""
    work = work[work["LoginID"] != ""]
    if work.empty:
        return []
    work["_Key"] = [identity_key_fn(lid, p) for lid, p in zip(work["LoginID"], work["Pilot"])]
    problems = []
    for (eid, key), rows in work.groupby(["EventID", "_Key"]):
        if len(rows) > 1:
            problems.append({"EventID": eid, "pilot_id": key, "loginids": sorted(set(rows["LoginID"]))})
    return problems


# --------------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------------

def _leaderboard(table: pd.DataFrame, top_n: int) -> List[dict]:
    if table.empty:
        return []
    head = table.head(top_n)
    return [
        {"Rank": int(r["Rank"]), "Pilot": str(r["Pilot"]), "LoginID": str(r.get("LoginID", "")), "Points": int(r["Points"])}
        for _, r in head.iterrows()
    ]


def _print_leaderboard_diff(before: Dict[str, pd.DataFrame], after: Dict[str, pd.DataFrame], top_n: int = 20) -> None:
    for season in sorted(set(before) | set(after)):
        print(f"\n== {season} -- top {top_n} before -> after ==")
        b = _leaderboard(before.get(season, pd.DataFrame()), top_n)
        a = _leaderboard(after.get(season, pd.DataFrame()), top_n)
        print(f"{'Rk':<4}{'Pilot':<24}{'LoginID':<12}{'Pts':>6}    {'Rk':<4}{'Pilot':<24}{'LoginID':<12}{'Pts':>6}")
        for i in range(max(len(b), len(a))):
            bl, al = (b[i] if i < len(b) else None), (a[i] if i < len(a) else None)
            bs = f"{bl['Rank']:<4}{bl['Pilot']:<24}{bl['LoginID']:<12}{bl['Points']:>6}" if bl else " " * 46
            as_ = f"{al['Rank']:<4}{al['Pilot']:<24}{al['LoginID']:<12}{al['Points']:>6}" if al else ""
            print(f"{bs}    {as_}")


def _row_diff_summary(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    b_by_lid = {str(r["LoginID"]): r for _, r in before.iterrows()} if not before.empty else {}
    a_by_lid = {str(r["LoginID"]): r for _, r in after.iterrows()} if not after.empty else {}
    removed = sorted(set(b_by_lid) - set(a_by_lid))
    changed = [
        lid for lid in sorted(set(b_by_lid) & set(a_by_lid))
        if int(b_by_lid[lid]["Points"]) != int(a_by_lid[lid]["Points"])
        or int(b_by_lid[lid]["Wins"]) != int(a_by_lid[lid]["Wins"])
    ]
    return {
        "before_row_count": len(before), "after_row_count": len(after),
        "rows_removed": removed, "rows_changed": changed,
    }


def _print_row_diffs(before: Dict[str, pd.DataFrame], after: Dict[str, pd.DataFrame]) -> None:
    print("\n=== outputs/league/pilot_league_<season>.csv row deltas (docs/data/*.json mirrors these 1:1) ===")
    for season in sorted(set(before) | set(after)):
        d = _row_diff_summary(before.get(season, pd.DataFrame()), after.get(season, pd.DataFrame()))
        print(
            f"  {season}: {d['before_row_count']} -> {d['after_row_count']} row(s); "
            f"removed (merged away) {d['rows_removed']}; changed {d['rows_changed']}"
        )


# --------------------------------------------------------------------------------------------
# `merge`
# --------------------------------------------------------------------------------------------

def _candidate_profile_rows(args: argparse.Namespace, primary: str) -> Optional[List[Dict[str, str]]]:
    """The full candidate data/pilot_profile.csv row set if --primary-name was given, else None
    (profile untouched -- no candidate rows to preview or write). Computed once and reused for
    both the before/after dry-run preview and the real --apply write, so the printed report can
    never show a different name/priorNames than what --apply actually persists.
    """
    if not args.primary_name:
        return None
    existing_rows = pilot_identity.read_profile_rows()
    existing_profile = next((r for r in existing_rows if r["pilot_id"] == primary), None)
    rows = [r for r in existing_rows if r["pilot_id"] != primary]
    rows.append({
        "pilot_id": primary,
        "display_name": args.primary_name,
        "x_handle": existing_profile["x_handle"] if existing_profile else "",
        "x_consent": existing_profile["x_consent"] if existing_profile else "",
        "x_confirmed_on": existing_profile["x_confirmed_on"] if existing_profile else "",
        "profile_hidden": existing_profile["profile_hidden"] if existing_profile else "",
        "note": args.note or (existing_profile["note"] if existing_profile else ""),
    })
    return rows

def merge_command(args: argparse.Namespace) -> int:
    outputs_base = Path(args.outputs_base)
    league_dir = outputs_base / "league"
    docs_data_dir = Path(args.docs_dir) / "data"
    data_dir = Path(args.data_dir)
    real_identity_path = data_dir / "pilot_identity.csv"
    real_profile_path = data_dir / "pilot_profile.csv"
    challenge_hist_csv, premier_hist_csv = _history_paths(outputs_base, args.format_name)

    primary = str(args.primary).strip()
    aliases = [str(a).strip() for a in args.aliases]
    today = date.today().isoformat()

    existing_rows = pilot_identity.read_identity_rows()
    existing_by_lid = {r["loginid"]: r for r in existing_rows}

    new_rows: List[Dict[str, str]] = []
    errors: List[str] = []

    confirmed_on = args.confirmed_on or today

    primary_row = existing_by_lid.get(primary)
    if primary_row is None:
        # No evidence_type/confirmed_on on a primary-only row -- there is no alias, nothing was
        # confirmed, see identity.IDENTITY_COLS.
        new_rows.append({
            "loginid": primary, "pilot_id": primary, "role": "primary",
            "added_on": today, "source": args.source, "evidence": args.evidence,
            "evidence_type": "", "confirmed_on": "", "note": args.note or "",
        })
    elif primary_row["role"] != "primary":
        errors.append(
            f"--primary {primary} is already recorded with role={primary_row['role']!r} "
            f"(pilot_id={primary_row['pilot_id']}) -- cannot use it as a merge target directly."
        )

    skipped: List[str] = []
    for alias in aliases:
        if alias == primary:
            errors.append(f"--alias {alias} is the same as --primary; nothing to merge.")
            continue
        existing = existing_by_lid.get(alias)
        if existing is None:
            new_rows.append({
                "loginid": alias, "pilot_id": primary, "role": "alias",
                "added_on": today, "source": args.source, "evidence": args.evidence,
                "evidence_type": args.evidence_type or "", "confirmed_on": confirmed_on,
                "note": args.note or "",
            })
        elif existing["role"] == "alias" and existing["pilot_id"] == primary:
            skipped.append(alias)
        else:
            errors.append(
                f"--alias {alias} is already recorded as role={existing['role']!r} "
                f"pointing at pilot_id={existing['pilot_id']} -- refusing to silently re-parent it."
            )

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1

    if not new_rows:
        print("Nothing to do -- every requested loginid is already recorded exactly as requested.")
        if skipped:
            print(f"Already merged: {skipped}")
        return 0

    combined_rows = existing_rows + new_rows

    print("Planned data/pilot_identity.csv changes:")
    for row in new_rows:
        print(f"  loginid {row['loginid']} -> pilot_id {row['pilot_id']} (role={row['role']})")
    if skipped:
        print(f"Already recorded (no-op): {skipped}")

    print("\n=== Validation (a)-(c): pilot_identity.csv structure ===")
    problems_abc = pilot_identity.validate_identity_rows(combined_rows)
    if problems_abc:
        print("FAIL")
        for p in problems_abc:
            print(f"  {p}")
        print("\nNothing written.")
        return 1
    print("PASS")

    as_of = date.today()

    # ---- BEFORE: real, on-disk identity ----
    pilot_identity.reset_cache()
    before_tables = _compute_season_tables(league_dir, as_of)
    before_challenge_hist = load_challenge_history(challenge_hist_csv)
    before_premier_hist = load_challenge_history(premier_hist_csv)
    before_league_results = load_all_league_results(league_dir / "results")
    before_challenge_sums = _bestpilots_sums(before_challenge_hist)
    before_premier_sums = _bestpilots_sums(before_premier_hist)

    # ---- AFTER: candidate identity (+ candidate profile, if --primary-name was given), via temp
    # files so the exact same production code paths (build_season_table -> aggregate_pilot_table
    # -> _identity_key -> identity.resolve/display_name, and league_site_export's _name_history /
    # priorNames) run unmodified against them, instead of a parallel reimplementation. The profile
    # override matters just as much as the identity one: without it, this preview would show
    # whatever "latest name wins" produces, not what --apply is actually about to write. ----
    candidate_profile_rows = _candidate_profile_rows(args, primary)
    with tempfile.TemporaryDirectory(prefix="pilot_identity_dryrun_") as tmp:
        temp_identity_csv = Path(tmp) / "pilot_identity.csv"
        pilot_identity.write_identity_rows(combined_rows, path=temp_identity_csv, backup=False)
        pilot_identity.set_identity_csv_path(temp_identity_csv)
        if candidate_profile_rows is not None:
            temp_profile_csv = Path(tmp) / "pilot_profile.csv"
            pilot_identity.write_profile_rows(candidate_profile_rows, path=temp_profile_csv, backup=False)
            pilot_identity.set_profile_csv_path(temp_profile_csv)
        try:
            after_tables = _compute_season_tables(league_dir, as_of)
            after_challenge_sums = _bestpilots_sums(before_challenge_hist)
            after_premier_sums = _bestpilots_sums(before_premier_hist)
            collisions = (
                _event_collisions(before_league_results, league_identity_key)
                + _event_collisions(before_challenge_hist, challenge_identity_key)
                + _event_collisions(before_premier_hist, challenge_identity_key)
            )
        finally:
            pilot_identity.set_identity_csv_path(real_identity_path)
            pilot_identity.set_profile_csv_path(real_profile_path)

    print("\n=== Validation (d): points/trophies/Top8/Top32 totals conserved before vs. after ===")
    problems_d = []
    for season in sorted(set(before_tables) | set(after_tables)):
        b, a = _season_sums(before_tables.get(season, pd.DataFrame())), _season_sums(after_tables.get(season, pd.DataFrame()))
        if b != a:
            problems_d.append(f"season {season}: before={b} after={a}")
    for label, b, a in (
        ("challenge_history (Wins/Top8/Top32)", before_challenge_sums, after_challenge_sums),
        ("premier_history (Wins/Top8/Top32)", before_premier_sums, after_premier_sums),
    ):
        if b != a:
            problems_d.append(f"{label}: before={b} after={a}")
    if problems_d:
        print("FAIL")
        for p in problems_d:
            print(f"  {p}")
    else:
        print("PASS")

    print("\n=== Validation (e): no pilot_id has two results in the same EventID (full history) ===")
    if collisions:
        print("FAIL")
        for c in collisions:
            print(f"  EventID {c['EventID']}: pilot_id {c['pilot_id']} <- loginids {c['loginids']}")
    else:
        print("PASS")

    _print_leaderboard_diff(before_tables, after_tables)
    _print_row_diffs(before_tables, after_tables)

    if problems_d or collisions:
        print("\nOne or more validations failed -- nothing written.")
        return 1

    if not args.apply:
        print("\nDry run only -- nothing written. Re-run with --apply to write these changes.")
        return 0

    # ---- APPLY ----
    # candidate_profile_rows was already computed above (before the AFTER preview) from the same
    # real on-disk pilot_profile.csv this write now targets -- identical to what the preview just
    # showed, not a second, potentially-diverging computation.
    profile_backup = None
    if candidate_profile_rows is not None:
        profile_backup = pilot_identity.write_profile_rows(candidate_profile_rows)

    identity_backup = pilot_identity.write_identity_rows(combined_rows)

    ts = datetime.now().isoformat(timespec="seconds")
    log_rows = [
        {
            "timestamp": ts,
            "action": "primary_added" if row["role"] == "primary" else "alias_added",
            "pilot_id": row["pilot_id"],
            "loginid": row["loginid"],
            "source": args.source,
            "evidence": args.evidence,
            "operator_note": args.note or "",
        }
        for row in new_rows
    ]
    pilot_identity.append_merge_log_rows(log_rows)

    for season, s, e in _season_config_rows(league_dir):
        table = build_season_table(league_dir / "results", s, e, as_of=as_of, prevrank_cutoff_days=7)
        path = write_season_league_csv(league_dir, season, table)
        print(f"[apply] rewrote {path} ({len(table)} pilot row(s))")

    written_seasons = export_league_site(league_dir, docs_data_dir, as_of=as_of, log=print)
    print(f"[apply] rewrote docs/data/*.json for: {written_seasons}")

    print("\n=== APPLIED ===")
    print(f"data/pilot_identity.csv: {len(pilot_identity.read_identity_rows())} row(s) (backup: {identity_backup})")
    print(
        f"data/pilot_profile.csv: {len(pilot_identity.read_profile_rows())} row(s)"
        + (f" (backup: {profile_backup})" if profile_backup else "")
    )
    print(f"data/pilot_merge_log.csv: {len(pilot_identity.read_merge_log_rows())} row(s)")
    return 0


# --------------------------------------------------------------------------------------------
# `--show`
# --------------------------------------------------------------------------------------------

def show_command(args: argparse.Namespace) -> int:
    query = str(args.show).strip()
    outputs_base = Path(args.outputs_base)
    challenge_hist_csv, premier_hist_csv = _history_paths(outputs_base, args.format_name)
    challenge_hist = load_challenge_history(challenge_hist_csv)
    premier_hist = load_challenge_history(premier_hist_csv)
    league_results = load_all_league_results(outputs_base / "league" / "results")
    frames = [challenge_hist, premier_hist, league_results]

    identity_map = pilot_identity.load_identity()

    candidate_pilot_ids = set()
    if query in identity_map:
        candidate_pilot_ids.add(pilot_identity.resolve(query, identity_map))
    else:
        seen_loginids = set()
        for df in frames:
            if "LoginID" in df.columns:
                seen_loginids |= set(df["LoginID"].astype(str).str.strip())
        if query in seen_loginids:
            candidate_pilot_ids.add(pilot_identity.resolve(query, identity_map))

    if not candidate_pilot_ids:
        q_lower = query.lower()
        matched_loginids = set()
        for df in frames:
            if "Pilot" not in df.columns or "LoginID" not in df.columns:
                continue
            mask = df["Pilot"].astype(str).str.strip().str.lower() == q_lower
            matched_loginids |= set(df.loc[mask, "LoginID"].astype(str).str.strip())
        matched_loginids.discard("")
        for lid in matched_loginids:
            candidate_pilot_ids.add(pilot_identity.resolve(lid, identity_map))

    if not candidate_pilot_ids:
        print(f"No pilot found matching {query!r} (checked loginids and historical display names).")
        return 1
    if len(candidate_pilot_ids) > 1:
        print(f"{query!r} matches more than one identity -- re-run with a specific loginid:")
        for pid in sorted(candidate_pilot_ids):
            print(f"  pilot_id {pid}")
        return 1

    pilot_id = next(iter(candidate_pilot_ids))
    identity_rows = pilot_identity.read_identity_rows()
    linked_loginids = sorted({pilot_id} | {r["loginid"] for r in identity_rows if r["pilot_id"] == pilot_id})

    candidate_names: List[str] = []
    for df in frames:
        if "Pilot" not in df.columns or "LoginID" not in df.columns:
            continue
        mask = df["LoginID"].astype(str).str.strip().isin(linked_loginids)
        candidate_names.extend(df.loc[mask, "Pilot"].astype(str).str.strip().tolist())
    names = pilot_identity.all_names(pilot_id, candidate_names)
    prof = pilot_identity.profile(pilot_id)

    print(f"pilot_id: {pilot_id}")
    print(f"linked loginid(s): {linked_loginids}")
    print(f"historical display name(s): {names}")
    if prof:
        x_visible = pilot_identity.is_x_handle_visible(pilot_id)
        print(
            f"profile: display_name={prof.get('display_name') or '(none)'} "
            f"x_handle={('@' + prof['x_handle']) if x_visible else '(not shown -- no consent or no handle)'} "
            f"profile_hidden={prof.get('profile_hidden') or 'false'}"
        )
    else:
        print("profile: (no data/pilot_profile.csv row)")
    return 0


# --------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show", metavar="LOGINID_OR_NAME", default=None)
    parser.add_argument("--format", dest="format_name", default="Modern")
    parser.add_argument("--outputs-base", default=str(REPO_ROOT / "outputs"))
    parser.add_argument("--docs-dir", default=str(REPO_ROOT / "docs"))
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))

    subparsers = parser.add_subparsers(dest="command")
    merge_parser = subparsers.add_parser("merge", help="Merge one or more alias loginids into a primary pilot identity.")
    merge_parser.add_argument("--primary", required=True)
    merge_parser.add_argument("--alias", action="append", required=True, dest="aliases")
    merge_parser.add_argument("--source", required=True)
    merge_parser.add_argument("--evidence", required=True)
    merge_parser.add_argument(
        "--evidence-type", default="",
        help="Short classification of --evidence's freeform text, e.g. 'direct_confirmation' "
             "(the player themselves confirmed it) vs. 'admin_decision'. Documentation only -- "
             "not validated. Applies to alias rows only (a primary-only row has nothing to confirm).",
    )
    merge_parser.add_argument(
        "--confirmed-on", default=None,
        help="ISO date the confirmation actually happened (e.g. the date of a DM), if different "
             "from today -- defaults to today (same default as added_on) when not given. "
             "Applies to alias rows only.",
    )
    merge_parser.add_argument("--primary-name", default=None)
    merge_parser.add_argument("--note", default="")
    merge_parser.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)
    pilot_identity.set_data_dir(Path(args.data_dir))

    if args.show:
        return show_command(args)
    if args.command == "merge":
        return merge_command(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
