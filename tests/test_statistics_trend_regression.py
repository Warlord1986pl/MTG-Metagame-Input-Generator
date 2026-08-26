"""Regression tests for the Trend Label unification (statistics_engine.py, 2026-08-24).

Background: the PNG legend (meta_trend_Archetype/_Deck) and the tabular "Trend Label" column
used to be computed two different ways -- a full-window average in _create_trend_chart vs. a
last-4-week average in run_statistics's Deck path -- and could disagree for the same key in the
same document (e.g. Broodscale Combo: legend showed rising, table said "Falling Deck" for the
2026-08-10..08-23 window). Separately, the Archetype-level Trend Label column was hardcoded to
"Stable" and never computed at all.

Both bugs are now impossible by construction: resolve_trend_status() is the single function
both call, and run_statistics raises RuntimeError (aborting before any file is written) if either
symptom reappears -- a flat Archetype Trend Label column, or a legend/table disagreement for any
key. These tests exercise the real code path (a small deterministic multi-week scenario, hand
verified below) rather than re-deriving the expected numbers from the same code under test.

Run directly: python tests/test_statistics_trend_regression.py
Or via pytest: pytest tests/test_statistics_trend_regression.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd  # noqa: E402

import statistics_engine  # noqa: E402
from statistics_engine import run_statistics  # noqa: E402

# Six decks across three archetypes, five weeks of Meta share. Archetype meta is the sum of its
# decks' meta each week (matches _aggregate_by_archetype). Week 5 is "this week"; weeks 1-4 are
# the last-4-week lookback resolve_trend_status averages against -- chosen so the deltas are an
# order of magnitude past both thresholds (TREND_THRESHOLD_DECK=0.5, TREND_THRESHOLD_ARCHETYPE=0.2),
# leaving no ambiguity in the expected direction.
ARCHETYPE_BY_DECK = {
    "DeckA": "Aggro", "DeckB": "Aggro",
    "DeckC": "Control", "DeckD": "Control",
    "DeckE": "Combo", "DeckF": "Combo",
}
WEEKLY_META = {
    1: {"DeckA": 3.0, "DeckB": 2.0, "DeckC": 12.0, "DeckD": 8.0, "DeckE": 6.0, "DeckF": 4.0},
    2: {"DeckA": 3.6, "DeckB": 2.4, "DeckC": 10.8, "DeckD": 7.2, "DeckE": 6.03, "DeckF": 4.02},
    3: {"DeckA": 4.2, "DeckB": 2.8, "DeckC": 9.6, "DeckD": 6.4, "DeckE": 5.97, "DeckF": 3.98},
    4: {"DeckA": 4.8, "DeckB": 3.2, "DeckC": 8.4, "DeckD": 5.6, "DeckE": 6.012, "DeckF": 4.008},
    5: {"DeckA": 7.2, "DeckB": 4.8, "DeckC": 4.8, "DeckD": 3.2, "DeckE": 6.0, "DeckF": 4.0},
}
# Aggro:   avg(3,3.6,4.2,4.8)=3.9+2.6=6.5   -> 12.0  delta=+5.5  -> Rising
# Control: avg(12,10.8,9.6,8.4)+avg(8,7.2,6.4,5.6)=10.2+6.8=17.0 -> 8.0  delta=-9.0  -> Falling
# Combo:   avg(6,6.03,5.97,6.012)+avg(4,4.02,3.98,4.008)=6.003+4.002=10.005 -> 10.0 delta=-0.005 -> Stable
EXPECTED_ARCH_TREND = {"Aggro": "Rising Deck", "Control": "Falling Deck", "Combo": "Stable"}
EXPECTED_DECK_TREND = {
    "DeckA": "Rising Deck", "DeckB": "Rising Deck",
    "DeckC": "Falling Deck", "DeckD": "Falling Deck",
    "DeckE": "Stable", "DeckF": "Stable",
}


def _week_frame(week: int) -> pd.DataFrame:
    meta = WEEKLY_META[week]
    decks = list(meta.keys())
    return pd.DataFrame({
        "Deck": decks,
        "Meta": [meta[d] for d in decks],
        "Winrate": [0.5] * len(decks),
        "Archetype": [ARCHETYPE_BY_DECK[d] for d in decks],
    })


def _run_all_weeks(tmp_dir: Path) -> tuple[Path, Path, statistics_engine.StatisticsRunResult]:
    history_csv = tmp_dir / "metagame_history_test.csv"
    output_dir = tmp_dir / "stats_out"
    result = None
    for week in sorted(WEEKLY_META):
        input_excel = tmp_dir / f"metagame_input_W{week:02d}.xlsx"
        _week_frame(week).to_excel(input_excel, index=False)
        result = run_statistics(
            input_excel=input_excel,
            output_dir=output_dir,
            history_csv=history_csv,
            weeks_back=12,
            output_profile="full",
            week_index=week,
        )
    assert result is not None
    return history_csv, output_dir, result


def test_archetype_trend_has_variance_and_matches_expected_directions() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="trend_regress_"))
    try:
        history_csv, output_dir, result = _run_all_weeks(tmp_dir)

        arch_path = output_dir / "deck_analysis_ARCHETYPE_W05.xlsx"
        deck_path = output_dir / "deck_analysis_W05.xlsx"
        assert arch_path in result.files, f"{arch_path.name} missing from result.files"
        assert deck_path in result.files, f"{deck_path.name} missing from result.files"
        df_arch = pd.read_excel(arch_path)

        # (a) the regression this guards: the column must not be flat.
        assert df_arch["Trend Label"].nunique() > 1, (
            f"Archetype Trend Label column is flat: {set(df_arch['Trend Label'])}"
        )
        for archetype, expected in EXPECTED_ARCH_TREND.items():
            actual = df_arch.loc[df_arch["Archetype"] == archetype, "Trend Label"].iloc[0]
            assert actual == expected, f"{archetype}: expected {expected!r}, got {actual!r}"

        df_deck = pd.read_excel(deck_path)
        for deck, expected in EXPECTED_DECK_TREND.items():
            actual = df_deck.loc[df_deck["Deck"] == deck, "Trend Label"].iloc[0]
            assert actual == expected, f"{deck}: expected {expected!r}, got {actual!r}"

        # (b) the PNG legend must show the identical direction for every key also in the table --
        # rebuild the exact df_history/df_current _create_trend_chart itself would have used for
        # week 5, and read off the legend status it actually produced (not a re-derivation).
        full_history = pd.read_csv(history_csv)
        performance_colors, trend_colors, deck_colors, legend_colors = statistics_engine._build_style(
            palette_name="classic", deck_colors=None, legend_colors=None,
        )
        week5_decks = full_history[(full_history["WeekIndex"] == 5) & (full_history["Level"] == "Deck")].copy()
        week5_decks["Deck Display Name"] = week5_decks["Deck"]
        _, deck_legend_status = statistics_engine._create_trend_chart(
            full_history, week5_decks, 12, 5, "test", "Deck", tmp_dir, trend_colors, deck_colors, legend_colors,
        )
        week5_arch = full_history[(full_history["WeekIndex"] == 5) & (full_history["Level"] == "Archetype")].copy()
        week5_arch["Deck Display Name"] = week5_arch["Deck"]
        _, arch_legend_status = statistics_engine._create_trend_chart(
            full_history, week5_arch, 12, 5, "test", "Archetype", tmp_dir, trend_colors, deck_colors, legend_colors,
        )

        for deck, expected in EXPECTED_DECK_TREND.items():
            assert deck_legend_status.get(deck) == expected, (
                f"legend for {deck}: expected {expected!r}, got {deck_legend_status.get(deck)!r}"
            )
            table_value = df_deck.loc[df_deck["Deck"] == deck, "Trend Label"].iloc[0]
            assert deck_legend_status[deck] == table_value, (
                f"legend/table disagree for Deck {deck!r}: {deck_legend_status[deck]!r} vs {table_value!r}"
            )
        for archetype, expected in EXPECTED_ARCH_TREND.items():
            assert arch_legend_status.get(archetype) == expected, (
                f"legend for {archetype}: expected {expected!r}, got {arch_legend_status.get(archetype)!r}"
            )
            table_value = df_arch.loc[df_arch["Archetype"] == archetype, "Trend Label"].iloc[0]
            assert arch_legend_status[archetype] == table_value, (
                f"legend/table disagree for Archetype {archetype!r}: {arch_legend_status[archetype]!r} vs {table_value!r}"
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_flat_archetype_trend_is_rejected() -> None:
    """Guard (a): if the flat-"Stable" bug ever comes back (by any mechanism), run_statistics
    must raise instead of writing the file. Simulated here by forcing resolve_trend_status to
    always return "Stable", reproducing the historical symptom without touching the guard itself.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="trend_regress_flat_"))
    original = statistics_engine.resolve_trend_status
    statistics_engine.resolve_trend_status = lambda *a, **k: "Stable"  # type: ignore[assignment]
    try:
        raised = None
        try:
            _run_all_weeks(tmp_dir)
        except RuntimeError as exc:
            raised = exc
        assert raised is not None, "expected RuntimeError for a flat Archetype Trend Label column, none raised"
        assert "flat-column regression" in str(raised), f"unexpected error message: {raised}"
    finally:
        statistics_engine.resolve_trend_status = original
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_legend_table_mismatch_is_rejected() -> None:
    """Guard (b): if the PNG legend and the Trend Label column ever disagree for the same key
    again (e.g. someone reintroduces a second trend definition at one call site only),
    run_statistics must raise instead of shipping the contradiction. Simulated by tampering with
    one entry of the status dict _create_trend_chart returns, independent of resolve_trend_status
    itself (which stays real and correct here) -- so this test cannot pass by coincidence with
    guard (a)'s check.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="trend_regress_mismatch_"))
    original = statistics_engine._create_trend_chart

    def tampered(*args, **kwargs):
        path, status = original(*args, **kwargs)
        chart_type = args[5] if len(args) > 5 else kwargs.get("chart_type")
        if chart_type == "Archetype" and status:
            key = next(iter(status))
            status = dict(status)
            status[key] = "Falling Deck" if status[key] != "Falling Deck" else "Rising Deck"
        return path, status

    statistics_engine._create_trend_chart = tampered  # type: ignore[assignment]
    try:
        raised = None
        try:
            _run_all_weeks(tmp_dir)
        except RuntimeError as exc:
            raised = exc
        assert raised is not None, "expected RuntimeError for a legend/table mismatch, none raised"
        assert "disagree" in str(raised), f"unexpected error message: {raised}"
    finally:
        statistics_engine._create_trend_chart = original
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_archetype_trend_has_variance_and_matches_expected_directions()
    print("OK: archetype trend variance + legend/table match")
    test_flat_archetype_trend_is_rejected()
    print("OK: flat-archetype-trend guard rejects the regression")
    test_legend_table_mismatch_is_rejected()
    print("OK: legend/table-mismatch guard rejects the regression")
