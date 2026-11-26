"""The k10 ranking ruleset (league_rules; Winter 2026/27 onward) and its per-season wiring.

The acceptance test ranks a frozen copy of the Autumn 2026 results export (events through
2026-11-17) with the k10 comparator, purely as a fixture: Autumn 2026 itself stays on the legacy
ruleset, and nothing here writes outside pytest's tmp_path.
"""
from __future__ import annotations

import random
import sys
import json
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import league_engine as le  # noqa: E402
import league_rules as lr  # noqa: E402
from league_rules import PilotStats, Ruleset, compare, competition_rank  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "k10" / "autumn_2026_results_through_2026-11-17.csv"
K10 = Ruleset(lr.K10, k=10)
WINTER_START, WINTER_END = date(2026, 12, 1), date(2027, 3, 1)
SPRING_START, SPRING_END = date(2027, 3, 2), date(2027, 5, 31)


def stats(score, points, placements, wins=None):
    """PilotStats with the bracket counts derived from *placements* (score given explicitly)."""
    pl = sorted(placements)
    return PilotStats(
        score=score, points=points, starts=len(pl),
        wins=sum(p == 1 for p in pl) if wins is None else wins,
        top2=sum(p <= 2 for p in pl), top4=sum(p <= 4 for p in pl), top8=sum(p <= 8 for p in pl),
        top16=sum(p <= 16 for p in pl), placements=tuple(pl),
    )


def result_rows(rows):
    """[(login_id, event_id, event_date, place, points, event_class), ...] -> results frame."""
    return pd.DataFrame([
        {"EventID": str(e), "EventDate": d, "Tier": "C32", "EventClass": c, "Pilot": f"p{lid}",
         "LoginID": str(lid), "Place": str(pl), "Deck": "", "DeckGuess": "", "LeaguePoints": str(pts),
         "SwissRank": "", "SwissPoints": "", "OMWP": "", "GWP": "", "OGWP": ""}
        for lid, e, d, pl, pts, c in rows
    ], columns=le.LEAGUE_RESULTS_COLS)


# --- per-season configuration ---------------------------------------------------------------

def test_rulesets_pinned_per_season():
    assert lr.ruleset_for_season("Summer 2026", date(2026, 6, 1)).name == lr.LEGACY
    assert lr.ruleset_for_season("Autumn 2026", date(2026, 9, 1)).name == lr.LEGACY
    assert lr.ruleset_for_season("Winter 2026/27", WINTER_START) == K10
    assert le.season_ruleset(date(2026, 9, 1)).name == lr.LEGACY
    assert le.season_ruleset(WINTER_START) == K10
    assert lr.ruleset_for_season("Spring 2027", SPRING_START) == K10
    assert le.season_ruleset(SPRING_START) == K10
    with pytest.raises(lr.UnconfiguredSeasonError):
        lr.ruleset_for_season("Summer 2027", date(2027, 6, 1))


# --- season calendar ----------------------------------------------------------------------------

def test_season_assignment_by_configured_dates():
    assert le.season_for_date(date(2026, 11, 30)) == ("Autumn 2026", date(2026, 9, 1), date(2026, 11, 30))
    assert le.season_for_date(date(2026, 12, 1)) == ("Winter 2026/27", WINTER_START, WINTER_END)
    assert le.season_for_date(date(2027, 3, 1)) == ("Winter 2026/27", WINTER_START, WINTER_END)
    assert le.season_for_date(date(2027, 3, 2)) == ("Spring 2027", SPRING_START, SPRING_END)
    assert le.season_for_date(date(2027, 5, 31)) == ("Spring 2027", SPRING_START, SPRING_END)
    assert le.season_for_date(date(2026, 6, 1))[0] == "Summer 2026"
    # Before the first configured season: meteorological quarters, as before.
    assert le.season_for_date(date(2026, 3, 15)) == ("Spring 2026", date(2026, 3, 1), date(2026, 5, 31))
    # Summer 2027 is not configured: the rebuild stops rather than guessing.
    with pytest.raises(lr.UnconfiguredSeasonError):
        le.season_for_date(date(2027, 6, 1))
    assert le.seasons_for_dates([date(2027, 3, 1), date(2027, 3, 2)]) == {
        "Winter 2026/27": (WINTER_START, WINTER_END), "Spring 2027": (SPRING_START, SPRING_END)}


