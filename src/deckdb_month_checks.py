"""Monthly package charts m03-m14 (order of 2026-11-03 b): the input table of every chart derived only from the sheets of
the package's data.xlsx, and the check of what each chart drew (deckdb_presentation.DRAWN, read back from the
matplotlib artists) against those sheets: labels and order, bar lengths / line points / matrix cells / table rows,
colours (archetype colours, fixed deck colours, 'Rogue & unclassified' grey) and the emphasis of a change (bold green /
red only for a BH rejection). Used twice: by the pipeline right after drawing (in-memory sheets) and by the week runner
on the files (sheets read back from data.xlsx, the drawn record from <month>.charts.json). A mismatch raises
ChartMismatch; nothing is fixed up."""
from __future__ import annotations

import math
from typing import Dict, List

import pandas as pd

try:
    import deckdb_input as di
    import deckdb_presentation as dp
except ModuleNotFoundError:
    from . import deckdb_input as di
    from . import deckdb_presentation as dp

NO_DECK = di.NO_DECK_LABEL
TOP_N = di.WEEK_TOP_N
MONTH_CHARTS = ("m03_archetypes_share.png", "m04_archetypes_trend.png", "m05_decks_share.png", "m06_decks_trend.png",
                "m07_weeks_in_month.png", "m08_month_matrix_decks.png", "m09_month_matrix_archetypes.png",
                "m10_top8_trophies.png", "m11_trophy_timeline.png", "m12_pilots.png", "m13_new_cards.png",
                "m14_variants.png")
EPS = 1e-9


class ChartMismatch(Exception):
    pass


# --- inputs of the charts, from the sheets only ----------------------------------------------------------------------

def _num(df: pd.DataFrame, cols) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        df[c] = pd.to_numeric(df[c])
    return df


def month_rows(mm: pd.DataFrame, month: str, level: str) -> pd.DataFrame:
    t = mm[(mm["month"].astype(str) == month) & (mm["level"] == level)]
    return _num(t, ["lists", "share_pct", "events_with_list", "month_events", "top8", "top8_events", "trophies",
                    "prev_month_lists", "prev_share_pct", "change_pp", "change_beyond", "tracked"])


def top_decks(mm: pd.DataFrame, month: str) -> List[str]:
    """The ten decks with the most lists of the month (ties by name; 'Rogue & unclassified' is not a deck)."""
    t = month_rows(mm, month, di.LEVEL_DECK)
    t = t[(t["lists"] > 0) & (t["name"] != NO_DECK)]
    return sorted(t["name"], key=lambda d: (-int(t.loc[t["name"] == d, "lists"].iloc[0]), d))[:TOP_N]


def _beyond(v) -> object:
    return pd.NA if pd.isna(v) else int(v)


def share_input(mm: pd.DataFrame, month: str, level: str) -> pd.DataFrame:
    """Rows of m03 (archetypes: every archetype with a list in M or M-1) or m05 (decks: the top 10, one pooled 'other
    decks' row, 'Rogue & unclassified' with its test from the archetype family), columns of share_chart."""
    key = "Archetype" if level == di.LEVEL_ARCHETYPE else "Deck"
    t = month_rows(mm, month, level)
    a = month_rows(mm, month, di.LEVEL_ARCHETYPE).set_index("name")

    def row(r, label=None) -> dict:
        return {key: label or r["name"], "Archetype": r["archetype"], "Lists": int(r["lists"]),
                "Share_Top32_pct": float(r["share_pct"]), "Events_with_list": int(r["events_with_list"]),
                "Week_events": int(r["month_events"]), "Change_pp": float(r["change_pp"]),
                "change_beyond": _beyond(r["change_beyond"])}

    if level == di.LEVEL_ARCHETYPE:
        keep = t[(t["lists"] > 0) | (t["prev_month_lists"].fillna(0) > 0)]
        return pd.DataFrame([row(r) for _, r in keep.iterrows()])
    top = top_decks(mm, month)
    out = [row(t[t["name"] == d].iloc[0]) for d in top]
    rest = t[(~t["name"].isin(top)) & (t["name"] != NO_DECK) & (t["lists"] > 0)]
    if len(rest):
        out.append({key: di.OTHER_DECKS_LABEL, "Archetype": "", "Lists": int(rest["lists"].sum()),
                    "Share_Top32_pct": float(rest["share_pct"].sum()), "Events_with_list": 0,
                    "Week_events": int(t["month_events"].iloc[0]), "Change_pp": math.nan,
                    "change_beyond": pd.NA, "Decks_in_row": len(rest)})
    rogue = t[t["name"] == NO_DECK]
    if len(rogue) and (int(rogue["lists"].iloc[0]) or (rogue["prev_month_lists"].fillna(0) > 0).any()):
        r = row(rogue.iloc[0])
        r["change_beyond"] = _beyond(a.loc[NO_DECK, "change_beyond"]) if NO_DECK in a.index else pd.NA
        out.append(r)
    return pd.DataFrame(out)


