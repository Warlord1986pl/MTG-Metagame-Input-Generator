"""Regression test for the classifier's nearest-neighbor guess reaching the site.

classify_deck (challenge_mtgo_source.py) always computes a nearest-neighbor label, even for a
decklist it flags needs_review -- either because the similarity didn't clear the auto-accept
threshold, or because the deck placed 1st (always manual, regardless of confidence). That value
used to be discarded entirely once Deck was set to NEEDS_MANUAL_REVIEW; ChallengeEventRow.deck_guess
(and HISTORY_COLS/LEAGUE_RESULTS_COLS' DeckGuess column) now carries it through so the site's
pilot profile can show a real, computed best guess instead of nothing (see
league_site_export._pilot_results_rows).

Run directly: python tests/test_classify_deck_guess.py
Or via pytest: pytest tests/test_classify_deck_guess.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from challenge_mtgo_source import (  # noqa: E402
    MtgoEventDeck,
    MtgoRegistryEvent,
    NEEDS_MANUAL_REVIEW,
    _classify_event_decks,
    classify_deck,
)

LIBRARY = [
    ("Burn", {"Lightning Bolt": 4, "Goblin Guide": 4, "Lava Spike": 4}),
    ("Esper Blink", {"Restoration Angel": 4, "Blink of an Eye": 4, "Supreme Verdict": 4}),
]


def test_place1_always_needs_review_but_nearest_label_is_still_computed() -> None:
    # Near-perfect match to Burn, but place=1 forces manual review regardless of confidence.
    target = MtgoEventDeck(
        loginid="1", player="Winner", place=1,
        signature={"Lightning Bolt": 4, "Goblin Guide": 4, "Lava Spike": 4},
    )
    result = classify_deck(target, LIBRARY)
    assert result.needs_review is True
    assert result.predicted_label is None
    assert result.nearest_label == "Burn", "the nearest-neighbor guess must still be computed for a Place=1 review"


def test_below_threshold_needs_review_but_nearest_label_is_still_computed() -> None:
    # Similarity to Burn is well under the 0.90 auto-accept threshold for places 9-32, but Burn
    # is still clearly the nearest (and only meaningfully close) candidate.
    target = MtgoEventDeck(
        loginid="2", player="Ninth", place=9,
        signature={"Lightning Bolt": 4, "Goblin Guide": 2, "Random Card": 6},
    )
    result = classify_deck(target, LIBRARY)
    assert result.needs_review is True
    assert result.nearest_sim < 0.90
    assert result.nearest_label == "Burn"


def test_classify_event_decks_propagates_nearest_label_to_deck_guess() -> None:
    event = MtgoRegistryEvent(
        event_id="99999999", date="2026-09-12", slug="modern-challenge-64",
        kind="challenge", size=64, url="https://www.mtgo.com/decklist/modern-challenge-64-2026-09-1299999999",
    )
    decks = [
        MtgoEventDeck(
            loginid="1", player="Winner", place=1,
            signature={"Lightning Bolt": 4, "Goblin Guide": 4, "Lava Spike": 4},
        ),
    ]
    review_items: list = []
    rows = _classify_event_decks(
        event, decks, LIBRARY, archetype_for=lambda label, raw: label,
        review_items=review_items,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.deck == NEEDS_MANUAL_REVIEW, "Place=1 always requires manual confirmation for Deck itself"
    assert row.deck_guess == "Burn", "the classifier's nearest-neighbor guess must reach ChallengeEventRow"
    assert len(review_items) == 1
    assert review_items[0].nearest_label == "Burn"


if __name__ == "__main__":
    test_place1_always_needs_review_but_nearest_label_is_still_computed()
    print("OK: test_place1_always_needs_review_but_nearest_label_is_still_computed")
    test_below_threshold_needs_review_but_nearest_label_is_still_computed()
    print("OK: test_below_threshold_needs_review_but_nearest_label_is_still_computed")
    test_classify_event_decks_propagates_nearest_label_to_deck_guess()
    print("OK: test_classify_event_decks_propagates_nearest_label_to_deck_guess")