def test_configured_k10_seasons_are_13_full_weeks():
    for x in lr.SEASONS:
        if x.ruleset.name == lr.K10:
            assert (x.end - x.start).days + 1 == 91, x


def _season(name, start, days, ruleset=K10):
    return lr.Season(name, start, start + timedelta(days=days - 1), ruleset)


@pytest.mark.parametrize("days", [90, 92])
def test_k10_season_that_is_not_whole_weeks_is_rejected(days):
    with pytest.raises(lr.SeasonConfigError, match="whole weeks"):
        lr.validate_seasons([_season("Winter 2026/27", WINTER_START, days)])


def test_k10_season_of_14_weeks_is_allowed():
    lr.validate_seasons([_season("Winter 2026/27", WINTER_START, 91), _season("Spring 2027", SPRING_START, 98)])


def test_overlapping_seasons_are_rejected():
    with pytest.raises(lr.SeasonConfigError, match="overlaps"):
        lr.validate_seasons([_season("A", WINTER_START, 91), _season("B", date(2027, 3, 1), 91)])


def test_gap_between_seasons_is_rejected():
    with pytest.raises(lr.SeasonConfigError, match="gap"):
        lr.validate_seasons([_season("A", WINTER_START, 91), _season("B", date(2027, 3, 3), 91)])


def test_season_config_csv_must_agree_with_the_calendar():
    lr.check_season_config_rows([("Autumn 2026", date(2026, 9, 1), date(2026, 11, 30))])
    with pytest.raises(lr.SeasonConfigError):
        lr.check_season_config_rows([("Winter 2026/27", WINTER_START, date(2027, 2, 28))])


# --- comparator ---------------------------------------------------------------------------------

def test_points_per_start_compared_as_exact_fractions():
    # Same score and bracket counts; 2/3 = 0.6667 vs 667/1000 = 0.667 both round to 0.67.
    a = stats(10, 2 * 10, [9] * 10 + [17] * 20)          # 20 points / 30 starts = 2/3
    b = stats(10, 667, [9] * 10 + [17] * 990)            # 667 points / 1000 starts
    assert round(a.points / a.starts, 2) == round(b.points / b.starts, 2)
    assert compare(b, a) == -1 and compare(a, b) == 1
    # Mathematically equal ratios (2/3 == 4/6) fall through to Starts: more starts ranks higher.
    c = stats(4, 2, [9, 9, 17])
    d = stats(4, 4, [9, 9, 17, 17, 17, 17])
    assert c.points * d.starts == d.points * c.starts
    assert compare(d, c) == -1


def test_premier_result_inside_and_outside_the_counted_k():
    rows = []
    # Pilot 1: 11 results -- a premier win (10) plus ten Challenge Top 16s (1 each): premier counted,
    # one 1-point result dropped.
    rows.append((1, 900, "2026-12-02", 1, 10, "Premier"))
    rows += [(1, 100 + i, "2026-12-03", 9, 1, "Challenge") for i in range(10)]
    # Pilot 2: ten Challenge wins (5 each) plus a premier 9th-16th (2): premier outside the 10 best.
    rows += [(2, 200 + i, "2026-12-04", 1, 5, "Challenge") for i in range(10)]
    rows.append((2, 901, "2026-12-05", 9, 2, "Premier"))
    df = le.keyed_results(result_rows(rows))
    st = lr.stats_by_key(df, "_Key", 10)
    assert st["id:1"].score == 10 + 9 and st["id:1"].points == 20 and st["id:1"].weakest_counted == 1
    assert st["id:2"].score == 50 and st["id:2"].points == 52 and st["id:2"].weakest_counted == 5
    counted = lr.counted_mask(df, "_Key", 10)
    by = df.assign(Counted=counted)
    assert by[(by.LoginID == "1") & (by.EventID == "900")].Counted.item()
    assert by[(by.LoginID == "1")].Counted.sum() == 10
    # Tie among equal 1-point results is broken by EventID ascending: the highest EventID drops.
    assert not by[(by.LoginID == "1") & (by.EventID == "109")].Counted.item()
    assert not by[(by.LoginID == "2") & (by.EventID == "901")].Counted.item()


