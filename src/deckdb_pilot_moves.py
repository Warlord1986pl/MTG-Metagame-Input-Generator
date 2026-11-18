"""Pilot moves week to week (order of 2026-11-18): sheets pilot_moves and pilot_flows of the week folder and the chart
10_pilot_moves.png. Descriptive counts, no test.

Population: the Challenge Top 32 lists of the DeckDB export (column ``player`` = anonymous pilot); a list without a
deck (status other than assigned) counts as the one deck and the one archetype 'Rogue & unclassified' (material_deck /
material_archetype). Week w = the folder's week, w-1 = the previous ISO week (across the year boundary too), both
from the same export and rules. For every level (deck, archetype) and label X:

  P(w)      pilots with >= 1 list in week w
  S_X(w)    pilots with >= 1 list of X in week w (a pilot with several decks in a week counts in each)
  pilots_prev = |S_X(w-1)|, pilots = |S_X(w)|, stayed = |S_X(w-1) & S_X(w)|
  moved_out  = |(S_X(w-1) - S_X(w)) & P(w)|      left_top32 = |(S_X(w-1) - S_X(w)) - P(w)|
  moved_in   = |(S_X(w) - S_X(w-1)) & P(w-1)|    new_top32  = |(S_X(w) - S_X(w-1)) - P(w-1)|
  flow X -> Y: a pilot of P(w-1) & P(w) with X in decks_p(w-1) - decks_p(w) and Y in decks_p(w) - decks_p(w-1);
               one pilot can give several pairs
  top_out / top_in: at most three biggest directions, 'Deck n; Deck n' (n descending, ties alphabetical)

Every inconsistency (the identities above, pilots vs weekly_metrics, symmetry of the flows) is a DeckdbInputError.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

import pandas as pd

try:
    import deckdb_input as di
except ModuleNotFoundError:
    from . import deckdb_input as di

LEVELS = ((di.LEVEL_DECK, "material_deck"), (di.LEVEL_ARCHETYPE, "material_archetype"))
MOVES_COLUMNS = ["iso_week", "prev_week", "level", "name", "archetype", "pilots_prev", "pilots", "stayed", "moved_out",
                 "left_top32", "moved_in", "new_top32", "top_out", "top_in"]
FLOWS_COLUMNS = ["iso_week", "level", "from", "to", "pilots"]
TOP_DIRECTIONS = 3
CHART_TOP_EXTRA = 2  # decks outside the week's top 10 added to the chart (largest moved_in + moved_out)
CHART_EXTRA_MIN = 3

GLOSSARY = [
    ("pilot moves (week)", "per deck and, apart, per archetype: the pilots of the deck in the previous ISO week "
                           "(pilots_prev) and this week (pilots); stayed = in both weeks; moved_out = played the deck "
                           "last week, is in this week's Top 32 without it; left_top32 = played it last week, no list "
                           "in this week's Top 32; moved_in = in last week's Top 32 without the deck, plays it this "
                           "week; new_top32 = plays it this week, no list in last week's Top 32. stayed + moved_out + "
                           "left_top32 = pilots_prev and stayed + moved_in + new_top32 = pilots. A pilot can play "
                           "several decks in a week and counts in each. Counts, not tested."),
    ("pilot flows (week)", "pairs from -> to: a pilot in the Top 32 of both weeks who dropped deck 'from' (played it "
                           "last week, not this week) and took up deck 'to' (this week, not last week); a pilot can "
                           "give several pairs. top_out / top_in of pilot_moves: the three biggest directions out of / "
                           "into the deck, 'Deck n; Deck n' (n descending, ties alphabetical). Counts, not tested."),
]


def _sets(rows: pd.DataFrame, key: str) -> Dict[str, Set[str]]:
    """pilot -> set of labels of the week."""
    return {p: set(g) for p, g in rows.groupby("player")[key]}


def _top(pairs: Dict[str, int]) -> str:
    items = sorted(((n, x) for x, n in pairs.items() if n > 0), key=lambda t: (-t[0], t[1]))[:TOP_DIRECTIONS]
    return "; ".join(f"{x} {n}" for n, x in items)


def pilot_moves(prev_rows: pd.DataFrame, rows: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """(pilot_moves, pilot_flows) of week w (*rows*) against w-1 (*prev_rows*), both levels, recomputed in full."""
    if prev_rows.empty or rows.empty:
        raise di.DeckdbInputError("pilot_moves: tydzień bez list")
    for name, r in (("w-1", prev_rows), ("w", rows)):
        if r["player"].isna().any() or (r["player"].astype(str).str.strip() == "").any():
            raise di.DeckdbInputError(f"pilot_moves: lista bez pilota (player) w tygodniu {name}")
    week = di.iso_week_label(pd.Timestamp(rows["event_date"].min()).date())
    pweek = di.iso_week_label(pd.Timestamp(prev_rows["event_date"].min()).date())
    arch = pd.concat([di.deck_archetype_map(prev_rows), di.deck_archetype_map(rows)])
    arch = arch[~arch.index.duplicated(keep="last")].to_dict()
    moves: List[dict] = []
    flows: List[dict] = []
    for level, key in LEVELS:
        before, after = _sets(prev_rows, key), _sets(rows, key)
        p_prev, p_cur = set(before), set(after)
        pair_n: Dict[Tuple[str, str], int] = {}
        for p in p_prev & p_cur:
            for x in before[p] - after[p]:
                for y in after[p] - before[p]:
                    pair_n[(x, y)] = pair_n.get((x, y), 0) + 1
        labels = sorted(set(prev_rows[key]) | set(rows[key]))
        for x in labels:
            s0 = {p for p, ls in before.items() if x in ls}
            s1 = {p for p, ls in after.items() if x in ls}
            moves.append({
                "iso_week": week, "prev_week": pweek, "level": level, "name": x,
                "archetype": arch.get(x, x) if level == di.LEVEL_DECK else x,
                "pilots_prev": len(s0), "pilots": len(s1), "stayed": len(s0 & s1),
                "moved_out": len((s0 - s1) & p_cur), "left_top32": len((s0 - s1) - p_cur),
                "moved_in": len((s1 - s0) & p_prev), "new_top32": len((s1 - s0) - p_prev),
                "top_out": _top({y: n for (a, y), n in pair_n.items() if a == x}),
                "top_in": _top({a: n for (a, y), n in pair_n.items() if y == x}),
            })
        flows += [{"iso_week": week, "level": level, "from": a, "to": b, "pilots": n}
                  for (a, b), n in pair_n.items()]
    mv = pd.DataFrame(moves, columns=MOVES_COLUMNS)
    fl = pd.DataFrame(flows, columns=FLOWS_COLUMNS)
    if not fl.empty:
        order = {lv: i for i, (lv, _k) in enumerate(LEVELS)}
        fl = (fl.assign(_l=fl["level"].map(order)).sort_values(["_l", "pilots", "from", "to"],
                                                                ascending=[True, False, True, True])
              .drop(columns="_l").reset_index(drop=True))
    return mv, fl


def _parse_top(text: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for part in filter(None, (s.strip() for s in str(text).split(";"))):
        name, n = part.rsplit(" ", 1)
        out[name] = int(n)
    return out


def check_pilot_moves(moves: pd.DataFrame, flows: pd.DataFrame, wm: pd.DataFrame, prev_rows: pd.DataFrame,
                      rows: pd.DataFrame) -> None:
    """The checks of the order: the two identities per row; pilots (and pilots_prev when w-1 is in the format period)
    equal to weekly_metrics on both levels; per label X the sum of pilot_flows over 'to' equals the number of pairs
    from X counted again from the lists (per pilot of both weeks who dropped X: the labels taken up); every direction
    of top_out / top_in equals pilot_flows, and X -> Y in top_out of X has the same count as X in top_in of Y."""
    for r in moves.itertuples():
        if r.stayed + r.moved_out + r.left_top32 != r.pilots_prev or r.stayed + r.moved_in + r.new_top32 != r.pilots:
            raise di.DeckdbInputError(f"pilot_moves {r.iso_week} {r.level} {r.name}: rozjazd sum (stayed + moved_out + "
                                      "left_top32 = pilots_prev, stayed + moved_in + new_top32 = pilots)")
    for col, week_col in (("pilots", "iso_week"), ("pilots_prev", "prev_week")):
        week = moves[week_col].iloc[0]
        w = wm[wm["iso_week"] == week]
        if w.empty:
            if col == "pilots":
                raise di.DeckdbInputError(f"pilot_moves: brak tygodnia {week} w weekly_metrics")
            continue  # w-1 before the format period: weekly_metrics does not have it
        for level in moves["level"].unique():
            ref = w[w["level"] == level].set_index("name")["pilots"].astype(int)
            got = moves[moves["level"] == level].set_index("name")[col].astype(int)
            ref, got = ref[ref > 0].sort_index(), got[got > 0].sort_index()
            if not ref.equals(got):
                diff = sorted(x for x in set(ref.index) | set(got.index) if ref.get(x) != got.get(x))
                raise di.DeckdbInputError(f"pilot_moves {week} {level}: {col} różne od weekly_metrics.pilots "
                                          f"({diff[:5]})")
    for level, key in LEVELS:
        m = moves[moves["level"] == level].set_index("name")
        if m.empty:
            continue
        f = flows[flows["level"] == level] if not flows.empty else pd.DataFrame(columns=FLOWS_COLUMNS)
        before, after = _sets(prev_rows, key), _sets(rows, key)
        pairs: Dict[str, int] = {}
        for p in set(before) & set(after):
            for x in before[p] - after[p]:
                pairs[x] = pairs.get(x, 0) + len(after[p] - before[p])
        out_sum = f.groupby("from")["pilots"].sum()
        for x in m.index:
            if int(out_sum.get(x, 0)) != pairs.get(x, 0):
                raise di.DeckdbInputError(f"pilot_flows {level} {x}: suma po 'to' {int(out_sum.get(x, 0))}, "
                                          f"par z from = {x}: {pairs.get(x, 0)}")
        for x in m.index:
            for y, n in _parse_top(m.at[x, "top_out"]).items():
                got = f[(f["from"] == x) & (f["to"] == y)]["pilots"]
                if len(got) != 1 or int(got.iloc[0]) != n:
                    raise di.DeckdbInputError(f"pilot_moves {level}: top_out {x} -> {y} {n} różne od pilot_flows")
                back = _parse_top(m.at[y, "top_in"]) if y in m.index else {}
                if x in back and back[x] != n:
                    raise di.DeckdbInputError(f"pilot_moves {level}: {x} -> {y} {n} w top_out, {back[x]} w top_in {y}")
            for y, n in _parse_top(m.at[x, "top_in"]).items():
                got = f[(f["from"] == y) & (f["to"] == x)]["pilots"]
                if len(got) != 1 or int(got.iloc[0]) != n:
                    raise di.DeckdbInputError(f"pilot_moves {level}: top_in {y} -> {x} {n} różne od pilot_flows")


def chart_decks(moves: pd.DataFrame, top_named: List[str]) -> List[str]:
    """The decks of 10_pilot_moves.png: the top 10 of the week in the order of 03_decks_share.png (no Rogue &
    unclassified), then at most two other decks with the largest moved_in + moved_out when that sum is >= 3 (ties
    alphabetical)."""
    d = moves[(moves["level"] == di.LEVEL_DECK) & (moves["name"] != di.NO_DECK_LABEL)].copy()
    shown = [x for x in top_named if x != di.NO_DECK_LABEL]
    d = d[~d["name"].isin(shown)]
    d["_m"] = d["moved_in"] + d["moved_out"]
    extra = d[d["_m"] >= CHART_EXTRA_MIN].sort_values(["_m", "name"], ascending=[False, True]).head(CHART_TOP_EXTRA)
    return shown + list(extra["name"])


def speaker_note(moves: pd.DataFrame, flows: pd.DataFrame, decks: List[str], prev_label: str, label: str) -> str:
    """1-3 sentences for notes.md."""
    d = moves[(moves["level"] == di.LEVEL_DECK)].set_index("name")
    shown = d.loc[decks]
    s1 = (f"For each deck: pilots who left it after week {prev_label[-2:]} (to another deck or out of the Top 32) on the "
          f"left, pilots who joined it in week {label[-2:]} (from another deck or new to the Top 32) on the right, and "
          "how many stayed; a pilot can play several decks in a week, so these are counts, not tested.")
    f = flows[flows["level"] == di.LEVEL_DECK] if not flows.empty else flows
    if not f.empty:
        best = f[f["pilots"] == f["pilots"].max()].head(TOP_DIRECTIONS)
        n = int(best["pilots"].iloc[0])
        s2 = (f" The largest direct move{'s' if len(best) > 1 else ''}: "
              + ", ".join(f"{a} to {b}" for a, b in zip(best["from"], best["to"]))
              + f" ({n} pilot{'s' if n != 1 else ''}{' each' if len(best) > 1 else ''}).")
    else:
        s2 = " No pilot switched decks between the two weeks."
    keep = shown.assign(_r=shown["stayed"] / shown["pilots_prev"].where(shown["pilots_prev"] > 0))
    keep = keep[keep["pilots_prev"] >= 5].sort_values(["_r", "pilots_prev"], ascending=[False, False])
    s3 = (f" {keep.index[0]} kept the most of its pilots ({int(keep.iloc[0]['stayed'])} of "
          f"{int(keep.iloc[0]['pilots_prev'])})." if not keep.empty else "")
    return s1 + s2 + s3
