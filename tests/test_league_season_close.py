"""Season close by EventDate (league_rules.season_is_closed / default_season,
league_results_export.maybe_close_season / find_late_after_close), run through the same steps as
league-rebuild: result files -> season_config.csv -> site export -> results export -> deploy guards,
several runs on one league directory with controlled ingestion timestamps.

Scenario (Autumn 2026 -> Winter 2026/27):
  run 1, 2026-12-02: only Autumn-dated events -> no Autumn record, Autumn default + "final results
         pending", Winter listed empty;
  run 2, 2026-12-03: first Winter-dated event -> Autumn record written, default switches to Winter;
  run 3, 2026-12-05: a late Autumn event (2026-11-29) -> in Autumn standings and results, flagged
         LateArrivals/AfterSeasonClose with its champion/top-16 impact, record unchanged.
The Summer 2026 record is present from the start and must stay byte-identical.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import league_engine as le  # noqa: E402
import league_results_export as lre  # noqa: E402
import league_rules as lr  # noqa: E402
from league_export_guards import check_docs_data  # noqa: E402
from league_site_export import export_league_site  # noqa: E402

SUMMER_RECORD = (
    "Season,SeasonStart,SeasonEnd,CoverageStart,CoverageEnd,Partial,EventsTotal,EventsChallenge,"
    "EventsPremier,PilotsTotal,PointsTotal,ChampionLoginID,ChampionName,ChampionPoints\n"
    "Summer 2026,2026-06-01,2026-08-31,2026-07-13,2026-08-31,true,91,87,4,1032,2945,2903591,MeninoNey,56\n"
)


def _event(event_id: int, event_date: str, first_login: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"EventID": str(event_id), "EventDate": event_date, "Tier": "C32", "EventClass": "Challenge",
         "Pilot": f"p{first_login + place - 1}", "LoginID": str(first_login + place - 1), "Place": str(place),
         "Deck": "", "DeckGuess": "", "LeaguePoints": str(le.league_points_for_place(place)),
         "SwissRank": "", "SwissPoints": "", "OMWP": "", "GWP": "", "OGWP": ""}
        for place in range(1, 33)
    ], columns=le.LEAGUE_RESULTS_COLS)


class League:
    def __init__(self, root: Path, monkeypatch):
        self.league = root / "league"
        self.results = self.league / "results"
        self.results.mkdir(parents=True)
        self.docs = root / "docs"
        self.docs.mkdir()
        (self.docs / "pilot_league_seasons.csv").write_bytes(SUMMER_RECORD.encode("utf-8"))
        self.monkeypatch = monkeypatch

    def run(self, as_of: date, ingested_at: str, events):
        """One league-rebuild: write the new result files, upsert their seasons (run_league_update),
        then site export, results export and guards. Returns (season docs by name, seasons.json)."""
        for eid, d, first in events:
            le._write_csv_lf(_event(eid, d, first), self.results / f"{eid}.csv")
        for name, (s, e) in le.seasons_for_dates([date.fromisoformat(d) for _eid, d, _f in events]).items():
            le.upsert_season_config(self.league / "season_config.csv", name, s, e)
        self.monkeypatch.setattr(lre, "_utcnow_iso", lambda: ingested_at)
        log = []
        export_league_site(self.league, self.docs, as_of, log=log.append)
        ok = lre.export_results_and_manifest(
            self.results, self.league / "season_config.csv", self.docs, "modern",
            self.league / "ingestion_log.csv", self.docs / "pilot_league_seasons.csv", as_of, log=log.append,
        )
        assert ok and check_docs_data(self.docs, identity_map={}, log=log.append), log
        self.log = log
        seasons = json.loads((self.docs / "seasons.json").read_text(encoding="utf-8"))
        docs = {
            x["season"]: json.loads((self.docs / f"season_{x['slug']}.json").read_text(encoding="utf-8"))
            for x in seasons["seasons"]
        }
        return docs, seasons

    def registry(self) -> bytes:
        return (self.docs / "pilot_league_seasons.csv").read_bytes()

    def manifest(self, slug: str) -> dict:
        return json.loads((self.docs / f"pilot_league_results_{slug}_manifest.json").read_text(encoding="utf-8"))


def test_season_close_by_event_date_and_late_arrival_after_close(tmp_path, monkeypatch):
    lg = League(tmp_path, monkeypatch)

    # Run 1: Autumn-dated events only (pilots 1 and 100 each win one event: co-leaders).
    docs, seasons = lg.run(date(2026, 12, 2), "2026-12-02T08:15:00Z",
                           [(9001, "2026-11-28", 1), (9002, "2026-11-30", 100)])
    assert lg.registry() == SUMMER_RECORD.encode("utf-8")  # no Autumn record yet
    assert seasons["currentSeason"] == "Autumn 2026"
    assert docs["Autumn 2026"]["finalResultsPending"] is True
    assert docs["Winter 2026/27"]["pilots"] == []  # listed from its first day, empty
    assert [x["isCurrent"] for x in seasons["seasons"]] == [True, False]

    # Run 2: the first Winter-dated result is ingested -> Autumn closes on this run.
    docs, seasons = lg.run(date(2026, 12, 3), "2026-12-03T08:15:00Z", [(9101, "2026-12-01", 200)])
    registry = pd.read_csv(pd.io.common.BytesIO(lg.registry()), dtype=str)
    assert list(registry.Season) == ["Summer 2026", "Autumn 2026", "Autumn 2026"]  # co-champions 1 and 100
    assert set(registry[registry.Season == "Autumn 2026"].ChampionLoginID) == {"1", "100"}
    assert lg.registry().startswith(SUMMER_RECORD.encode("utf-8"))
    assert seasons["currentSeason"] == "Winter 2026/27"
    assert "finalResultsPending" not in docs["Autumn 2026"]
    assert not [x for x in lg.manifest("Autumn_2026")["LateArrivals"] if x.get("AfterSeasonClose")]
    record_after_close = lg.registry()

    # Run 3: a late Autumn result (2026-11-29, pilot 100 wins again) arrives after the close.
    docs, seasons = lg.run(date(2026, 12, 5), "2026-12-05T08:15:00Z", [(9003, "2026-11-29", 100)])
    assert lg.registry() == record_after_close  # record never changed automatically
    autumn_results = pd.read_csv(lg.docs / "pilot_league_results_Autumn_2026.csv", dtype=str)
    assert "9003" in set(autumn_results.EventID)
    top = docs["Autumn 2026"]["pilots"][0]
    assert (top["loginId"], top["points"], top["starts"]) == ("100", 10, 2)
    late = [x for x in lg.manifest("Autumn_2026")["LateArrivals"] if x.get("AfterSeasonClose")]
    assert late == [{
        "EventID": "9003", "EventDate": "2026-11-29", "IngestedAt": "2026-12-05T08:15:00Z",
        "AfterSeasonClose": True, "SeasonClosedAt": "2026-12-03T08:15:00Z",
        "ChangesChampion": True, "ChangesTop16": True,
    }]
    assert any("LATE AFTER SEASON CLOSE" in m and "9003" in m for m in lg.log)
    assert seasons["currentSeason"] == "Winter 2026/27"
    assert not [x for x in lg.manifest("Winter_2026-27")["LateArrivals"] if x.get("AfterSeasonClose")]


def test_default_season_rule():
    seasons = [("Summer 2026", date(2026, 6, 1), date(2026, 8, 31)),
               ("Autumn 2026", date(2026, 9, 1), date(2026, 11, 30)),
               ("Winter 2026/27", date(2026, 12, 1), date(2027, 3, 1))]
    closed = {"Summer 2026": True, "Autumn 2026": False, "Winter 2026/27": False}
    assert lr.default_season(seasons, closed, date(2026, 11, 26)) == "Autumn 2026"
    assert lr.default_season(seasons, closed, date(2026, 12, 2)) == "Autumn 2026"
    assert lr.default_season(seasons, {**closed, "Autumn 2026": True}, date(2026, 12, 3)) == "Winter 2026/27"
    assert lr.season_is_closed("Autumn 2026", date(2026, 11, 30), date(2026, 11, 30), ["Summer 2026"]) is False
    assert lr.season_is_closed("Autumn 2026", date(2026, 11, 30), date(2026, 12, 1), []) is True
    assert lr.season_is_closed("Summer 2026", date(2026, 8, 31), None, ["Summer 2026"]) is True


@pytest.mark.parametrize("late_ingested", [False, True])
def test_no_close_while_only_autumn_dated_events_are_ingested(tmp_path, monkeypatch, late_ingested):
    # Even long after 2026-11-30 (run date 2026-12-20): no Winter-dated result -> no record.
    lg = League(tmp_path, monkeypatch)
    lg.run(date(2026, 12, 2), "2026-12-02T08:15:00Z", [(9001, "2026-11-28", 1)])
    if late_ingested:
        lg.run(date(2026, 12, 20), "2026-12-20T08:15:00Z", [(9002, "2026-11-30", 100)])
    assert lg.registry() == SUMMER_RECORD.encode("utf-8")