def test_zero_point_pilots_decided_by_starts_then_placement_vector():
    more_starts = stats(0, 0, [17, 20, 30])
    fewer_starts = stats(0, 0, [17, 18])
    assert compare(more_starts, fewer_starts) == -1
    better_vector = stats(0, 0, [17, 25])
    worse_vector = stats(0, 0, [18, 19])
    assert compare(better_vector, worse_vector) == -1
    assert compare(stats(0, 0, [20, 17]), stats(0, 0, [17, 20])) == 0


def test_shared_rank_and_the_rank_after_it():
    st = {
        "id:50": stats(9, 9, [1, 9, 9, 9, 9]),
        "id:30": stats(5, 5, [1, 17]),
        "id:10": stats(5, 5, [1, 17]),
        "id:40": stats(4, 4, [2, 17]),
    }
    order = {k: lr.row_order_key(k[3:], "") for k in st}
    ranked = competition_rank(st, order)
    assert [(k, r, t) for k, r, t in ranked] == [
        ("id:50", 1, False), ("id:10", 2, True), ("id:30", 2, True), ("id:40", 4, False),
    ]
    # Row order inside the tie group is LoginID ascending whatever the input order.
    shuffled = dict(reversed(list(st.items())))
    assert competition_rank(shuffled, order) == ranked


def test_shared_weekly_win():
    rows = [
        # Week 1 (12-01..12-07): pilots 1 and 2 identical -> both win the week.
        (1, 101, "2026-12-01", 1, 5, "Challenge"), (2, 102, "2026-12-02", 1, 5, "Challenge"),
        (3, 101, "2026-12-01", 2, 4, "Challenge"),
        # Week 2 (12-08..12-14): pilot 3 alone.
        (3, 103, "2026-12-09", 1, 5, "Challenge"), (1, 103, "2026-12-09", 2, 4, "Challenge"),
        # Week 3 (12-15..12-21) is still running on as_of 2026-12-21.
        (2, 104, "2026-12-16", 1, 5, "Challenge"),
    ]
    df = le.keyed_results(result_rows(rows))
    order = {k: le._row_order_key_for(k) for k in df["_Key"].unique()}
    wins = lr.weekly_wins(df, "_Key", order, WINTER_START, WINTER_END, as_of=date(2026, 12, 21))
    assert wins == {"id:1": 1, "id:2": 1, "id:3": 1}
    assert [o.week for o in lr.weekly_outcomes(df, "_Key", order, WINTER_START, WINTER_END,
                                               as_of=date(2026, 12, 21))] == [1, 2]