def matrix_input(mm: pd.DataFrame, month: str, level: str) -> pd.DataFrame:
    key = "Archetype" if level == di.LEVEL_ARCHETYPE else "Deck"
    t = month_rows(mm, month, level)
    t = t[t["lists"] > 0]
    return pd.DataFrame({key: t["name"], "Lists": t["lists"].astype(int),
                         "Events_with_list": t["events_with_list"].astype(int), "Top8_lists": t["top8"].astype(int),
                         "Events_with_Top8": t["top8_events"].astype(int), "Trophies": t["trophies"].astype(int)})


def trend_input(sh: pd.DataFrame, key: str) -> pd.DataFrame:
    return _num(sh, ["Share_Top32_pct"]).rename(columns={"Month": "Period"})[["Period", key, "Share_Top32_pct"]]


def weeks_input(wim: pd.DataFrame) -> pd.DataFrame:
    w = _num(wim, ["Share_Top32_pct", "Partial"])
    return w.rename(columns={"ISO_week": "Period"})[["Period", "Partial", "Deck", "Share_Top32_pct"]]


def top8_input(top8: pd.DataFrame) -> pd.DataFrame:
    t = _num(top8, ["Place", "Winner"])
    t["Variant"] = t["Variant"].fillna("").astype(str)
    t["EventID"], t["Date"] = t["EventID"].astype(str), t["Date"].astype(str)
    return t


def new_cards_input(nc: pd.DataFrame) -> pd.DataFrame:
    if nc.empty or "Reported" not in nc.columns:
        return pd.DataFrame(columns=["Deck", "Card", "Pilots_with_card", "Zone"])
    t = _num(nc, ["Reported", "Mana_only_land", "Pilots_with_card"])
    return t[(t["Reported"] == 1) & (t["Mana_only_land"] == 0)].reset_index(drop=True)


def variant_label(deck: str, variant: str) -> str:
    """A variant row's bar label: the export's label ('Zoo: Phlage', 'Zoo: other'); a list without a variant
    '<deck> (no variant)'."""
    return f"{deck} {variant}" if variant == di.NO_VARIANT_LABEL else variant


def variants_input(v: pd.DataFrame) -> pd.DataFrame:
    if v.empty or "Variant" not in v.columns:
        return pd.DataFrame(columns=["Deck", "Variant", "Lists", "Top8", "Trophies"])
    t = _num(v, ["Lists", "Top8", "Trophies"])
    t["Variant"] = [variant_label(d, x) for d, x in zip(t["Deck"], t["Variant"])]
    return t


# --- the check ---------------------------------------------------------------------------------------------------

def _eq(what: str, got, want) -> None:
    if got != want:
        raise ChartMismatch(f"{what}: wykres {got!r}, arkusz {want!r}")


