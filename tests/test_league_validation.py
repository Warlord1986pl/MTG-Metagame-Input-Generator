"""Offline, deterministic tests for league_engine.validate_league, the tie-break rule
(_apply_tie_break_sort / _rank_table), the weekly-snapshot delta machinery, the
overwritable-current-week/frozen-earlier-week snapshot rule, LeagueBlockingError, and the
metagame_input_generator.py exit-code wiring that reacts to it.

No live network access, no dependency on outputs/league/pilot_league_Summer_2026.csv (that file is
gitignored and mutates every production run -- see CLAUDE.md). The exact-numbers test instead reads
a frozen copy of it, tests/fixtures/league/pilot_league_validation_fixture.csv, captured on
2026-08-31 with the CURRENT tie-break rule (Points, Points/Starts, Wins, Top2, Top4, Top8, Top16,
Starts -- see _rank_table's docstring) specifically so this test keeps passing regardless of how
the live season file grows afterward.

Run directly: python tests/test_league_validation.py
Or via pytest (if installed): pytest tests/test_league_validation.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date
from fractions import Fraction
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd  # noqa: E402

import league_engine as le  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "league" / "pilot_league_validation_fixture.csv"

N_EVENTS = 89
N_PREMIER_EVENTS = 4

# Only quantities confirmed to be STABLE (independent of which prior snapshot/table the current
# one happens to be diffed against) are asserted here -- see the discussion that established this:
# a "how many Rank values changed vs. the old rule" count is NOT stable (it depends on the old
# rule's leftover tie-break order among fully-tied rows, itself dependent on input order), so it is
# never asserted, only logged informationally by _remigrate_snapshot_ranks.
EXPECTED = {
    "rows": 1025,
    "unique_loginid": 1025,
    "sum_starts": 2848,
    "sum_top16": 1424,
    "sum_top8": 712,
    "sum_top4": 356,
    "sum_top2": 178,
    "sum_wins": 89,
    "sum_premier_points": 248,
    "sum_points": 2883,
    "pilots_with_premier": 63,
    "empty_prevrank": 96,
    "tie_groups": 68,
    "pilots_in_tie_groups": 866,
    "tie_groups_top100_pilots": 6,
}

EXPECTED_TOP5 = [
    # Pilot, Points, PremierPoints, Wins, Top2, Top4, Top8, Top16, Starts
    ("MeninoNey", 54, 0, 3, 7, 9, 14, 21, 35),
    ("Lightspirit", 34, 0, 2, 3, 6, 7, 16, 21),
    ("eclipse4343", 34, 0, 4, 4, 5, 6, 15, 23),
    ("Kritik", 31, 0, 3, 4, 5, 8, 11, 15),
    ("yriel", 30, 6, 1, 2, 4, 6, 14, 23),
]


def _load_fixture() -> pd.DataFrame:
    return pd.read_csv(FIXTURE)


def _full_key(row) -> tuple:
    points, starts = int(row["Points"]), int(row["Starts"])
    return (
        points, Fraction(points, starts), int(row["Wins"]), int(row["Top2"]),
        int(row["Top4"]), int(row["Top8"]), int(row["Top16"]), starts,
    )


# --------------------------------------------------------------------------------------------
# 1. Full run against the frozen fixture: stable numbers only, top-5 order, validate_league clean.
# --------------------------------------------------------------------------------------------

def test_full_run_validates_and_matches_expected_numbers() -> None:
    df = _load_fixture()

    actual = {
        "rows": len(df),
        "unique_loginid": int(df["LoginID"].nunique()),
        "sum_starts": int(df["Starts"].sum()),
        "sum_top16": int(df["Top16"].sum()),
        "sum_top8": int(df["Top8"].sum()),
        "sum_top4": int(df["Top4"].sum()),
        "sum_top2": int(df["Top2"].sum()),
        "sum_wins": int(df["Wins"].sum()),
        "sum_premier_points": int(df["PremierPoints"].sum()),
        "sum_points": int(df["Points"].sum()),
        "pilots_with_premier": int((df["PremierPoints"] > 0).sum()),
        "empty_prevrank": int(df["PrevRank"].isna().sum()),
    }

    groups: dict = {}
    for _, row in df.iterrows():
        groups.setdefault(_full_key(row), []).append(int(row["Rank"]))
    tie_groups = {k: v for k, v in groups.items() if len(v) > 1}
    actual["tie_groups"] = len(tie_groups)
    actual["pilots_in_tie_groups"] = sum(len(v) for v in tie_groups.values())
    actual["tie_groups_top100_pilots"] = sum(1 for v in tie_groups.values() for r in v if r <= 100)

    mismatches = {k: (actual[k], EXPECTED[k]) for k in EXPECTED if actual[k] != EXPECTED[k]}
    assert not mismatches, f"fixture numbers drifted from the checkpoint table: {mismatches}"

    ranks = sorted(int(r) for r in df["Rank"])
    assert ranks == list(range(1, len(df) + 1)), "Rank must be exactly 1..N with no gaps/duplicates"

    top5 = df.head(5)
    actual_top5 = [
        (r["Pilot"], int(r["Points"]), int(r["PremierPoints"]), int(r["Wins"]), int(r["Top2"]),
         int(r["Top4"]), int(r["Top8"]), int(r["Top16"]), int(r["Starts"]))
        for _, r in top5.iterrows()
    ]
    assert actual_top5 == EXPECTED_TOP5, f"top-5 order/values changed: {actual_top5}"

    le.validate_league(df, N_EVENTS, N_PREMIER_EVENTS)


# --------------------------------------------------------------------------------------------
# 2. A corrupted Points value must raise, and the message must name the offending LoginID.
# --------------------------------------------------------------------------------------------

def test_corrupted_points_row_raises_with_loginid() -> None:
    df = _load_fixture()
    rows = df.to_dict("records")
    target = rows[17]
    bad_lid = str(target["LoginID"])
    target["Points"] = target["Points"] + 1

    try:
        le.validate_league(rows, N_EVENTS, N_PREMIER_EVENTS)
    except AssertionError as exc:
        assert bad_lid in str(exc), f"AssertionError message must name LoginID {bad_lid}: {exc}"
    else:
        raise AssertionError("expected AssertionError for a corrupted Points row, got none")


# --------------------------------------------------------------------------------------------
# 3. Determinism: two consecutive builds from identical results/ input produce byte-identical CSVs.
# --------------------------------------------------------------------------------------------

def test_two_consecutive_builds_are_byte_identical() -> None:
    results_dir = REPO_ROOT / "outputs" / "league" / "results"
    if not results_dir.exists() or not any(results_dir.glob("*.csv")):
        print("  (no outputs/league/results on disk -- skipping determinism test)")
        return

    tmp = Path(tempfile.mkdtemp(prefix="league_validation_determinism_"))
    try:
        results_a, results_b = tmp / "results_a", tmp / "results_b"
        shutil.copytree(results_dir, results_a)
        shutil.copytree(results_dir, results_b)

        season_start, season_end, as_of = date(2026, 6, 1), date(2026, 8, 31), date(2026, 8, 31)

        table_a = le.build_season_table(results_a, season_start, season_end, as_of=as_of, snapshot_dir=None)
        table_b = le.build_season_table(results_b, season_start, season_end, as_of=as_of, snapshot_dir=None)

        path_a = le.write_season_league_csv(tmp / "run_a", "Summer 2026", table_a)
        path_b = le.write_season_league_csv(tmp / "run_b", "Summer 2026", table_b)

        assert path_a.read_bytes() == path_b.read_bytes(), (
            "two consecutive builds from identical input must be byte-identical"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# 4. Tie-break input-order independence, on synthetic rows with no full 8-key ties. This is the
#    test that actually guards what the new rule fixes: the OLD 3-key rule (Points, Wins, Top2)
#    would leave rows tied on those three (but differing on Top4+) dependent on input order; the
#    new 8-key rule must not, since every one of the 8 keys is now taken into account.
# --------------------------------------------------------------------------------------------

def test_rank_independent_of_input_row_order() -> None:
    rows = [
        {"Pilot": "P1", "LoginID": "1", "Points": 20, "PremierPoints": 0, "Wins": 2, "Top2": 3, "Top4": 4, "Top8": 5, "Top16": 6, "Starts": 10},
        {"Pilot": "P2", "LoginID": "2", "Points": 20, "PremierPoints": 0, "Wins": 2, "Top2": 3, "Top4": 5, "Top8": 5, "Top16": 6, "Starts": 10},
        {"Pilot": "P3", "LoginID": "3", "Points": 15, "PremierPoints": 0, "Wins": 1, "Top2": 2, "Top4": 3, "Top8": 4, "Top16": 5, "Starts": 8},
        {"Pilot": "P4", "LoginID": "4", "Points": 15, "PremierPoints": 0, "Wins": 1, "Top2": 2, "Top4": 3, "Top8": 4, "Top16": 6, "Starts": 8},
        {"Pilot": "P5", "LoginID": "5", "Points": 10, "PremierPoints": 0, "Wins": 1, "Top2": 1, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 5},
        {"Pilot": "P6", "LoginID": "6", "Points": 10, "PremierPoints": 0, "Wins": 1, "Top2": 1, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 4},
    ]
    # sanity: no two rows here are tied on all 8 keys (P1/P2 differ on Top4, P3/P4 differ on
    # Top16, P5/P6 differ on Starts) -- so input order must never affect the result.
    keys = [_full_key(r) for r in rows]
    assert len(set(keys)) == len(keys), "test fixture rows must have no full 8-key ties"

    normal = le._apply_tie_break_sort(pd.DataFrame(rows))
    reversed_ = le._apply_tie_break_sort(pd.DataFrame(list(reversed(rows))))

    normal_order = list(normal["LoginID"])
    reversed_order = list(reversed_["LoginID"])
    assert normal_order == reversed_order, (
        f"reversing input row order changed the tie-broken order: {normal_order} vs {reversed_order}"
    )


# --------------------------------------------------------------------------------------------
# 5. Starts >= 1 is enforced with the offending LoginID in the message.
# --------------------------------------------------------------------------------------------

def test_zero_starts_raises_with_loginid() -> None:
    rows = [{"Pilot": "P1", "LoginID": "999", "Points": 5, "PremierPoints": 0,
             "Wins": 0, "Top2": 0, "Top4": 0, "Top8": 1, "Top16": 1, "Starts": 0}]
    try:
        le._apply_tie_break_sort(pd.DataFrame(rows))
    except AssertionError as exc:
        assert "999" in str(exc)
    else:
        raise AssertionError("expected AssertionError for Starts=0")


# --------------------------------------------------------------------------------------------
# 6. Snapshot machinery: current-week overwrite, earlier-week frozen, blank-not-zero deltas.
# --------------------------------------------------------------------------------------------

def test_snapshot_overwrite_current_week_frozen_earlier_week() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="league_snapshot_test_"))
    try:
        snapshot_dir = tmp / "snapshots"
        t1 = pd.DataFrame([{"Rank": 1, "Pilot": "Alice", "LoginID": "111", "Points": 10, "PremierPoints": 0,
                             "Wins": 1, "Top2": 1, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 2}])

        # Wednesday run: as_of and today both in ISO week 32.
        wed = date(2026, 8, 3)
        p1 = le.write_weekly_snapshot(snapshot_dir, "Summer 2026", wed, t1, today=wed)
        assert p1 is not None and p1.exists()

        # Friday run, same week: overwritable.
        fri = date(2026, 8, 7)
        t2 = pd.DataFrame([{"Rank": 1, "Pilot": "Alice", "LoginID": "111", "Points": 20, "PremierPoints": 0,
                             "Wins": 2, "Top2": 2, "Top4": 2, "Top8": 2, "Top16": 2, "Starts": 4}])
        p2 = le.write_weekly_snapshot(snapshot_dir, "Summer 2026", fri, t2, today=fri)
        assert p2 == p1
        assert int(pd.read_csv(p2)["Points"].iloc[0]) == 20, "same-week rewrite must overwrite"

        # A later run whose as_of still targets week 32 but whose real "today" has moved into week
        # 33 must be refused -- week 32 is now an earlier, frozen week.
        t3 = pd.DataFrame([{"Rank": 1, "Pilot": "Alice", "LoginID": "111", "Points": 30, "PremierPoints": 0,
                             "Wins": 3, "Top2": 3, "Top4": 3, "Top8": 3, "Top16": 3, "Starts": 6}])
        try:
            le.write_weekly_snapshot(snapshot_dir, "Summer 2026", fri, t3, today=date(2026, 8, 10))
        except FileExistsError:
            pass
        else:
            raise AssertionError("writing an earlier (frozen) week's snapshot must raise FileExistsError")

        base, base_week = le._load_latest_snapshot_before(snapshot_dir, "Summer 2026", le._iso_week(date(2026, 8, 17)))
        assert base is not None and base_week == 32
        assert int(base.loc["111"]["Points"]) == 20, "delta base must be the overwritten (Friday) snapshot"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_debutant_gets_blank_not_zero_deltas() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="league_snapshot_debutant_"))
    try:
        snapshot_dir = tmp / "snapshots"
        week1 = date(2026, 8, 3)
        table_week1 = pd.DataFrame([{"Rank": 1, "Pilot": "Alice", "LoginID": "111", "Points": 10, "PremierPoints": 0,
                                      "Wins": 1, "Top2": 1, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 2}])
        le.write_weekly_snapshot(snapshot_dir, "Summer 2026", week1, table_week1, today=week1)

        base, _ = le._load_latest_snapshot_before(snapshot_dir, "Summer 2026", le._iso_week(date(2026, 8, 10)))
        table_week2 = pd.DataFrame([
            {"Rank": 1, "Pilot": "Alice", "LoginID": "111", "Points": 15, "PremierPoints": 0,
             "Wins": 1, "Top2": 2, "Top4": 2, "Top8": 2, "Top16": 2, "Starts": 3,
             "PrevRank": 1, "RankChange": 0},
            {"Rank": 2, "Pilot": "Carol", "LoginID": "333", "Points": 3, "PremierPoints": 0,
             "Wins": 0, "Top2": 0, "Top4": 0, "Top8": 0, "Top16": 1, "Starts": 1,
             "PrevRank": pd.NA, "RankChange": pd.NA},
        ])
        merged = le._apply_deltas(table_week2, base)

        alice = merged[merged["LoginID"] == "111"].iloc[0]
        assert int(alice["PrevPoints"]) == 10 and int(alice["DPoints"]) == 5

        carol = merged[merged["LoginID"] == "333"].iloc[0]
        assert pd.isna(carol["PrevPoints"]) and pd.isna(carol["DPoints"]), (
            "a debutant (absent from the snapshot) must have blank Prev*/D*, never zero"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# 7. _remigrate_snapshot_ranks: recomputes Rank in place, is idempotent, reports "no snapshots".
# --------------------------------------------------------------------------------------------

def test_remigrate_snapshot_ranks() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="league_remigrate_"))
    try:
        snapshot_dir = tmp / "snapshots"
        result_empty = le._remigrate_snapshot_ranks(snapshot_dir)
        assert result_empty == {"snapshots_migrated": 0, "rows_changed": 0}

        table = pd.DataFrame([
            {"Rank": 1, "Pilot": "A", "LoginID": "1", "Points": 10, "PremierPoints": 0, "Wins": 1, "Top2": 1, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 5},
            {"Rank": 2, "Pilot": "B", "LoginID": "2", "Points": 8, "PremierPoints": 0, "Wins": 0, "Top2": 1, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 3},
            {"Rank": 3, "Pilot": "C", "LoginID": "3", "Points": 6, "PremierPoints": 0, "Wins": 0, "Top2": 0, "Top4": 1, "Top8": 1, "Top16": 1, "Starts": 2},
        ])
        p = le.write_weekly_snapshot(snapshot_dir, "Summer 2026", date(2026, 8, 3), table, today=date(2026, 8, 3))
        # corrupt Rank to simulate a stale, pre-migration snapshot
        df = pd.read_csv(p)
        df["Rank"] = [3, 1, 2]
        df.to_csv(p, index=False)

        res = le._remigrate_snapshot_ranks(snapshot_dir)
        assert res["snapshots_migrated"] == 1
        assert res["rows_changed"] > 0

        migrated = pd.read_csv(p).sort_values("Rank")
        assert list(migrated["LoginID"]) == [1, 2, 3], "Rank must follow the current tie-break rule after migration"

        res2 = le._remigrate_snapshot_ranks(snapshot_dir)
        assert res2 == {"snapshots_migrated": 1, "rows_changed": 0}, "re-running the migration must be idempotent"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# 8. LeagueBlockingError: a frozen-week snapshot write failure inside run_league_update raises it
#    (not a plain FileExistsError/AssertionError), so the caller can distinguish it from every
#    other league/Challenge failure.
# --------------------------------------------------------------------------------------------

def test_run_league_update_raises_league_blocking_error_on_frozen_week() -> None:
    history_csv = REPO_ROOT / "outputs" / "challenge_history_modern.csv"
    premier_csv = REPO_ROOT / "outputs" / "premier_history_modern.csv"
    results_dir = REPO_ROOT / "outputs" / "league" / "results"
    if not results_dir.exists() or not history_csv.exists():
        print("  (no outputs/league/results or challenge_history_modern.csv on disk -- skipping)")
        return

    tmp = Path(tempfile.mkdtemp(prefix="league_blocking_error_"))
    try:
        league_dir = tmp / "league"
        shutil.copytree(results_dir, league_dir / "results")

        wed = date(2026, 8, 3)
        le.run_league_update(
            history_csv=history_csv, league_dir=league_dir, format_name="Modern",
            start_date=date(2026, 6, 1), end_date=wed, as_of=wed,
            premier_history_csv=premier_csv if premier_csv.exists() else None,
            today=wed, log=None,
        )

        try:
            le.run_league_update(
                history_csv=history_csv, league_dir=league_dir, format_name="Modern",
                start_date=date(2026, 6, 1), end_date=wed, as_of=wed,
                premier_history_csv=premier_csv if premier_csv.exists() else None,
                today=date(2026, 8, 10), log=None,
            )
        except le.LeagueBlockingError:
            pass
        else:
            raise AssertionError("expected LeagueBlockingError for a frozen-week snapshot rewrite")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# 9. metagame_input_generator wiring: _log_blocking_summary prints "BLOCKING: ..." lines, and
#    main() exits with a non-zero code when any GenerationRunResult carries blocking_failures.
# --------------------------------------------------------------------------------------------

def test_log_blocking_summary_and_main_exit_code() -> None:
    import metagame_input_generator as mig

    fake_result = mig.GenerationRunResult(
        week_start=date(2026, 1, 1), week_end=date(2026, 1, 7), run_dir=Path("."),
        row_count=0, mapped_from_user=0, primary_count=0, fallback_count=0,
        imputed_count=0, rogue_threshold=0.5,
        blocking_failures=["league snapshot not written for week 32 (Summer 2026): boom -- next week's deltas will be empty"],
    )
    clean_result = mig.GenerationRunResult(
        week_start=date(2026, 1, 8), week_end=date(2026, 1, 14), run_dir=Path("."),
        row_count=0, mapped_from_user=0, primary_count=0, fallback_count=0,
        imputed_count=0, rogue_threshold=0.5,
    )

    logged: list = []
    had_blocking = mig._log_blocking_summary([clean_result], log=logged.append)
    assert had_blocking is False
    assert not any("BLOCKING" in line for line in logged)

    logged2: list = []
    had_blocking2 = mig._log_blocking_summary([fake_result, clean_result], log=logged2.append)
    assert had_blocking2 is True
    blocking_lines = [line for line in logged2 if "BLOCKING" in line]
    assert blocking_lines, "expected at least one line containing BLOCKING"
    assert "boom" in blocking_lines[0]

    original_run_generation = mig.run_generation
    original_parse_args = mig.parse_args
    original_argv = sys.argv
    mig.run_generation = lambda args: [fake_result]
    mig.parse_args = lambda: object()
    sys.argv = ["prog", "--dummy"]
    try:
        try:
            mig.main()
        except SystemExit as exc:
            assert exc.code not in (0, None), f"expected a non-zero exit code, got {exc.code!r}"
        else:
            raise AssertionError("expected SystemExit when a result carries blocking_failures")
    finally:
        mig.run_generation = original_run_generation
        mig.parse_args = original_parse_args
        sys.argv = original_argv


if __name__ == "__main__":
    test_full_run_validates_and_matches_expected_numbers()
    print("OK: test_full_run_validates_and_matches_expected_numbers")
    test_corrupted_points_row_raises_with_loginid()
    print("OK: test_corrupted_points_row_raises_with_loginid")
    test_two_consecutive_builds_are_byte_identical()
    print("OK: test_two_consecutive_builds_are_byte_identical")
    test_rank_independent_of_input_row_order()
    print("OK: test_rank_independent_of_input_row_order")
    test_zero_starts_raises_with_loginid()
    print("OK: test_zero_starts_raises_with_loginid")
    test_snapshot_overwrite_current_week_frozen_earlier_week()
    print("OK: test_snapshot_overwrite_current_week_frozen_earlier_week")
    test_debutant_gets_blank_not_zero_deltas()
    print("OK: test_debutant_gets_blank_not_zero_deltas")
    test_remigrate_snapshot_ranks()
    print("OK: test_remigrate_snapshot_ranks")
    test_run_league_update_raises_league_blocking_error_on_frozen_week()
    print("OK: test_run_league_update_raises_league_blocking_error_on_frozen_week")
    test_log_blocking_summary_and_main_exit_code()
    print("OK: test_log_blocking_summary_and_main_exit_code")
    print("All league validation tests passed.")