def test_weekly_win_is_provisional_during_the_grace_period():
    rows = [(1, 101, "2026-12-03", 1, 5, "Challenge"), (2, 101, "2026-12-03", 2, 4, "Challenge")]
    df = le.keyed_results(result_rows(rows))
    order = {k: le._row_order_key_for(k) for k in df["_Key"].unique()}
    assert lr.WEEKLY_GRACE_DAYS == 3

    def outcome(as_of):
        return lr.weekly_outcomes(df, "_Key", order, WINTER_START, WINTER_END, as_of=as_of)

    # Week 1 ends 2026-12-07: running on 12-07, provisional 12-08..12-10, final from 12-11.
    assert outcome(date(2026, 12, 7)) == []
    for d in (8, 9, 10):
        (w1,) = outcome(date(2026, 12, d))
        assert (w1.week, w1.winners, w1.provisional) == (1, ("id:1",), True)
        assert lr.weekly_wins(df, "_Key", order, WINTER_START, WINTER_END, as_of=date(2026, 12, d)) == {}
    (w1,) = outcome(date(2026, 12, 11))
    assert not w1.provisional
    assert lr.weekly_wins(df, "_Key", order, WINTER_START, WINTER_END, as_of=date(2026, 12, 11)) == {"id:1": 1}


def test_weekly_windows_are_13_full_weeks():
    w = lr.weekly_windows(WINTER_START, WINTER_END)
    assert len(w) == 13
    assert w[0] == (1, date(2026, 12, 1), date(2026, 12, 7))
    assert w[1] == (2, date(2026, 12, 8), date(2026, 12, 14))
    assert w[-1] == (13, date(2027, 2, 23), date(2027, 3, 1))
    assert all((e - s).days == 6 for _n, s, e in w)
    sp = lr.weekly_windows(SPRING_START, SPRING_END)
    assert (sp[0], sp[-1], len(sp)) == ((1, date(2027, 3, 2), date(2027, 3, 8)), (13, date(2027, 5, 25), date(2027, 5, 31)), 13)
    with pytest.raises(lr.SeasonConfigError):
        lr.weekly_windows(WINTER_START, date(2027, 2, 28))


def test_week_13_grace_period_ends_2027_03_04():
    rows = [(1, 101, "2027-03-01", 1, 5, "Challenge"), (2, 101, "2027-03-01", 2, 4, "Challenge")]
    df = le.keyed_results(result_rows(rows))
    order = {k: le._row_order_key_for(k) for k in df["_Key"].unique()}

    def week13(as_of):
        return [o for o in lr.weekly_outcomes(df, "_Key", order, WINTER_START, WINTER_END, as_of=as_of)
                if o.week == 13]

    assert week13(date(2027, 3, 1)) == []  # still running
    for d in (2, 3, 4):
        (w,) = week13(date(2027, 3, d))
        assert (w.winners, w.provisional) == (("id:1",), True)
    (w,) = week13(date(2027, 3, 5))
    assert not w.provisional
    assert lr.weekly_wins(df, "_Key", order, WINTER_START, WINTER_END, as_of=date(2027, 3, 5)) == {"id:1": 1}


# --- pipeline: build_season_table / validate_league / determinism ---------------------------

def _winter_rows():
    rng = random.Random(7)
    rows = []
    eid = 1000
    for day in range(1, 29):
        d = date(2026, 12, day).isoformat()
        eid += 1
        pilots = rng.sample(range(1, 60), 32)
        for place, lid in enumerate(pilots, start=1):
            rows.append((lid, eid, d, place, le.league_points_for_place(place), "Challenge"))
    return rows


def _write_results(results_dir: Path, df: pd.DataFrame) -> None:
    for eid, grp in df.groupby("EventID"):
        le._write_csv_lf(grp, results_dir / f"{eid}.csv")


