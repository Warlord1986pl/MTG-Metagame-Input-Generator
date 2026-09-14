"""Regression tests for challenge_mtgo_source.parse_mtgo_event's final_rank validation.

Found live on the Mikrus collector deployment (2026-09-14): a real "Modern Challenge 16" event
(mtgo.com event_id=12853784, 2026-09-08) with an actual, final, correctly-published field of 31
players -- not 32 -- got permanently stuck as ERROR ("final_rank is not exactly {1..32}") on every
retry forever, because the event's own decklist count was never consulted; the check was hardcoded
to require exactly rank 1..32 regardless of how many players actually played. A second, independent
event (12854076, 29 players) hit the identical failure four days later, confirming this is a
recurring, real mtgo.com event shape (Challenges run at several real capacities: 16/32/64/96/...,
per MtgoRegistryEvent.size's own docstring), not a one-off data glitch that would resolve on retry.

These tests use synthetic minimal blobs (no network, no fixtures) shaped exactly like the real
cached blob pulled from the VPS for event_id=12853784 (player_count.players="31", 31 decklists,
31 final_rank entries spanning 1..31).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from challenge_mtgo_source import (  # noqa: E402
    ChallengePendingError,
    ChallengeSourceError,
    parse_mtgo_event,
)

try:
    import pytest
except ImportError:  # pragma: no cover -- pytest is not a hard project dependency
    pytest = None


def _make_event(event_id: str, n_players: int, *, rank_offset: int = 0) -> dict:
    """A minimal, valid event blob with n_players decklists and a complete final_rank 1..n_players
    (or, with rank_offset != 0, a deliberately incomplete/shifted one for the negative tests)."""
    decklists = [
        {"loginid": f"login{i}", "player": f"Pilot{i}", "main_deck": []} for i in range(n_players)
    ]
    final_rank = [
        {"loginid": f"login{i}", "rank": i + 1 + rank_offset} for i in range(n_players)
    ]
    return {"event_id": event_id, "decklists": decklists, "final_rank": final_rank}


def test_32_player_event_still_parses():
    data = _make_event("11111111", 32)
    decks = parse_mtgo_event(data)
    assert len(decks) == 32
    assert sorted(d.place for d in decks) == list(range(1, 33))


def test_16_player_event_parses_not_treated_as_malformed():
    # Reproduces the real production event_id=12853784 shape (Modern Challenge 16, 31 real
    # players) at a rounder size to keep the fixture readable.
    data = _make_event("22222222", 16)
    decks = parse_mtgo_event(data)
    assert len(decks) == 16
    assert sorted(d.place for d in decks) == list(range(1, 17))


def test_31_player_event_matches_real_stuck_event_shape():
    # The exact attendance of the real event that was permanently stuck for 4+ days in production.
    data = _make_event("12853784", 31)
    decks = parse_mtgo_event(data)
    assert len(decks) == 31
    assert sorted(d.place for d in decks) == list(range(1, 32))


def test_zero_decklists_is_pending_not_malformed():
    data = {"event_id": "33333333", "decklists": [], "final_rank": [{"loginid": "login0", "rank": 1}]}
    with pytest.raises(ChallengePendingError) if pytest else _expect(ChallengePendingError):
        parse_mtgo_event(data)


def test_gap_in_rank_sequence_is_still_malformed():
    # 32 decklists but final_rank skips rank 5 (and duplicates rank 6) -- a genuine data anomaly,
    # must still be rejected regardless of the size-awareness fix.
    data = _make_event("44444444", 32)
    data["final_rank"][4]["rank"] = 6  # login4 (would-be rank 5) now duplicates login5's rank 6
    with pytest.raises(ChallengeSourceError) if pytest else _expect(ChallengeSourceError):
        parse_mtgo_event(data)


class _expect:
    """Fallback context manager so this file's assertions still run under `python -m pytest`-less
    execution (python tests/test_parse_mtgo_event.py), matching this repo's other test modules."""

    def __init__(self, exc_type):
        self.exc_type = exc_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if not (exc_type is not None and issubclass(exc_type, self.exc_type)):
            raise AssertionError(f"expected {self.exc_type.__name__} to be raised")
        return True


if __name__ == "__main__":
    tests = [
        test_32_player_event_still_parses,
        test_16_player_event_parses_not_treated_as_malformed,
        test_31_player_event_matches_real_stuck_event_shape,
        test_zero_decklists_is_pending_not_malformed,
        test_gap_in_rank_sequence_is_still_malformed,
    ]
    for t in tests:
        t()
        print(f"OK: {t.__name__}")
    print(f"{len(tests)} test(s) passed.")
