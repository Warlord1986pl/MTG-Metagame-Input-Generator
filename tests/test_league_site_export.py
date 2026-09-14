"""Regression test for the pilot-profile "Results" table's Deck column.

Found live via the published site (a pilot who won a real 2026-09-12 Challenge 64 event whose
winning decklist was still NEEDS_MANUAL_REVIEW): docs/index.html renders r.deck almost verbatim
(only an empty/falsy value falls back to "-"), so the raw internal NEEDS_MANUAL_REVIEW marker was
showing up unchanged on a general-audience page, right next to that pilot's actual win.

_pilot_results_rows now fills a still-unclassified event's deck with that SAME pilot's own most
recent PRIOR resolved deck -- pilots overwhelmingly keep playing one deck across consecutive
events, so their last confirmed choice is the best available guess, carried forward across any
run of consecutive unresolved events until the next real resolution. An identity's first-ever
appearance being unresolved has nothing to infer from and stays blank.

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


def test_unresolved_deck_filled_from_most_recent_prior_resolved_deck() -> None:
    grp = pd.DataFrame([
        {
            "EventDate": "2026-09-12", "EventID": "12854081", "Tier": "C64", "EventClass": "Challenge",
            "Place": 1, "Deck": "NEEDS_MANUAL_REVIEW", "LeaguePoints": 5, "SwissPoints": 8, "GWP": 0.7,
        },
        {
            "EventDate": "2026-09-10", "EventID": "12854060", "Tier": "C64", "EventClass": "Challenge",
            "Place": 9, "Deck": "NEEDS_MANUAL_REVIEW", "LeaguePoints": 1, "SwissPoints": 5, "GWP": 0.5,
        },
        {
            "EventDate": "2026-09-05", "EventID": "12853780", "Tier": "C16", "EventClass": "Challenge",
            "Place": 2, "Deck": "Esper Blink", "LeaguePoints": 4, "SwissPoints": 6, "GWP": 0.6,
        },
        {
            "EventDate": "2026-08-29", "EventID": "12853163", "Tier": "C32", "EventClass": "Challenge",
            "Place": 1, "Deck": "NEEDS_MANUAL_REVIEW", "LeaguePoints": 5, "SwissPoints": 7, "GWP": 0.65,
        },
    ])
    rows = _pilot_results_rows(grp)
    by_event = {r["eventId"]: r for r in rows}

    # Both unresolved events after the 09-05 Esper Blink result inherit it -- carried across
    # a whole consecutive run, not just the single nearest event.
    assert by_event["12854081"]["deck"] == "Esper Blink"
    assert by_event["12854060"]["deck"] == "Esper Blink"
    assert by_event["12853780"]["deck"] == "Esper Blink", "a real deck name must pass through unchanged"
    # This pilot's oldest appearance on file, with nothing earlier to infer from.
    assert by_event["12853163"]["deck"] == ""


if __name__ == "__main__":
    test_unresolved_deck_filled_from_most_recent_prior_resolved_deck()
    print("OK: test_unresolved_deck_filled_from_most_recent_prior_resolved_deck")