def test_k10_table_validates_and_is_deterministic(tmp_path):
    df = result_rows(_winter_rows())
    outs = []
    for seed in (None, 1, 2):
        d = tmp_path / f"run_{seed}"
        res = d / "results"
        res.mkdir(parents=True)
        _write_results(res, df if seed is None else df.sample(frac=1.0, random_state=seed))
        table = le.build_season_table(res, WINTER_START, WINTER_END, as_of=date(2026, 12, 29))
        assert table.attrs["ruleset"] == lr.K10
        assert list(table.columns) == le.PILOT_TABLE_COLS + le.K10_TABLE_COLS
        season_results = le.load_all_league_results(res)
        le.validate_league(table, season_results.EventID.nunique(), 0, len(season_results),
                           ruleset=K10, season_results=season_results)
        path = le.write_season_league_csv(d, "Winter 2026/27", table)
        outs.append(path.read_bytes())
    assert outs[0] == outs[1] == outs[2]
    t = pd.read_csv(path)
    assert (t.SeasonScore <= t.Points).all()
    assert (t.CountedResults == t.Starts.clip(upper=10)).all()
    assert (t.WeakestCounted.isna() == (t.Starts < 10)).all()
    # as_of 12-29: weeks 1-3 final, week 4 (ends 12-28) provisional and not counted.
    assert t.WeeklyWins.sum() >= 3
    assert [(w["week"], w["provisional"]) for w in table.attrs["weekly_outcomes"]] == [
        (1, False), (2, False), (3, False), (4, True)]
    assert all(w["winners"] for w in table.attrs["weekly_outcomes"])


def test_k10_validation_rejects_a_wrong_rank(tmp_path):
    res = tmp_path / "results"
    res.mkdir()
    _write_results(res, result_rows(_winter_rows()))
    table = le.build_season_table(res, WINTER_START, WINTER_END, as_of=date(2026, 12, 29))
    season_results = le.load_all_league_results(res)
    args = (season_results.EventID.nunique(), 0, len(season_results))
    bad = table.copy()
    bad.loc[1, "Rank"] = bad.loc[0, "Rank"]  # claim a tie the rule does not give
    with pytest.raises(AssertionError):
        le.validate_league(bad, *args, ruleset=K10, season_results=season_results)
    bad = table.copy()
    bad.loc[0, "SeasonScore"] = bad.loc[0, "SeasonScore"] + 1
    with pytest.raises(AssertionError):
        le.validate_league(bad, *args, ruleset=K10, season_results=season_results)


def test_legacy_season_table_has_no_k10_columns(tmp_path):
    res = tmp_path / "results"
    res.mkdir()
    rows = [(lid, eid, d.replace("2026-12-", "2026-10-"), pl, pts, c) for lid, eid, d, pl, pts, c in _winter_rows()]
    _write_results(res, result_rows(rows))
    table = le.build_season_table(res, date(2026, 9, 1), date(2026, 11, 30), as_of=date(2026, 11, 1))
    assert table.attrs["ruleset"] == lr.LEGACY
    assert list(table.columns) == le.PILOT_TABLE_COLS
    assert list(table.Rank) == list(range(1, len(table) + 1))


# --- acceptance: k10 on the frozen Autumn 2026 export -----------------------------------------

EXPECTED_TOP16 = [
    (1, "nahuel10", 47, 7, 11, 17, 23, 36, 94, 52),
    (2, "rastaf", 40, 4, 6, 12, 19, 30, 71, 39),
    (3, "Venom01", 36, 4, 5, 7, 16, 23, 55, 33),
    (4, "Mistakenn", 34, 3, 4, 7, 16, 24, 54, 29),
    (5, "xfile", 34, 2, 5, 7, 16, 25, 55, 40),
    (6, "MeninoNey", 34, 1, 4, 9, 16, 28, 58, 54),
    (7, "komattaman", 33, 2, 3, 8, 15, 23, 51, 33),
    (8, "Selfeisek", 33, 2, 3, 8, 10, 19, 42, 40),
    (9, "sioutis_kk", 33, 1, 4, 8, 12, 16, 41, 24),
    (10, "Bradykiesling13", 31, 3, 4, 7, 8, 9, 31, 10),
    (11, "RespectTheCat", 30, 2, 4, 6, 6, 9, 30, 11),
    (12, "O_danielakos", 28, 2, 3, 5, 5, 12, 30, 17),
    (13, "hugofreitas1", 28, 2, 2, 4, 15, 29, 52, 46),
    (14, "Fransadness", 28, 0, 1, 7, 10, 25, 43, 50),
    (15, "caquaa", 28, 0, 1, 3, 11, 13, 32, 22),
    (16, "reidq7", 27, 2, 3, 4, 8, 10, 27, 17),
]