def _close(what: str, got: float, want: float) -> None:
    if not (abs(float(got) - float(want)) <= EPS or (math.isnan(float(got)) and math.isnan(float(want)))):
        raise ChartMismatch(f"{what}: wykres {got}, arkusz {want}")


def _low(c: str) -> str:
    return c.lower()


def _check_share(name: str, d: dict, t: pd.DataFrame, key: str, colours: dp.Colours) -> int:
    named = t[~t[key].isin(dp.POOLED)].sort_values(["Share_Top32_pct", key], ascending=[False, True])
    exp = pd.concat([named, t[t[key] == NO_DECK]])
    _eq(f"{name} wiersze", [r["label"] for r in d["rows"]], list(exp[key]))
    for r, (_, e) in zip(d["rows"], exp.iterrows()):
        _close(f"{name} {r['label']} udział", r["value"], e["Share_Top32_pct"])
        arch = NO_DECK if e[key] == NO_DECK else (e["Archetype"] if key == "Deck" else e[key])
        _eq(f"{name} {r['label']} kolor", _low(r["colour"]), _low(colours.of_archetype(arch)))
        head = f"{e['Share_Top32_pct']:.1f}% · {e['Events_with_list']}/{e['Week_events']} events · "
        _eq(f"{name} {r['label']} etykieta", [p["text"] for p in r["parts"]], [head, dp._pp(e["Change_pp"])])
        beyond = pd.notna(e["change_beyond"]) and int(e["change_beyond"]) == 1
        style = (("bold", _low(dp.C_UP if e["Change_pp"] > 0 else dp.C_DOWN)) if beyond
                 else ("normal", _low(dp.C_SOFT)))
        _eq(f"{name} {r['label']} wyróżnienie zmiany (BH)", (r["parts"][1]["weight"], _low(r["parts"][1]["colour"])),
            style)
    other = t[t[key] == di.OTHER_DECKS_LABEL]
    want = (f"{di.OTHER_DECKS_LABEL}: {other.iloc[0]['Share_Top32_pct']:.1f}% of lists across "
            f"{int(other.iloc[0]['Decks_in_row'])} decks") if len(other) else ""
    _eq(f"{name} linia other decks", d.get("other", ""), want)
    return int(sum(1 for _, e in exp.iterrows() if pd.notna(e["change_beyond"]) and int(e["change_beyond"]) == 1))


def _check_lines(name: str, d: dict, t: pd.DataFrame, key: str, labels: List[str], colour_of) -> None:
    periods = sorted(t["Period"].unique())
    _eq(f"{name} oś", d["x"], [str(p) for p in periods])
    _eq(f"{name} linie", [ln["label"] for ln in d["lines"]], list(labels))
    piv = t.pivot_table(index="Period", columns=key, values="Share_Top32_pct", aggfunc="sum").reindex(periods)
    seen = set()
    for ln in d["lines"]:
        want = piv[ln["label"]].fillna(0.0).tolist() if ln["label"] in piv.columns else [0.0] * len(periods)
        for i, (g, w) in enumerate(zip(ln["y"], want)):
            _close(f"{name} {ln['label']} {periods[i]}", g, w)
        c = _low(colour_of(ln["label"]))
        _eq(f"{name} {ln['label']} kolor", _low(ln["colour"]), c)
        _eq(f"{name} {ln['label']} styl", ln["style"], "--" if c in seen else "-")
        seen.add(c)


def _check_matrix(name: str, d: dict, t: pd.DataFrame, key: str, top_n) -> None:
    named = t[t[key] != NO_DECK].sort_values(["Lists", key], ascending=[False, True])
    if top_n is not None:
        named = named.head(top_n)
    exp = pd.concat([named, t[t[key] == NO_DECK]])
    _eq(f"{name} wiersze", [r["label"] for r in d["rows"]], list(exp[key]))
    _eq(f"{name} kolumny", d["cols"], [c for c, _h in dp.MATRIX_COLS])
    for r, (_, e) in zip(d["rows"], exp.iterrows()):
        _eq(f"{name} {r['label']}", r["cells"], [int(e[c]) for c, _h in dp.MATRIX_COLS])


