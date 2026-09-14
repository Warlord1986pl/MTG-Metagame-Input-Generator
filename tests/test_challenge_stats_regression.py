"""Frozen ground-truth regression test for the Challenge-stats aggregation layer.

Fixture: tests/fixtures/challenge_history_ground_truth_2026-07-13_to_2026-07-26.csv, a verified
768-row (24-event) slice of outputs/challenge_history_modern.csv, manually reconciled against
https://www.mtgo.com/decklists for the 2026-07-13..2026-07-26 window (15 x Challenge 64, 5 x
Challenge 32, 4 x Challenge 96; 2 premier events -- RC Super Qualifier 2026-07-19 and Showcase
Qualifier 2026-07-25 -- correctly excluded).

This guards the aggregation/invariant layer (challenge_history_engine.py) offline and
deterministically. It does not re-exercise the mtgo.com/MTGGoldfish fetch+classification layer
(challenge_mtgo_source.py) since that requires live network access; that layer is what produced
this fixture in the first place, cross-checked by hand against mtgo.com at the time.

Run directly: python tests/test_challenge_stats_regression.py
Or via pytest: pytest tests/test_challenge_stats_regression.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd  # noqa: E402

from challenge_history_engine import run_challenge_statistics  # noqa: E402
from challenge_mtgo_source import CompletenessSummary  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "challenge_history_ground_truth_2026-07-13_to_2026-07-26.csv"
WEEK_START = date(2026, 7, 13)
WEEK_END = date(2026, 7, 26)

EXPECTED_TIER_TROPHIES = {"32": 5, "64": 15, "96": 4}
EXPECTED_TOTAL_TROPHIES = 24
EXPECTED_TOTAL_TOP32_ENTRIES = 768
EXPECTED_TOTAL_TOP8_ENTRIES = 192


def test_challenge_stats_matches_frozen_ground_truth() -> None:
    assert FIXTURE.exists(), f"missing fixture: {FIXTURE}"

    tmp_dir = Path(tempfile.mkdtemp(prefix="challenge_stats_regression_"))
    try:
        history_csv = tmp_dir / "challenge_history_modern.csv"
        shutil.copyfile(FIXTURE, history_csv)

        # This fixture is a frozen, manually-reconciled ground truth (see module docstring) --
        # completeness is asserted directly rather than computed live, since this test is meant to
        # run offline. run_challenge_statistics() now treats completeness=None as fail-closed
        # INCOMPLETE (it no longer silently defaults to complete=true), so a real signal matching
        # the fixture's known-good state must be passed explicitly.
        completeness = CompletenessSummary(
            registry_count=EXPECTED_TOTAL_TROPHIES,
            fetched_count=EXPECTED_TOTAL_TROPHIES,
            complete=True,
            missing=[],
            tier_counts_registry={int(k): v for k, v in EXPECTED_TIER_TROPHIES.items()},
            tier_counts_fetched={int(k): v for k, v in EXPECTED_TIER_TROPHIES.items()},
            premier_checked=True,
            premier_count=2,
            premier_events=[
                ("12847670", "RC Super Qualifier", "2026-07-19"),
                ("12848159", "Showcase Qualifier", "2026-07-25"),
            ],
            premier_note="2026-07-19 RC Super Qualifier (12847670); 2026-07-25 Showcase Qualifier (12848159)",
        )
        result = run_challenge_statistics(
            history_csv=history_csv,
            output_dir=tmp_dir / "stats_out",
            format_name="Modern",
            week_start=WEEK_START,
            week_end=WEEK_END,
            completeness=completeness,
        )

        assert result.excel_path.exists(), "xlsx was not written -- an invariant check must have failed"
        assert result.events_processed == EXPECTED_TOTAL_TROPHIES, (
            f"N_all={result.events_processed}, expected {EXPECTED_TOTAL_TROPHIES}"
        )

        xl = pd.ExcelFile(result.excel_path)
        for tier, expected_n in EXPECTED_TIER_TROPHIES.items():
            sheet = f"C{tier}_Decks"
            assert sheet in xl.sheet_names, f"missing sheet {sheet}"
            df = xl.parse(sheet)
            assert int(df["WinnerCount"].sum()) == expected_n, f"{sheet}: sum(WinnerCount) != {expected_n}"
            assert int(df["Top32EntryCount"].sum()) == expected_n * 32, f"{sheet}: sum(Top32EntryCount) != {expected_n * 32}"
            assert int(df["Top8Count"].sum()) == expected_n * 8, f"{sheet}: sum(Top8Count) != {expected_n * 8}"

        deck_all = xl.parse("ALL_Decks")
        assert int(deck_all["WinnerCount"].sum()) == EXPECTED_TOTAL_TROPHIES
        assert int(deck_all["Top32EntryCount"].sum()) == EXPECTED_TOTAL_TOP32_ENTRIES
        assert int(deck_all["Top8Count"].sum()) == EXPECTED_TOTAL_TOP8_ENTRIES

        pilots = xl.parse("BestPilots")
        assert int(pilots["Wins"].sum()) == EXPECTED_TOTAL_TROPHIES
        assert int(pilots["Top8"].sum()) == EXPECTED_TOTAL_TOP8_ENTRIES
        assert int(pilots["Top32"].sum()) == EXPECTED_TOTAL_TOP32_ENTRIES

        # No C96 sheet used to exist at all (Phase 2a/2b gap) -- lock in that it does now.
        assert "C96_Decks" in xl.sheet_names
        assert "C96_Archetypes" in xl.sheet_names
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_challenge_stats_accepts_a_real_sub_32_challenge_16_event() -> None:
    """Regression for the real production bug (2026-09-14, see PR #1-3): a genuine 'Modern
    Challenge 16' event with real attendance under 32 is a COMPLETE, valid event, not a corrupt
    32-player one missing rows -- mtgo.com runs Challenges at several real capacities. Before this
    fix, _check_challenge_invariants hardcoded every check (Place coverage, decklist count,
    Top32EntryCount/BestPilots Top32 sums) to assume exactly 32 per event, so this synthetic
    16-player Challenge-16 event mixed with a normal 32-player Challenge would have made the whole
    xlsx refuse to write.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="challenge_stats_c16_regression_"))
    try:
        rows = []
        for place in range(1, 33):  # a normal, complete 32-player Challenge 32
            rows.append({
                "EventDate": "2026-09-08", "Format": "Modern", "Tier": 32,
                "EventSlug": "modern-challenge-32", "EventID": "90000001", "Place": place,
                "Deck": "TestDeck", "Archetype": "TestArchetype", "Pilot": f"c32pilot{place}",
            })
        for place in range(1, 17):  # a real, complete 16-player Challenge 16 -- NOT missing rows
            rows.append({
                "EventDate": "2026-09-08", "Format": "Modern", "Tier": 16,
                "EventSlug": "modern-challenge-16", "EventID": "90000002", "Place": place,
                "Deck": "TestDeck", "Archetype": "TestArchetype", "Pilot": f"c16pilot{place}",
            })
        history_csv = tmp_dir / "challenge_history_modern.csv"
        pd.DataFrame(rows).to_csv(history_csv, index=False, encoding="utf-8-sig")

        completeness = CompletenessSummary(
            registry_count=2, fetched_count=2, complete=True, missing=[],
            tier_counts_registry={32: 1, 16: 1}, tier_counts_fetched={32: 1, 16: 1},
            premier_checked=True, premier_count=0, premier_events=[], premier_note="",
        )
        result = run_challenge_statistics(
            history_csv=history_csv,
            output_dir=tmp_dir / "stats_out",
            format_name="Modern",
            week_start=date(2026, 9, 8),
            week_end=date(2026, 9, 8),
            completeness=completeness,
        )

        assert result.excel_path.exists(), "xlsx was not written -- an invariant check must have failed"

        xl = pd.ExcelFile(result.excel_path)
        c32 = xl.parse("C32_Decks")
        assert int(c32["Top32EntryCount"].sum()) == 32
        c16 = xl.parse("C16_Decks")
        assert int(c16["Top32EntryCount"].sum()) == 16, (
            f"a complete 16-player Challenge-16 event must contribute 16, not 32: "
            f"got {int(c16['Top32EntryCount'].sum())}"
        )

        deck_all = xl.parse("ALL_Decks")
        assert int(deck_all["Top32EntryCount"].sum()) == 48  # 32 + 16, not 2*32=64

        pilots = xl.parse("BestPilots")
        assert int(pilots["Top32"].sum()) == 48
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_challenge_stats_matches_frozen_ground_truth()
    print("OK: challenge stats regression test passed.")
    test_challenge_stats_accepts_a_real_sub_32_challenge_16_event()
    print("OK: challenge stats accepts a real sub-32 Challenge-16 event.")