def test_acceptance_k10_on_frozen_autumn_export():
    raw = pd.read_csv(FIXTURE, dtype=str, keep_default_na=False)
    merges = {"3263693": "2903591"}
    results = pd.DataFrame({
        "EventID": raw.EventID, "EventDate": raw.EventDate, "Tier": "",
        "EventClass": raw.EventType.map({"challenge": "Challenge", "premier": "Premier"}),
        "Pilot": raw.Name, "LoginID": raw.LoginID.map(lambda x: merges.get(x, x)),
        "Place": raw.Placement, "LeaguePoints": raw.PointsAwarded,
    })
    agg = le.aggregate_pilot_table(results)
    table = le._rank_table_k10(agg, results, K10, date(2026, 9, 1), date(2026, 11, 30), as_of=date(2026, 11, 18))

    got = [
        (int(r.Rank), r.Pilot, int(r.SeasonScore), int(r.Wins), int(r.Top2), int(r.Top4), int(r.Top8),
         int(r.Top16), int(r.Points), int(r.Starts))
        for r in table.head(16).itertuples()
    ]
    assert got == EXPECTED_TOP16

    tied = table[table.Tied]
    group_sizes = tied.Rank.value_counts()
    assert len(table) == 1197
    assert tied.Rank.nunique() == 64
    assert len(tied) == 584
    assert int(tied.Rank.min()) == 147
    assert int(group_sizes.max()) == 26 and int(group_sizes.idxmax()) == 905
    assert int((table.Starts > 10).sum()) == 90

    # The same checks validate_league runs on a published k10 table.
    le._validate_k10(table.to_dict("records"), K10, results)


# --- full export path: late Autumn arrival, empty and near-empty Winter ---------------------------

def _event_rows(event_id, event_date, first_login=1):
    """One 32-player Challenge, LoginIDs first_login..first_login+31 in placement order."""
    return [(first_login + place - 1, event_id, event_date, place, le.league_points_for_place(place), "Challenge")
            for place in range(1, 33)]


def _export_all(tmp_path, rows, as_of, seasons, ingested_at=None):
    """Writes result files, season_config.csv (rows for *seasons*), then runs the site export, the
    results export and the deploy guards exactly as league-rebuild does. Returns (docs_dir, ok)."""
    from league_export_guards import check_docs_data
    from league_results_export import export_results_and_manifest
    from league_site_export import export_league_site

    league = tmp_path / "league"
    res = league / "results"
    res.mkdir(parents=True)
    docs = tmp_path / "docs"
    docs.mkdir()
    if rows:
        _write_results(res, result_rows(rows))
    for name in seasons:
        x = next(x for x in lr.SEASONS if x.name == name)
        le.upsert_season_config(league / "season_config.csv", x.name, x.start, x.end)
    if ingested_at:
        le._write_csv_lf(pd.DataFrame(sorted(ingested_at.items()), columns=["EventID", "IngestedAtUTC"]),
                         league / "ingestion_log.csv")
    log = []
    export_league_site(league, docs, as_of, log=log.append)
    ok = export_results_and_manifest(res, league / "season_config.csv", docs, "modern", league / "ingestion_log.csv",
                                     docs / "pilot_league_seasons.csv", as_of, log=log.append)
    guards = check_docs_data(docs, identity_map={}, log=log.append)
    return docs, ok and guards, log


