"""Pilot moves week to week (order of 2026-11-18): sheets pilot_moves / pilot_flows and the decks of 10_pilot_moves.png."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import deckdb_input as di  # noqa: E402
import deckdb_pilot_moves as pm  # noqa: E402

W46_EXPORT = Path("E:/github/DeckDB/data/exports/assignments_c4310b586bc1a4d2_2026-11-15.csv")
COLS = ["pilots_prev", "pilots", "stayed", "moved_out", "left_top32", "moved_in", "new_top32"]


def _rows(day: str, lists):
    """lists: (player, deck, archetype); a deck None = a list without a deck (Rogue & unclassified)."""
    return pd.DataFrame([{"event_date": day, "event_id": 1, "player": p,
                          "material_deck": d or di.NO_DECK_LABEL, "material_archetype": a or di.NO_DECK_LABEL}
                         for p, d, a in lists])


def _row(t: pd.DataFrame, level: str, name: str) -> dict:
    return t[(t["level"] == level) & (t["name"] == name)].iloc[0].to_dict()


def test_definitions_multi_deck_pilot_and_year_boundary():
    # 2026-W53 (Mon 2026-12-28) -> 2027-W01 (Mon 2027-01-04)
    prev = _rows("2026-12-29", [("a", "Zoo", "Aggro"), ("b", "Zoo", "Aggro"), ("c", "Zoo", "Aggro"),
                                ("d", "Tron", "Ramp"), ("e", None, None), ("a", "Tron", "Ramp")])
    cur = _rows("2027-01-05", [("a", "Zoo", "Aggro"), ("b", "Burn", "Aggro"), ("b", "Tron", "Ramp"),
                               ("f", "Zoo", "Aggro"), ("e", "Burn", "Aggro")])
    mv, fl = pm.pilot_moves(prev, cur)
    assert set(mv["iso_week"]) == {"2027-W01"} and set(mv["prev_week"]) == {"2026-W53"}
    z = _row(mv, "deck", "Zoo")
    # Zoo: a stays, b moved out (plays Burn + Tron), c left the Top 32, f new
    assert [z[c] for c in COLS] == [3, 2, 1, 1, 1, 0, 1]
    assert z["top_out"] == "Burn 1; Tron 1" and z["top_in"] == ""
    t = _row(mv, "deck", "Tron")
    # Tron: a dropped it (still plays Zoo: moved_out), d left; b took it up from Zoo
    assert [t[c] for c in COLS] == [2, 1, 0, 1, 1, 1, 0]
    assert t["top_in"] == "Zoo 1" and t["top_out"] == ""  # a dropped Tron and took up nothing: no pair
    r = _row(mv, "deck", di.NO_DECK_LABEL)
    assert [r[c] for c in COLS] == [1, 0, 0, 1, 0, 0, 0] and r["top_out"] == "Burn 1"
    assert r["archetype"] == di.NO_DECK_LABEL
    pairs = {(x.level, x._3, x.to): x.pilots for x in fl.itertuples()}
    assert pairs[("deck", "Zoo", "Burn")] == 1 and pairs[("deck", "Zoo", "Tron")] == 1
    assert pairs[("deck", di.NO_DECK_LABEL, "Burn")] == 1
    # archetype level: b played Aggro in both weeks and took up Ramp: no Aggro -> Ramp pair (Aggro not dropped)
    ag = _row(mv, "archetype", "Aggro")
    assert [ag[c] for c in COLS] == [3, 4, 2, 0, 1, 1, 1] and ag["top_in"] == f"{di.NO_DECK_LABEL} 1"
    assert ("archetype", "Aggro", "Ramp") not in pairs


def test_check_refuses_a_broken_table():
    prev = _rows("2026-11-03", [("a", "Zoo", "Aggro"), ("b", "Tron", "Ramp")])
    cur = _rows("2026-11-10", [("a", "Tron", "Ramp"), ("c", "Zoo", "Aggro")])
    mv, fl = pm.pilot_moves(prev, cur)
    wm = pd.DataFrame([{"iso_week": "2026-W46", "level": lv, "name": n, "pilots": 1}
                       for lv, names in (("deck", ("Zoo", "Tron")), ("archetype", ("Aggro", "Ramp"))) for n in names])
    pm.check_pilot_moves(mv, fl, wm, prev, cur)
    bad = mv.copy()
    bad.loc[0, "stayed"] += 1
    with pytest.raises(di.DeckdbInputError, match="rozjazd sum"):
        pm.check_pilot_moves(bad, fl, wm, prev, cur)
    wm2 = wm.copy()
    wm2.loc[0, "pilots"] = 2
    with pytest.raises(di.DeckdbInputError, match="weekly_metrics"):
        pm.check_pilot_moves(mv, fl, wm2, prev, cur)
    fl2 = fl.copy()
    fl2.loc[0, "pilots"] = 2
    with pytest.raises(di.DeckdbInputError):
        pm.check_pilot_moves(mv, fl2, wm, prev, cur)


@pytest.mark.skipif(not W46_EXPORT.exists(), reason="DeckDB export of 2026-11-15 not on this machine")
def test_w46_control():
    """The W46 control of the order (export assignments_c4310b586bc1a4d2_2026-11-15.csv, 2026-W46 vs 2026-W45); the
    chart has 12 decks (top 10 + Neobrand + Dimir Midrange; the order's '11' was corrected on 2026-11-18)."""
    exp = di.load_export(W46_EXPORT)
    prev = di.window_rows(exp, date(2026, 11, 2), date(2026, 11, 8))
    cur = di.window_rows(exp, date(2026, 11, 9), date(2026, 11, 15))
    assert (prev["player"].nunique(), cur["player"].nunique(),
            len(set(prev["player"]) & set(cur["player"]))) == (254, 229, 120)
    mv, fl = pm.pilot_moves(prev, cur)
    expect = {
        ("deck", "Energy"): [22, 14, 5, 7, 10, 2, 7], ("deck", "Neobrand"): [7, 11, 1, 2, 4, 5, 5],
        ("deck", "Prowess"): [13, 19, 4, 5, 4, 3, 12], ("deck", "Tron"): [13, 12, 5, 6, 2, 0, 7],
        ("deck", "Broodscale Combo"): [31, 27, 14, 3, 14, 4, 9], ("deck", "Moonshadow"): [32, 29, 10, 5, 17, 5, 14],
        ("deck", "Prison"): [18, 16, 8, 2, 8, 4, 4],
        ("archetype", "Aggro"): [88, 81, 33, 12, 43, 6, 42], ("archetype", "Combo"): [86, 78, 39, 6, 41, 6, 33],
        ("archetype", "Midrange"): [33, 21, 6, 7, 20, 5, 10], ("archetype", "Ramp"): [19, 12, 5, 7, 7, 0, 7],
    }
    for (level, name), want in expect.items():
        r = _row(mv, level, name)
        assert [int(r[c]) for c in COLS] == want, (level, name)
    assert _row(mv, "deck", "Energy")["top_out"] == "Neobrand 3; Prison 3; Dimir Midrange 1"
    assert _row(mv, "deck", "Energy")["top_in"] == "Moonshadow 1; Prison 1"
    assert _row(mv, "deck", "Neobrand")["top_in"] == "Energy 3; Broodscale Combo 1; Control 1"
    assert _row(mv, "archetype", "Aggro")["top_out"] == "Combo 3; Control 2; Graveyard 2"
    wm = di.weekly_metrics(exp, date(2026, 11, 2), date(2026, 11, 15), draws=20)
    pm.check_pilot_moves(mv, fl, wm, prev, cur)
    decks = di.week_share_table(cur, prev, "material_deck", "Deck", top_n=di.WEEK_TOP_N)
    order = list(decks[~decks["Deck"].isin((di.OTHER_DECKS_LABEL, di.NO_DECK_LABEL))]
                 .sort_values(["Share_Top32_pct", "Deck"], ascending=[False, True])["Deck"])
    shown = pm.chart_decks(mv, order)
    assert len(shown) == 12 and shown[:10] == order and shown[10:] == ["Neobrand", "Dimir Midrange"]
