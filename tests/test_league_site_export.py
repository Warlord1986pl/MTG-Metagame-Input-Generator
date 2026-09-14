"""Regression test for the pilot-profile "Results" table's Deck column.

Found live via the published site (a pilot who won a real 2026-09-12 Challenge 64 event whose
winning decklist was still NEEDS_MANUAL_REVIEW): docs/index.html renders r.deck almost verbatim
(only an empty/falsy value falls back to "-"), so the raw internal NEEDS_MANUAL_REVIEW marker was
showing up unchanged on a general-audience page, right next to that pilot's actual win. Every
other place in this codebase that surfaces an unresolved deck to a reader (challenge_history_engine
Deck stats tables) already relabels it "Unknown" -- _pilot_results_rows just hadn't been.

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


def test_needs_manual_review_deck_shows_as_unknown() -> None:
    grp = pd.DataFrame([
        {
            "EventDate": "2026-09-12", "EventID": "12854081", "Tier": "C64", "EventClass": "Challenge",
            "Place": 1, "Deck": "NEEDS_MANUAL_REVIEW", "LeaguePoints": 5, "SwissPoints": 8, "GWP": 0.7,
        },
        {
            "EventDate": "2026-09-05", "EventID": "12853780", "Tier": "C16", "EventClass": "Challenge",
            "Place": 2, "Deck": "Esper Blink", "LeaguePoints": 4, "SwissPoints": 6, "GWP": 0.6,
        },
    ])
    rows = _pilot_results_rows(grp)
    by_event = {r["eventId"]: r for r in rows}

    assert by_event["12854081"]["deck"] == "Unknown", (
        "an unresolved deck must display as Unknown, not the raw NEEDS_MANUAL_REVIEW marker"
    )
    assert by_event["12853780"]["deck"] == "Esper Blink", "a real deck name must pass through unchanged"


if __name__ == "__main__":
    test_needs_manual_review_deck_shows_as_unknown()
    print("OK: test_needs_manual_review_deck_shows_as_unknown")
