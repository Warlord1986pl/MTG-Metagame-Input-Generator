"""Regression test for the pilot-profile "Results" table's Deck column.

Found live via the published site (a pilot who won a real 2026-09-12 Challenge 64 event whose
winning decklist was still NEEDS_MANUAL_REVIEW): docs/index.html renders r.deck almost verbatim
(only an empty/falsy value falls back to "-"), so the raw internal NEEDS_MANUAL_REVIEW marker was
showing up unchanged on a general-audience page, right next to that pilot's actual win.

_pilot_results_rows now fills a still-unclassified event's deck with a two-tier fallback:
  1. DeckGuess -- the classifier's own nearest-neighbor label for that exact event
     (challenge_mtgo_source.classify_deck's nearest_label, carried through regardless of whether
     it cleared the auto-accept confidence threshold), when available.
  2. This SAME identity's own most recent PRIOR resolved deck, carried forward across a whole
     run of consecutive unresolved events until the next real resolution -- only used when
     DeckGuess is itself blank.
An identity's first-ever appearance being unresolved with no DeckGuess yet has nothing to infer
from and stays blank.

Run directly: python tests/test_league_site_export.py
Or via pytest: pytest tests/test_league_site_export.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import shutil  # noqa: E402
import tempfile  # noqa: E402
from datetime import date  # noqa: E402

import pandas as pd  # noqa: E402

from league_engine import LEAGUE_RESULTS_COLS  # noqa: E402
from league_site_export import _pilot_results_rows, build_season_site_data  # noqa: E402


def test_deck_guess_wins_over_last_played_deck() -> None:
    grp = pd.DataFrame([
        {
            "EventDate": "2026-09-12", "EventID": "12854081", "Tier": "C64", "EventClass": "Challenge",
            "Place": 1, "Deck": "NEEDS_MANUAL_REVIEW", "DeckGuess": "Burn", "LeaguePoints": 5,
            "SwissPoints": 8, "GWP": 0.7,
        },
        {
            "EventDate": "2026-09-05", "EventID": "12853780", "Tier": "C16", "EventClass": "Challenge",
            "Place": 2, "Deck": "Esper Blink", "DeckGuess": "", "LeaguePoints": 4,
            "SwissPoints": 6, "GWP": 0.6,
        },
    ])
    rows = _pilot_results_rows(grp)
    by_event = {r["eventId"]: r for r in rows}

    assert by_event["12854081"]["deck"] == "Burn", (
        "DeckGuess (the classifier's own nearest-neighbor guess for THIS event) must win over "
        "the pilot's last-played-deck fallback"
    )
    assert by_event["12853780"]["deck"] == "Esper Blink", "a real deck name must pass through unchanged"
    assert "_deckGuess" not in by_event["12854081"], "DeckGuess is internal-only, not part of the public row"


def test_falls_back_to_last_played_deck_when_deck_guess_blank() -> None:
    grp = pd.DataFrame([
        {
            "EventDate": "2026-09-12", "EventID": "12854081", "Tier": "C64", "EventClass": "Challenge",
            "Place": 1, "Deck": "NEEDS_MANUAL_REVIEW", "DeckGuess": "", "LeaguePoints": 5,
            "SwissPoints": 8, "GWP": 0.7,
        },
        {
            "EventDate": "2026-09-10", "EventID": "12854060", "Tier": "C64", "EventClass": "Challenge",
            "Place": 9, "Deck": "NEEDS_MANUAL_REVIEW", "DeckGuess": "", "LeaguePoints": 1,
            "SwissPoints": 5, "GWP": 0.5,
        },
        {
            "EventDate": "2026-09-05", "EventID": "12853780", "Tier": "C16", "EventClass": "Challenge",
            "Place": 2, "Deck": "Esper Blink", "DeckGuess": "", "LeaguePoints": 4,
            "SwissPoints": 6, "GWP": 0.6,
        },
        {
            "EventDate": "2026-08-29", "EventID": "12853163", "Tier": "C32", "EventClass": "Challenge",
            "Place": 1, "Deck": "NEEDS_MANUAL_REVIEW", "DeckGuess": "", "LeaguePoints": 5,
            "SwissPoints": 7, "GWP": 0.65,
        },
    ])
    rows = _pilot_results_rows(grp)
    by_event = {r["eventId"]: r for r in rows}

    # Both unresolved events after the 09-05 Esper Blink result inherit it -- carried across
    # a whole consecutive run, not just the single nearest event.
    assert by_event["12854081"]["deck"] == "Esper Blink"
    assert by_event["12854060"]["deck"] == "Esper Blink"
    assert by_event["12853780"]["deck"] == "Esper Blink", "a real deck name must pass through unchanged"
    # This pilot's oldest appearance on file, with nothing earlier to infer from and no DeckGuess.
    assert by_event["12853163"]["deck"] == ""


def test_bracket_matches_use_the_same_resolved_deck_as_the_results_table() -> None:
    """The bracket-matches list (head-to-head opponent history) has its own raw WinnerDeck/
    LoserDeck columns (outputs/league/matches/<EventID>.csv, league_matches.py) -- same
    NEEDS_MANUAL_REVIEW exposure risk as the Results table's raw Deck, found live on the same
    real pilot/event. build_season_site_data must show the SAME already-resolved value
    (DeckGuess, in this case) in bracketMatches as it does in that pilot's own "results" row for
    the identical event, not the matches file's stale raw value.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="site_export_bracket_regression_"))
    try:
        results_dir = tmp_dir / "results"
        matches_dir = tmp_dir / "matches"
        results_dir.mkdir()
        matches_dir.mkdir()

        def result_row(event_id, pilot, login_id, place, deck, deck_guess):
            return {
                "EventID": event_id, "EventDate": "2026-09-12", "Tier": "C64", "EventClass": "Challenge",
                "Pilot": pilot, "LoginID": login_id, "Place": place, "Deck": deck, "DeckGuess": deck_guess,
                "LeaguePoints": 5 if place == 1 else 4, "SwissRank": place, "SwissPoints": 20,
                "OMWP": 0.55, "GWP": 0.7, "OGWP": 0.5,
            }

        pd.DataFrame(
            [
                result_row("90000001", "Winner", "1001", 1, "NEEDS_MANUAL_REVIEW", "Burn"),
                result_row("90000001", "Runner", "1002", 2, "Tron", ""),
            ],
            columns=LEAGUE_RESULTS_COLS,
        ).to_csv(results_dir / "90000001.csv", index=False, encoding="utf-8-sig")

        pd.DataFrame([{
            "EventID": "90000001", "EventDate": "2026-09-12", "Tier": "C64", "EventClass": "Challenge",
            "Round": "F", "WinnerPilot": "Winner", "WinnerLoginID": "1001",
            "LoserPilot": "Runner", "LoserLoginID": "1002", "WinnerGames": "2", "LoserGames": "0",
            # Stale raw values, as if captured before this event was ever reclassified -- exactly
            # the real production scenario.
            "WinnerDeck": "NEEDS_MANUAL_REVIEW", "LoserDeck": "Tron",
        }]).to_csv(matches_dir / "90000001.csv", index=False, encoding="utf-8-sig")

        _season_doc, pilots_doc = build_season_site_data(
            results_dir, "Autumn 2026", date(2026, 9, 1), date(2026, 11, 30), as_of=date(2026, 9, 14),
            matches_dir=matches_dir,
        )
        pilots = pilots_doc["pilots"]
        winner = pilots["id:1001"]
        assert winner["results"][0]["deck"] == "Burn"
        match = winner["bracketMatches"][0]
        assert match["pilotDeck"] == "Burn", (
            f"bracketMatches must show the same resolved deck as the Results table, not the raw "
            f"matches-file value: got {match['pilotDeck']!r}"
        )
        assert match["opponentDeck"] == "Tron"

        runner = pilots["id:1002"]
        runner_match = runner["bracketMatches"][0]
        assert runner_match["pilotDeck"] == "Tron"
        assert runner_match["opponentDeck"] == "Burn"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_deck_guess_wins_over_last_played_deck()
    print("OK: test_deck_guess_wins_over_last_played_deck")
    test_falls_back_to_last_played_deck_when_deck_guess_blank()
    print("OK: test_falls_back_to_last_played_deck_when_deck_guess_blank")
    test_bracket_matches_use_the_same_resolved_deck_as_the_results_table()
    print("OK: test_bracket_matches_use_the_same_resolved_deck_as_the_results_table")