def test_autumn_event_ingested_in_december_stays_in_autumn_under_legacy(tmp_path):
    rows = _event_rows(9001, "2026-11-30") + _event_rows(9002, "2026-12-01", first_login=100)
    touched = le.seasons_for_dates([date(2026, 11, 30), date(2026, 12, 1)])
    assert sorted(touched) == ["Autumn 2026", "Winter 2026/27"]
    docs, ok, log = _export_all(tmp_path, rows, date(2026, 12, 2), touched,
                                ingested_at={"9001": "2026-12-02T09:15:00Z", "9002": "2026-12-02T09:15:00Z"})
    assert ok, log
    autumn = pd.read_csv(docs / "pilot_league_results_Autumn_2026.csv", dtype=str)
    assert set(autumn.EventID) == {"9001"} and set(autumn.Season) == {"Autumn 2026"}
    assert "Counted" not in autumn.columns  # legacy columns
    autumn_doc = json.loads((docs / "season_Autumn_2026.json").read_text(encoding="utf-8"))
    assert "ruleset" not in autumn_doc and list(range(1, 33)) == [p["rank"] for p in autumn_doc["pilots"]]
    winter = pd.read_csv(docs / "pilot_league_results_Winter_2026-27.csv", dtype=str)
    assert set(winter.EventID) == {"9002"} and set(winter.Season) == {"Winter 2026/27"}


@pytest.mark.parametrize("winter_row", [False, True])
def test_empty_winter_season_exports_cleanly(tmp_path, winter_row):
    # Without a season_config row (the pipeline only writes one once a Winter result exists) and
    # with one: either way Winter is listed from 2026-12-01 as an empty k10 season, and Autumn --
    # ended, no Winter result ingested yet -- stays the default, "final results pending".
    autumn = _event_rows(9001, "2026-11-28")
    seasons_cfg = ["Autumn 2026", "Winter 2026/27"] if winter_row else ["Autumn 2026"]
    docs, ok, log = _export_all(tmp_path, autumn, date(2026, 12, 1), seasons_cfg)
    assert ok, log
    doc = json.loads((docs / "season_Winter_2026-27.json").read_text(encoding="utf-8"))
    assert (doc["pilotCount"], doc["pilots"], doc["weeks"], doc["ruleset"]) == (0, [], [], "k10")
    assert not (docs / "pilot_league_results_Winter_2026-27.csv").exists()
    seasons = json.loads((docs / "seasons.json").read_text(encoding="utf-8"))
    assert [x["season"] for x in seasons["seasons"]] == ["Autumn 2026", "Winter 2026/27"]
    assert seasons["currentSeason"] == "Autumn 2026"
    assert json.loads((docs / "season_Autumn_2026.json").read_text(encoding="utf-8"))["finalResultsPending"] is True


def test_winter_with_first_day_events_only(tmp_path):
    rows = _event_rows(9101, "2026-12-01") + _event_rows(9102, "2026-12-01", first_login=20)
    docs, ok, log = _export_all(tmp_path, rows, date(2026, 12, 2), ["Winter 2026/27"])
    assert ok, log
    doc = json.loads((docs / "season_Winter_2026-27.json").read_text(encoding="utf-8"))
    assert doc["weeks"] == []  # week 1 still running
    assert all(p["weeklyWins"] == 0 and p["weeklyWinsProvisional"] == 0 for p in doc["pilots"])
    assert doc["pilots"][0]["rank"] == 1


def test_guard_rejects_a_season_column_that_disagrees_with_the_calendar(tmp_path):
    from league_export_guards import check_season
    rows = _event_rows(9201, "2027-03-01")
    docs, ok, log = _export_all(tmp_path, rows, date(2027, 3, 2), ["Winter 2026/27"])
    assert ok, log
    csv = docs / "pilot_league_results_Winter_2026-27.csv"
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert set(df.Season) == {"Winter 2026/27"}
    df["Season"] = "Spring 2027"
    le._write_csv_lf(df, csv)
    failures = check_season(docs / "season_Winter_2026-27.json", csv, docs / "pilots_Winter_2026-27.json", {})
    assert any("Season" in f for f in failures), failures
