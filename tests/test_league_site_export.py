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

import pandas as pd  # noqa: E402

from league_site_export import _pilot_results_rows  # noqa: E402


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


if __name__ == "__main__":
    test_deck_guess_wins_over_last_played_deck()
    print("OK: test_deck_guess_wins_over_last_played_deck")
    test_falls_back_to_last_played_deck_when_deck_guess_blank()
    print("OK: test_falls_back_to_last_played_deck_when_deck_guess_blank")