def check_month_charts(drawn: Dict[str, dict], sheets: Dict[str, pd.DataFrame], month: str,
                       colours: dp.Colours) -> Dict[str, object]:
    """Every chart m03-m14 against the sheets it is drawn from; returns facts for the report."""
    missing = [n for n in MONTH_CHARTS if n not in drawn]
    if missing:
        raise ChartMismatch(f"brak zapisu rysunku dla {missing}")
    mm = sheets["monthly_metrics"]
    facts: Dict[str, object] = {}
    arch_t = share_input(mm, month, di.LEVEL_ARCHETYPE)
    facts["beyond_archetypes"] = _check_share("m03", drawn["m03_archetypes_share.png"], arch_t, "Archetype", colours)
    deck_t = share_input(mm, month, di.LEVEL_DECK)
    facts["beyond_decks_shown"] = _check_share("m05", drawn["m05_decks_share.png"], deck_t, "Deck", colours)
    arch_order = list(drawn["m03_archetypes_share.png"]["rows"][i]["label"]
                      for i in range(len(drawn["m03_archetypes_share.png"]["rows"])))
    _check_lines("m04", drawn["m04_archetypes_trend.png"], trend_input(sheets["share_history_months_archetypes"],
                                                                      "Archetype"), "Archetype", arch_order,
                 colours.of_archetype)
    top = top_decks(mm, month)
    _check_lines("m06", drawn["m06_decks_trend.png"], trend_input(sheets["share_history_months_decks"], "Deck"), "Deck",
                 top, colours.of_deck)
    _check_lines("m07", drawn["m07_weeks_in_month.png"], weeks_input(sheets["weeks_in_month"]), "Deck", top,
                 colours.of_deck)
    _check_matrix("m08", drawn["m08_month_matrix_decks.png"], matrix_input(mm, month, di.LEVEL_DECK), "Deck",
                  dp.TOP_N_MATRIX)
    _check_matrix("m09", drawn["m09_month_matrix_archetypes.png"], matrix_input(mm, month, di.LEVEL_ARCHETYPE),
                  "Archetype", None)
    # m10: Top 8 lists and trophies per deck
    t8 = top8_input(sheets["top8_month"])
    g = t8.groupby("Deck").agg(Top8=("Place", "size"), Trophies=("Winner", "sum"), Archetype=("Archetype", "first"))
    d10 = drawn["m10_top8_trophies.png"]
    tail = d10.get("tail", [])
    _eq("m10 talie", sorted([r["label"] for r in d10["rows"]] + list(d10.get("single", []))
                            + [x["label"] for x in tail]), sorted(g.index))
    named = g[g.index != NO_DECK].reset_index().sort_values(["Top8", "Trophies", "Deck"], ascending=[False, False, True])
    _eq("m10 kolejność słupków i linii", [r["label"] for r in d10["rows"] if r["label"] != NO_DECK]
        + [x["label"] for x in tail], list(named["Deck"]))
    for x in tail:
        _eq(f"m10 {x['label']} (linia pod wykresem)", (x["top8"], x["trophies"]),
            (int(g.loc[x["label"], "Top8"]), int(g.loc[x["label"], "Trophies"])))
    for r in d10["rows"]:
        e = g.loc[r["label"]]
        _close(f"m10 {r['label']} Top 8", r["value"], e["Top8"])
        _eq(f"m10 {r['label']} trofea", [p["text"] for p in r["parts"]],
            [str(int(e["Top8"]))] + (["★", str(int(e["Trophies"]))] if e["Trophies"] else []))
        _eq(f"m10 {r['label']} kolor", _low(r["colour"]),
            _low(colours.of_archetype(NO_DECK if r["label"] == NO_DECK else e["Archetype"])))
    for dname in d10.get("single", []):
        _eq(f"m10 {dname} (linia pod wykresem)", (int(g.loc[dname, "Top8"]), int(g.loc[dname, "Trophies"])), (1, 0))
    # m11: one point per event of the month, the winner of top8_month
    win = t8[t8["Winner"] == 1].sort_values(["Date", "EventID"]).reset_index(drop=True)
    pts = drawn["m11_trophy_timeline.png"]["points"]
    events = int(month_rows(mm, month, di.LEVEL_DECK)["month_events"].iloc[0])
    _eq("m11 liczba punktów = eventy miesiąca", len(pts), events)
    _eq("m11 eventy", [p["event_id"] for p in pts], list(win["EventID"]))
    for p, (_, w) in zip(pts, win.iterrows()):
        _eq(f"m11 {p['event_id']} zwycięzca", p["label"], w["Variant"] or w["Deck"])
        _eq(f"m11 {p['event_id']} kolor", _low(p["colour"]),
            _low(colours.of_archetype(NO_DECK if w["Deck"] == NO_DECK else w["Archetype"])))
    _eq("m11 trofea na talię", drawn["m11_trophy_timeline.png"]["trophies"],
        {str(k): int(v) for k, v in win["Deck"].value_counts().items()})
    facts["trophies"] = {str(k): int(v) for k, v in win["Deck"].value_counts().items()}
    # m12: pilots and concentration
    pm = _num(sheets["pilots_month"], ["Pilots", "Lists", "Concentrated", "Top_pilot_share_pct"])
    d12 = drawn["m12_pilots.png"]
    _eq("m12 talie", sorted(r["label"] for r in d12["rows"]), sorted(pm["Deck"]))
    _eq("m12 talie = top 10 miesiąca", sorted(pm["Deck"]), sorted(top))
    for r in d12["rows"]:
        e = pm[pm["Deck"] == r["label"]].iloc[0]
        _close(f"m12 {r['label']} piloci", r["value"], e["Pilots"])
        _eq(f"m12 {r['label']} kolor", _low(r["colour"]), _low(colours.of_archetype(e["Archetype"])))
        _eq(f"m12 {r['label']} koncentracja", len(r["parts"]) > 1, bool(int(e["Concentrated"])))
    facts["concentrated"] = list(pm.loc[pm["Concentrated"] == 1, "Deck"])
    # m13: new cards table
    nc = new_cards_input(sheets["new_cards_month"]).head(dp.TOP_N_NEW_CARDS)
    _eq("m13 wiersze", drawn["m13_new_cards.png"]["rows"],
        [[r.Deck, r.Card, f"{int(r.Pilots_with_card)}", "SB" if r.Zone != "main" else ""] for r in nc.itertuples()])
    # m14: variants
    v = variants_input(sheets["variants_month"])
    d14 = drawn["m14_variants.png"]
    for r, deck in zip(d14["rows"], d14.get("decks", [])):
        e = v[(v["Deck"] == deck) & (v["Variant"] == r["label"])]
        if len(e) != 1:
            raise ChartMismatch(f"m14 {deck} / {r['label']}: brak wiersza variants_month")
        e = e.iloc[0]
        _close(f"m14 {r['label']} listy", r["value"], e["Lists"])
        _eq(f"m14 {r['label']} etykieta", [p["text"] for p in r["parts"]],
            [dp.variant_bar_text(e["Lists"], e["Top8"])])
        _eq(f"m14 {r['label']} kolor", _low(r["colour"]), _low(colours.of_deck(deck)))
    shown_decks = list(dict.fromkeys(d14.get("decks", [])))
    tot = v.groupby("Deck")["Lists"].sum()
    want = sorted(tot.index, key=lambda x: (-int(tot[x]), x))[:len(shown_decks)]
    _eq("m14 talie = talie z najwięcej list", shown_decks, want)
    for deck in shown_decks:
        _eq(f"m14 {deck} wszystkie warianty", sum(1 for x in d14["decks"] if x == deck), int((v["Deck"] == deck).sum()))
    return facts
