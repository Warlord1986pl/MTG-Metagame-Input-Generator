"""Working ("WORKING") materials of input mode "deckdb" (docs/deckdb_metodologia.md, accepted 2026-10-21).

All materials are in English. Charts are drawn by the legacy chart functions
(challenge_history_engine._chart_compare_presence / _chart_conversion_matrix / _chart_delta_ranking,
statistics_engine._create_trend_chart) through their optional parameters, so the deckdb mode keeps the
legacy palette, layout, fonts, colour scales and number formats; no styling code lives here.

Levels, each its own directory under ``<outputs-base>/deckdb_working/WORKING_<as-of>/``:

  week_<YYYY-Www>                        the last complete ISO week: share of Challenge Top 32, event
                                         presence, Top 8; trend over the last N disjoint ISO weeks
  month_<YYYY-MM>[_incomplete]           the as-of month: the same + trophies, variants by D2
  format_period_<start>_to_<as-of>       Top 8 conversion with the common baseline and the pilot
                                         bootstrap CI, trophies, pilot measures (decks only)
  cards_<M1>_vs_<M2>[_incomplete]        card layer (deckdb_cards.py), when a card export is configured

Every material states the DeckDB rules version, the export's sha256 and data_version (chart footer,
STATUS sheet, CSV columns) and carries the glossary (``Glossary`` sheet, ``GLOSSARY.md``, the terms a
chart uses in its footer). No material carries a loginid or a player id. Winrate is not a measure (K36).
Legacy materials without a counterpart are listed in the ``Omitted`` sheet (OMITTED_MATERIALS).
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    import deckdb_cards as dc
    import deckdb_input as di
    from challenge_history_engine import _chart_compare_presence, _chart_conversion_matrix, _chart_delta_ranking
    from statistics_engine import _build_style, _create_trend_chart
except ModuleNotFoundError:
    from . import deckdb_cards as dc
    from . import deckdb_input as di
    from .challenge_history_engine import _chart_compare_presence, _chart_conversion_matrix, _chart_delta_ranking
    from .statistics_engine import _build_style, _create_trend_chart

WORKING_DIR_NAME = "deckdb_working"
TOP_N_CHART = 20
TOP_N_CARD_CHART = 30

# measure names, used consistently in every sheet, chart and the glossary
M_SHARE = "share of Challenge Top 32"
M_PRESENCE = "event presence"
M_TOP8 = "Top 8"
M_CONVERSION = "Top 8 conversion"
M_BASELINE = "common baseline"
M_CI = "95% CI (pilot bootstrap)"
M_TROPHY = "trophies"
M_PILOTS = "pilots"
M_TOP_PILOT = "top pilot"
M_COVERAGE = "participant coverage"

GLOSSARY: List[tuple[str, str]] = [
    ("event", "one Modern Challenge of the DeckDB export (event_id); premier events are not in the export. Date = "
              "event_date (UTC); every period is closed on both ends."),
    ("Challenge Top 32", "the published lists of an event: final_rank 1..L, L = min(32, player_count). The population "
                         "of every material is these lists -- not all participants and not the MTGO metagame."),
    (M_SHARE, "lists of the deck / all lists of the period. Rogue and unassigned lists together form the 'no deck' "
              "row. It is not the probability of meeting the deck."),
    (M_PRESENCE, "number of events of the period with at least one list of the deck; also as % of the events."),
    (M_TOP8, "a list with final_rank <= 8. 'Top 8 lists' = their number; 'Top 8 (% of events)' = events with at least "
             "one Top 8 list of the deck / events of the period."),
    (M_CONVERSION, "Top 32 -> Top 8: Top 8 lists of the deck / lists of the deck (DeckDB K36, the main result measure)."),
    (M_BASELINE, "all Top 8 lists / all lists of the period, measured (not assumed 25%); the conversion colour scale "
                 "diverges around it."),
    (M_CI, f"95% interval of the Top 8 conversion: {di.BOOT_DRAWS} draws of the deck's pilots with replacement, all "
           f"lists of each drawn pilot, percentiles 2.5 / 97.5; given only with >= {di.BOOT_MIN_PILOTS} pilots."),
    ("pilot labels", f"'{di.LABEL_FEW_PILOTS}': fewer than {di.BOOT_MIN_PILOTS} pilots in the period, no interval; "
                     f"'{di.LABEL_CONCENTRATION}': the top pilot holds >= 1/3 of the deck's lists or >= 1/2 of its Top 8 "
                     "entries (decks only)."),
    ("expected conversion", "sum of 8/L over the deck's lists / its lists; only in the control script "
                            "scripts/deckdb_methodology_stats.py (warning above 1 pp for decks with >= 20 lists), not in "
                            "the materials."),
    (M_TROPHY, "lists with final_rank = 1, as a count (month, format period)."),
    (M_PILOTS, "distinct values of the export's anonymous player column; materials give counts and shares only, never "
               "an identifier."),
    (M_TOP_PILOT, "the pilot with the most Top 32 lists of the deck (ties: more Top 8 lists, then the id): their share "
                  "of the deck's Top 32 lists and Top 8 entries, their own conversion and the deck's conversion "
                  "without them. Decks only, never archetypes."),
    (M_COVERAGE, "lists of the period / sum of player_count over its events."),
    ("format period", "from the MTGO effective date of the last confirmed major format event (DeckDB "
                      "rules/format_events.yaml), cumulative up to the as-of date."),
    ("week", "ISO week, Monday-Sunday, disjoint weeks; complete weeks only."),
    ("month", "calendar month; an incomplete month (the export ends inside it) is marked and is working material only."),
    ("variants", "by D2 (DeckDB decisions.md, entry 35): reported in a month with >= 5% of the deck's lists, >= 3 lists "
                 "and >= 2 pilots, only in months with >= 20 lists of the deck; names by K16; a variant does not "
                 "change the deck's measures."),
    ("no deck", "Rogue + unassigned lists (one row in materials, split only in the No_deck_split sheet)."),
]

VARIANT_SPLIT_NOTE = ("The split into list changes and deck composition changes depends on the current variant "
                      "definitions in the DeckDB rules; in decks without variants the whole change counts as a list "
                      "change.")
FDR_NOTE = ("FDR is controlled separately in each category: Benjamini-Hochberg q = 0.10 in the family of total-change "
            "tests and, separately, q = 0.10 in the family of within-variant tests.")

CARD_GLOSSARY: List[tuple[str, str]] = [
    ("card", "oracle_id from the DeckDB card export (deckdb export-cards); copies summed over printings; lands with "
             "the supertype Basic (is_basic = 1) are excluded from every measure."),
    ("card presence", "lists of the deck with at least one copy of the card in the zone / lists of the deck in the "
                      "month; maindeck and sideboard separately."),
    ("mean copies", "copies of the card in the zone / lists of the deck with the card."),
    ("presence change", f"presence in month 2 - presence in month 1 (pp); tested for decks with >= {dc.MIN_LISTS} "
                        f"lists and >= {dc.MIN_PILOTS} pilots in both months; interval: pilot bootstrap, pilots drawn "
                        "independently in each month (no pairing)."),
    ("reported change", f"|change| >= {dc.MIN_CHANGE_PP:.0f} pp and rejected by Benjamini-Hochberg (q = {dc.BH_Q}) in "
                        "the family of total-change tests: every tested (deck, zone, card) of the comparison, all "
                        "decks together; two-sided p from the normal approximation (value / bootstrap SE). "
                        + FDR_NOTE),
    ("change decomposition", "change within variants + change of variant proportions (symmetric midpoints, sums "
                             "exactly to the change); every export variant label, empty = '(no variant)'."),
    (dc.KIND_LIST, f"within-variant component |>= {dc.MIN_CHANGE_PP:.0f} pp| significant in its own BH family "
                   f"(q = {dc.BH_Q}) of within-variant tests (same tested pairs; bootstrap of the component on the "
                   "same pilot draws)."),
    (dc.KIND_COMPOSITION, "a reported change that is not a list change: it comes from the deck's variant proportions, "
                          "not from list changes within variants."),
    ("untested card", "0% presence in one of the months; never tested as a presence change."),
    ("gone card", f"0% in month 2; reported when (1 - share of the deck's month-1 pilots playing it) ^ (the deck's "
                  f"month-2 pilots) < {dc.GONE_P} and >= {dc.MIN_PILOTS_GONE} pilots played it in month 1; otherwise "
                  "an observation."),
    ("new card", f"0% in month 1; reported with >= {dc.MIN_PILOTS_NEW} pilots in month 2; otherwise an observation."),
    ("first appearance", f"the earliest list of the deck with the card in the zone over the whole export; lists and "
                         f"pilots with it in the {dc.FIRST_SEEN_DAYS} days from that date; 'before data start' within "
                         f"the export's first {dc.FIRST_SEEN_DAYS} days; reported with >= {dc.MIN_PILOTS_NEW} pilots "
                         "in those days, otherwise an observation."),
    ("pilot list changes", "consecutive published lists of the same pilot in the same deck (format period): sum of "
                           "|copy differences| in maindeck and sideboard, cards added / removed, main <-> sideboard "
                           "moves; a next list of another deck is a deck change. Distributions per deck only."),
    ("variant split caveat", VARIANT_SPLIT_NOTE),
]

# (legacy material, what replaces it / why it is not produced)
OMITTED_MATERIALS: List[tuple[str, str]] = [
    ("metagame_input.csv / .xlsx / rogue_grouped / grouped.xml", "Videre data (Meta, winrate, game counts)"),
    ("challenge_*_vs_metagame.csv", "comparison with Videre Meta"),
    ("challenge_vs_meta_*.png: 'Metagame Share %' bar", "Videre Meta; replaced by event presence (% of events), the "
                                                        "K36 counterpart, next to the share of Challenge Top 32"),
    ("challenge_vs_meta_*.png: encounter-probability deck cut", "share is not an encounter probability; replaced by "
                                                                f"the top {TOP_N_CHART} decks by share"),
    ("challenge_delta_ranking_decks.png / _archetypes.png (Top 32 frequency - Videre Meta)", "no delta vs Videre; "
                                                                                              "the ranking chart is "
                                                                                              "used for card presence "
                                                                                              "changes instead"),
    ("challenge_conversion_matrix_*.png: Top 8 / Winner event %, Win / Top 32 entries %",
     "replaced by the K36 columns: Top 32 lists, Top 8 lists, Top 8 conversion with CI on a scale diverging around "
     "the common baseline, trophies, pilots, top pilot"),
    ("encounter_prob_Deck/_Archetype.png", "share of Challenge Top 32 is not the probability of meeting a deck"),
    ("record_probability.png / record_probabilities.xlsx", "record distribution from winrate (K36: no winrate)"),
    ("meta_trend_*.png: Rising / Stable / Falling status (Trend Label)", "composite measure without a definition for "
                                                                         "the export; the trend lines remain"),
    ("deck_analysis*.xlsx: Importance, Quartile, Prep Priority, Performance Label, Trend Label",
     "composite measures of Videre Meta and winrate"),
    ("Swiss winrate (winrate charts, Wins / Losses / Matches / Winrate / Wilson columns)", "DeckDB K36: winrate is not "
                                                                                           "a result measure"),
    ("Swiss record and byes", "used only by winrate (K36)"),
    ("challenge_statistics.xlsx: Top16 event %, Top16 / Top4 / Top2 counts, AvgPlace, BestPlace",
     "not defined for the deckdb mode"),
    ("trophies / lists, winner % of events", "decision 2026-10-21 d: trophies as counts only"),
    ("expected conversion", "decision 2026-10-21 d: control script only"),
    ("challenge_statistics.xlsx: C16 / C32 / C64 / C96 sheets", "the export has no Challenge tier column"),
    ("BestPilots, pilot names in finalists_*.csv", "no player names or loginids in materials"),
    ("pilot measures for archetypes", "decision 2026-10-21 d"),
    ("challenge_C*_decklist.csv", "carried player names; placings are in the export"),
    ("broodscale_override_*.csv, rakdos_override_*.csv, alias_report_*.csv", "the pipeline's card rules and aliases do "
                                                                             "not run in the deckdb mode"),
    ("challenge_manifest / challenge_event_status / premier_events", "mtgo.com registry and premier events; the "
                                                                     "period's events are in the Events sheet"),
]

# Render override for test renders only (scripts/deckdb_presentation_test.py): e.g. {"figsize": (12.8, 7.2),
# "dpi": 150}. Empty for every material.
RENDER_OVERRIDE: Dict[str, object] = {}


def _render(**defaults) -> Dict[str, object]:
    return {**defaults, **RENDER_OVERRIDE}


_STYLE = _build_style("classic", None, None)  # (performance, trend, deck, legend) colours of the legacy charts


def root_dir(outputs_base: Path, as_of: date) -> Path:
    return outputs_base / WORKING_DIR_NAME / f"WORKING_{as_of.isoformat()}"


def _terms(*terms: str, glossary: Optional[List[tuple[str, str]]] = None) -> str:
    g = dict(glossary or GLOSSARY)
    return "\n".join(f"{t}: {g[t]}" for t in terms)


def _stamp(export: di.DeckdbExport, level: str, start: date, end: date, cov: dict, note: str = "") -> str:
    return (
        f"WORKING - {level} {start.isoformat()}..{end.isoformat()}{note} - {cov['events']} events, {cov['lists']} "
        f"Challenge Top 32 lists, {M_COVERAGE} {cov['coverage_pct']:.1f}%\n"
        f"DeckDB rules {export.rules_version} - export {export.path.name} sha256 {export.sha256}\n"
        f"data_version {export.data_version}"
    )


def _stamp_cols(df: pd.DataFrame, export: di.DeckdbExport) -> pd.DataFrame:
    out = df.copy()
    out["rules_version"] = export.rules_version
    out["export_sha256"] = export.sha256
    out["data_version"] = export.data_version
    return out


def _status(export: di.DeckdbExport, level: str, start: date, end: date, rows: pd.DataFrame, cov: dict,
            extra: List[tuple]) -> pd.DataFrame:
    base = [
        ("Status", "WORKING - input mode deckdb, not for publication without approval"),
        ("Time level", level),
        ("Period (closed on both ends, event_date)", f"{start.isoformat()} .. {end.isoformat()}"),
        ("Events", cov["events"]),
        ("Challenge Top 32 lists", cov["lists"]),
        ("Lists with a deck", int((rows["status"] == di.STATUS_ASSIGNED).sum())),
        ("Lists without a deck (Rogue + unassigned)", int((rows["status"] != di.STATUS_ASSIGNED).sum())),
        ("  of which Rogue", int((rows["status"] == di.STATUS_ROGUE).sum())),
        ("  of which unassigned", int((rows["status"] == di.STATUS_UNASSIGNED).sum())),
        ("Sum of player_count over the events", cov["participants"]),
        (f"{M_COVERAGE} (lists / sum of player_count), %", round(cov["coverage_pct"], 2)),
        ("rules_version", export.rules_version),
        ("name_match_version", export.name_match_version),
        ("data_version", export.data_version),
        ("Export file", str(export.path)),
        ("Export sha256", export.sha256),
        ("Event dates in the export", f"{export.first_event_date} .. {export.last_event_date}"),
        ("Population", "published Challenge Top 32 lists of Modern Challenges; not the MTGO or league metagame"),
        ("Winrate", "not a measure (DeckDB K36)"),
    ]
    return pd.DataFrame(base + extra, columns=["Field", "Value"])


def _write_level(out_dir: Path, label: str, export: di.DeckdbExport, sheets: Dict[str, pd.DataFrame],
                 csvs: Dict[str, pd.DataFrame], glossary: List[tuple[str, str]],
                 extra_cols: Optional[Dict[str, Dict[str, object]]] = None) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    xlsx = out_dir / f"deckdb_WORKING_{label}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, df in sheets.items():
            (df if not df.empty else pd.DataFrame([{"Info": "no rows"}])).to_excel(writer, sheet_name=name[:31],
                                                                                    index=False)
        pd.DataFrame(glossary, columns=["Term", "Definition"]).to_excel(writer, sheet_name="Glossary", index=False)
        pd.DataFrame(OMITTED_MATERIALS, columns=["Legacy material", "Replacement / reason"]).to_excel(
            writer, sheet_name="Omitted", index=False)
    written.append(xlsx)
    for name, df in csvs.items():
        p = out_dir / f"{name}_WORKING_{label}.csv"
        st = _stamp_cols(df, export)
        for col, val in ((extra_cols or {}).get(name) or {}).items():
            st[col] = val
        st.to_csv(p, index=False, encoding="utf-8-sig")
        written.append(p)
    gl = out_dir / "GLOSSARY.md"
    gl.write_text(
        "# Glossary (input mode deckdb, WORKING)\n\n"
        f"DeckDB rules `{export.rules_version}`, export `{export.path.name}`, sha256 `{export.sha256}`, data_version "
        f"`{export.data_version}`. Every CSV in this directory carries the columns `rules_version`, `export_sha256` "
        "and `data_version`.\n\n"
        + "\n".join(f"- **{t}** - {d}" for t, d in glossary) + "\n",
        encoding="utf-8",
    )
    written.append(gl)
    return written


SHARE_BARS = [
    ("Share_Top32_pct", f"{M_SHARE} (%)"),
    ("Event_presence_pct", f"{M_PRESENCE} (% of events)"),
    ("Top8_event_pct", f"{M_TOP8} (% of events with a Top 8 list)"),
]


def _share_charts(decks: pd.DataFrame, archs: pd.DataFrame, out: Path, what: str, footer: str) -> List[Path]:
    written = []
    for tbl, key, name in ((decks, "Deck", "decks"), (archs, "Archetype", "archetypes")):
        p = _chart_compare_presence(
            tbl, out / f"share_top32_{name}.png",
            f"{key}s, {what}: {M_SHARE}, {M_PRESENCE}, {M_TOP8} (top {TOP_N_CHART} by share)",
            key_col=key, bars=SHARE_BARS, ylabel="%", top_n=TOP_N_CHART, footer=footer, **_render())
        if p is not None:
            written.append(p)
    return written


def _trend_chart(trend: pd.DataFrame, current: pd.DataFrame, key: str, level: str, path: Path, footer: str
                 ) -> Optional[Path]:
    """Legacy trend chart (statistics_engine._create_trend_chart) on disjoint ISO weeks, no trend status."""
    if trend.empty:
        return None
    weeks = sorted(trend["ISO_week"].unique())
    idx = {w: i + 1 for i, w in enumerate(weeks)}
    hist = trend.rename(columns={key: "Deck"}).assign(WeekIndex=trend["ISO_week"].map(idx), Level=level)
    cur = current.rename(columns={key: "Deck Display Name", "Share_Top32_pct": "Meta"})
    p, _ = _create_trend_chart(
        hist, cur, len(weeks), len(weeks), "deckdb", level, path.parent, *_STYLE[1:],
        value_col="Share_Top32_pct", title=f"{key}s: {M_SHARE} - last {len(weeks)} ISO weeks (disjoint)",
        xlabel="ISO week", ylabel=f"{M_SHARE} (%)", week_labels={i: w for w, i in idx.items()},
        show_trend_status=False, legend_title=f"{key}s (top 10 of the last week)", footer=footer, out_path=path,
        **_render())
    return p


def build_week(export: di.DeckdbExport, as_of: date, trend_weeks: int, root: Path) -> List[Path]:
    weeks = di.complete_iso_weeks(as_of, max(1, trend_weeks))
    ws, we = weeks[-1]
    rows = di.window_rows(export, ws, we)
    cov = di.coverage(rows)
    label = di.iso_week_label(ws)
    out = root / f"week_{label}"
    decks = di.share_table(rows, "material_deck", "Deck", trophies=False)
    decks.insert(1, "Archetype", decks["Deck"].map(di.deck_archetype_map(rows)))
    archs = di.share_table(rows, "material_archetype", "Archetype", trophies=False)
    archs.insert(1, "Decks", archs["Archetype"].map(di.archetype_decks(rows)).astype(int))
    trend_d = di.weekly_trend(export, weeks, "material_deck", "Deck")
    trend_a = di.weekly_trend(export, weeks, "material_archetype", "Archetype")
    status = _status(export, f"ISO week {label}", ws, we, rows, cov, [
        ("Trend", f"{len(weeks)} disjoint ISO weeks, the last one {label}"),
    ])
    written = _write_level(out, label, export, {
        "STATUS": status, "Decks": decks, "Archetypes": archs,
        "No_deck_split": di.no_deck_split(rows, trophies=False), "Events": di.event_table(rows),
        "Trend_decks": trend_d, "Trend_archetypes": trend_a,
    }, {"decks": decks, "archetypes": archs, "share_history_weeks": trend_d}, GLOSSARY)
    footer = f"{_stamp(export, 'ISO week ' + label, ws, we, cov)}\n{_terms(M_SHARE, M_PRESENCE, M_TOP8, 'week')}"
    written += _share_charts(decks, archs, out, f"ISO week {label}", footer)
    tcov = di.coverage(di.window_rows(export, weeks[0][0], we))
    tfooter = f"{_stamp(export, 'trend', weeks[0][0], we, tcov)}\n{_terms(M_SHARE, 'week')}"
    for tbl, cur, key, level, name in ((trend_d, decks, "Deck", "Deck", "decks"),
                                       (trend_a, archs, "Archetype", "Archetype", "archetypes")):
        p = _trend_chart(tbl, cur, key, level, out / f"trend_share_{name}_last{len(weeks)}w.png", tfooter)
        if p is not None:
            written.append(p)
    return written


def build_month(export: di.DeckdbExport, as_of: date, root: Path) -> List[Path]:
    ms, me, complete = di.month_period(as_of)
    rows = di.window_rows(export, ms, me)
    cov = di.coverage(rows)
    label = ms.strftime("%Y-%m") + ("" if complete else "_incomplete")
    out = root / f"month_{label}"
    decks = di.share_table(rows, "material_deck", "Deck", trophies=True)
    decks.insert(1, "Archetype", decks["Deck"].map(di.deck_archetype_map(rows)))
    archs = di.share_table(rows, "material_archetype", "Archetype", trophies=True)
    archs.insert(1, "Decks", archs["Archetype"].map(di.archetype_decks(rows)).astype(int))
    note = "" if complete else f" (incomplete month: the export ends {export.last_event_date})"
    status = _status(export, f"month {ms:%Y-%m}", ms, me, rows, cov, [
        ("Completeness", "complete" if complete else f"INCOMPLETE - working only; data up to {me.isoformat()}"),
        ("Variants (D2)", dict(GLOSSARY)["variants"]),
    ])
    written = _write_level(out, label, export, {
        "STATUS": status, "Decks": decks, "Archetypes": archs, "Variants": di.variant_table(rows),
        "No_deck_split": di.no_deck_split(rows, trophies=True), "Events": di.event_table(rows),
    }, {"decks": decks, "archetypes": archs, "finalists": di.finalists_table(rows)}, GLOSSARY)
    footer = f"{_stamp(export, 'month', ms, me, cov, note)}\n{_terms(M_SHARE, M_PRESENCE, M_TOP8, 'month')}"
    written += _share_charts(decks, archs, out, label.replace("_", " "), footer)
    return written


_PILOT_CHART_COLS = {"Pilots_Top8", "Top_pilot_share_Top32_pct", "Top_pilot_share_Top8_pct", "Top_pilot_conversion_pct",
                     "Without_top_pilot_conversion_pct"}


def _conversion_cell(r: pd.Series, col: str) -> str:
    v = r[col]
    if col == "Top8_conversion_pct":
        text = f"{v:.1f}%"
        if not math.isnan(r["CI_low_pct"]):
            text += f" [{r['CI_low_pct']:.0f}-{r['CI_high_pct']:.0f}]"
        return text + (f"\n{r['Pilot_labels']}" if r.get("Pilot_labels") else "")
    if r.get("Deck") == di.NO_DECK_LABEL and col in _PILOT_CHART_COLS:
        return ""  # pilot measures are not defined for the 'no deck' row (the chart fills them with 0)
    if col.endswith("_pct"):
        return "" if pd.isna(v) else f"{v:.1f}%"
    return "" if pd.isna(v) else f"{int(v)}"


def _conversion_chart(tbl: pd.DataFrame, key: str, baseline: float, path: Path, title: str, footer: str,
                      pilots: bool) -> Optional[Path]:
    columns = [("Lists", "Top 32\nlists"), ("Top8_lists", "Top 8\nlists"),
               ("Top8_conversion_pct", f"{M_CONVERSION}\n[{M_CI}]"), ("Trophies", M_TROPHY),
               ("Pilots_Top32", f"{M_PILOTS}\nTop 32")]
    if pilots:
        columns += [("Pilots_Top8", f"{M_PILOTS}\nTop 8"),
                    ("Top_pilot_share_Top32_pct", f"{M_TOP_PILOT}\n% of Top 32 lists"),
                    ("Top_pilot_share_Top8_pct", f"{M_TOP_PILOT}\n% of Top 8 entries"),
                    ("Top_pilot_conversion_pct", f"{M_TOP_PILOT}\nconversion"),
                    ("Without_top_pilot_conversion_pct", "conversion\nwithout top pilot")]
    shown = tbl.sort_values("Lists", ascending=False).head(TOP_N_CHART)
    decks_only = shown[shown[key] != di.NO_DECK_LABEL]["Top8_conversion_pct"]
    span = float((decks_only - baseline).abs().max()) if not decks_only.empty else 1.0
    scales = {c: ("seq", max(float(pd.to_numeric(shown[c], errors="coerce").max()), 1e-9)) for c, _ in columns}
    scales["Top8_conversion_pct"] = ("div", baseline, span or 1.0)
    return _chart_conversion_matrix(
        tbl, key, path,
        f"{title}\n{M_CONVERSION}: red-blue scale diverging around the {M_BASELINE} {baseline:.2f}% (Top 8 lists / "
        "lists of the period); other columns: own 0..max blue scale",
        columns=columns, scales=scales, cell_text=_conversion_cell, top_n=TOP_N_CHART, colorbar_label="",
        footer=footer, **_render(figsize=(4 + 1.6 * len(columns), 9)))


def build_format_period(export: di.DeckdbExport, start: date, event_key: str, as_of: date, root: Path) -> List[Path]:
    rows = di.window_rows(export, start, as_of)
    cov = di.coverage(rows)
    baseline = di.top8_baseline(rows)
    label = f"{start.isoformat()}_to_{as_of.isoformat()}"
    out = root / f"format_period_{label}"
    decks = di.conversion_table(rows, "material_deck", "Deck", pilots=True)
    decks.insert(1, "Archetype", decks["Deck"].map(di.deck_archetype_map(rows)))
    archs = di.conversion_table(rows, "material_archetype", "Archetype", pilots=False)
    archs.insert(1, "Decks", archs["Archetype"].map(di.archetype_decks(rows)).astype(int))
    pilot_cols = ["Deck", "Lists", "Top8_lists", "Pilots_Top32", "Pilots_Top8", "Top_pilot_lists",
                  "Top_pilot_share_Top32_pct", "Top_pilot_Top8_lists", "Top_pilot_share_Top8_pct",
                  "Top_pilot_conversion_pct", "Without_top_pilot_lists", "Without_top_pilot_conversion_pct",
                  "Top_pilot_tie", "Pilot_labels"]
    pilots = decks[decks["Deck"] != di.NO_DECK_LABEL][pilot_cols].reset_index(drop=True)
    deck_cols = ["Deck", "Archetype", "Lists", "Top8_lists", "Top8_conversion_pct", "CI_low_pct", "CI_high_pct",
                 "CI_info", "Pilot_labels", "Trophies", "Pilots_Top32"]
    status = _status(export, "format period", start, as_of, rows, cov, [
        ("Format period start", f"{start.isoformat()} (effective_mtgo, entry {event_key} of rules/format_events.yaml)"),
        (f"{M_BASELINE} of the {M_CONVERSION} (Top 8 lists / lists of the period), %", round(baseline, 3)),
        ("  Top 8 lists in the period", int(rows["top8"].sum())),
        ("Conversion interval", dict(GLOSSARY)[M_CI] + f" Seed {di.BOOT_SEED}."),
        ("Pilot labels", dict(GLOSSARY)["pilot labels"]),
        ("Pilot measures", "decks only (not archetypes, not the 'no deck' row); Pilots sheet"),
    ])
    written = _write_level(out, label, export, {
        "STATUS": status, "Decks": decks[deck_cols], "Archetypes": archs, "Pilots": pilots,
        "No_deck_split": di.no_deck_split(rows, trophies=True), "Events": di.event_table(rows),
    }, {"decks": decks[deck_cols], "archetypes": archs, "pilots": pilots, "finalists": di.finalists_table(rows)},
        GLOSSARY)
    stamp = _stamp(export, "format period", start, as_of, cov)
    for tbl, key, name, with_pilots, terms in (
            (decks, "Deck", "decks", True, (M_CONVERSION, M_BASELINE, M_CI, "pilot labels", M_TOP_PILOT)),
            (archs, "Archetype", "archetypes", False, (M_CONVERSION, M_BASELINE, M_CI, "pilot labels"))):
        p = _conversion_chart(
            tbl, key, baseline, out / f"conversion_matrix_{name}.png",
            f"{key}s, format period from {start.isoformat()}: {M_CONVERSION}"
            + (f", {M_PILOTS} (top {TOP_N_CHART} by lists)" if with_pilots else ""),
            f"{stamp}\n{_terms(*terms, 'format period')}", with_pilots)
        if p is not None:
            written.append(p)
    return written


def _card_label(r: pd.Series) -> str:
    zone = "MD" if r["Zone"] == "main" else "SB"
    return f"{r['Deck']}: {r['Card']} ({zone}) {r['Pilots_with_card_1']}->{r['Pilots_with_card_2']} pilots"


def build_card_layer(export: di.DeckdbExport, cards: "dc.CardLayer", as_of: date, period_start: date,
                     root: Path) -> List[Path]:
    m2s, m2e, complete = di.month_period(as_of)
    m1s, m1e = di.previous_month(m2s)
    label = f"{m1s:%Y-%m}_vs_{m2s:%Y-%m}" + ("" if complete else "_incomplete")
    out = root / f"cards_{label}"
    t = dc.change_tables(export, cards, (m1s, m1e), (m2s, m2e))
    first = dc.first_appearances(export, cards, m2s, m2e)
    pilot_changes = dc.pilot_list_changes(export, cards, period_start, as_of)
    tested, outside = t["tested"], t["outside"]
    rows = pd.concat([di.window_rows(export, m1s, m1e), di.window_rows(export, m2s, m2e)])
    cov = di.coverage(rows)
    n_tested = len(tested)
    kind = tested["Category"] if n_tested else pd.Series(dtype=str)
    list_changes = tested[kind == dc.KIND_LIST] if n_tested else tested
    composition = tested[kind == dc.KIND_COMPOSITION] if n_tested else tested
    gone = outside[outside["Path"].str.startswith("gone")] if not outside.empty else outside
    new = outside[outside["Path"].str.startswith("new")] if not outside.empty else outside

    def _n(df: pd.DataFrame) -> str:
        return (f"{int(df['Reported'].sum())} reported, {int((df['Reported'] == 0).sum())} observations"
                if not df.empty else "0")

    status = _status(export, f"card layer, months {m1s:%Y-%m} and {m2s:%Y-%m}", m1s, m2e, rows, cov, [
        ("Comparison", f"{m1s} .. {m1e} vs {m2s} .. {m2e}"
                       + ("" if complete else " - month 2 INCOMPLETE, comparison is working only")),
        ("Card export", str(cards.path)),
        ("Card export sha256", cards.sha256),
        ("name_match_version / scryfall_dump_id", f"{cards.name_match_version} / {cards.scryfall_dump_id}"),
        ("data_version (cards = assignments)", cards.data_version),
        ("Rows of lands with the supertype Basic (excluded)", cards.basic_rows),
        (f"Tested decks (>= {dc.MIN_LISTS} lists and >= {dc.MIN_PILOTS} pilots in both months)",
         int(t["eligible"]["Tested"].sum()) if not t["eligible"].empty else 0),
        ("Tested (deck, zone, card) pairs", n_tested),
        ("BH families (each q = 0.10)", f"two: total-change tests ({n_tested}) and within-variant tests ({n_tested}); "
                                        "FDR is controlled separately in each category"),
        (f"Reported presence changes (|change| >= {dc.MIN_CHANGE_PP:.0f} pp, BH q = 0.10)",
         int(tested["Reported_change"].sum()) if n_tested else 0),
        (f"  of which '{dc.KIND_COMPOSITION}'", len(composition)),
        (f"'{dc.KIND_LIST}' (|within| >= {dc.MIN_CHANGE_PP:.0f} pp, BH q = 0.10 in the within-variant family)",
         len(list_changes)),
        ("  of which without a reported total change",
         int((list_changes["Reported_change"] == 0).sum()) if n_tested else 0),
        ("Gone cards (0% in month 2)", _n(gone)),
        ("New cards (0% in month 1)", _n(new)),
        ("First appearances in month 2", _n(first)),
        ("FDR", FDR_NOTE),
        ("Variant split caveat", VARIANT_SPLIT_NOTE),
        ("Bootstrap", f"{di.BOOT_DRAWS} draws, seed {di.BOOT_SEED}, pilots drawn independently in each month; the "
                      "within-variant component on the same draws"),
    ])
    sheets = {
        "STATUS": status, "List_changes": list_changes, "Composition_changes": composition, "Gone_cards": gone,
        "New_cards": new, "First_appearances": first, "Tested": tested, "Deck_eligibility": t["eligible"],
        f"Presence_{m1s:%Y-%m}": t["presence_1"], f"Presence_{m2s:%Y-%m}": t["presence_2"],
        "Pilot_list_changes": pilot_changes,
    }
    reported = {
        "list_changes": list_changes, "composition_changes": composition,
        "gone_cards": gone[gone["Reported"] == 1] if not gone.empty else gone,
        "new_cards": new[new["Reported"] == 1] if not new.empty else new,
        "first_appearances": first[first["Reported"] == 1] if not first.empty else first,
        "pilot_list_changes": pilot_changes,
    }
    extra = {name: {"cards_sha256": cards.sha256} for name in reported}
    for name in ("list_changes", "composition_changes"):
        extra[name]["variant_split_caveat"] = VARIANT_SPLIT_NOTE
    written = _write_level(out, label, export, sheets, reported, GLOSSARY + CARD_GLOSSARY, extra)
    stamp = (_stamp(export, "cards", m1s, m2e, cov, "" if complete else " (month 2 incomplete)")
             + f"\ncards sha256 {cards.sha256}")
    for df, value, err, name, title, terms in (
            (list_changes, "Within_variants_pp", ("Within_CI_low_pp", "Within_CI_high_pp"), "list_changes",
             f"List changes {m1s:%Y-%m} -> {m2s:%Y-%m}: within-variant component (pp) with {M_CI}, "
             f"{len(list_changes)} of {n_tested} tested (top {TOP_N_CARD_CHART} by |component|)",
             ("presence change", dc.KIND_LIST, "change decomposition")),
            (composition, "Change_pp", ("CI_low_pp", "CI_high_pp"), "composition_changes",
             f"Deck composition changes {m1s:%Y-%m} -> {m2s:%Y-%m}: presence change (pp) with {M_CI}, "
             f"{len(composition)} reported without a list change (top {TOP_N_CARD_CHART} by |change|)",
             ("reported change", dc.KIND_COMPOSITION, "change decomposition"))):
        if df.empty:
            continue
        chart_df = df.assign(Label=df.apply(_card_label, axis=1))
        p = _chart_delta_ranking(
            chart_df, "Label", out / f"{name}.png", title, delta_col=value, err_cols=err,
            ylabel=f"{'within-variant component' if value.startswith('Within') else 'presence change'} (pp); "
                   "bar colours: rank order only",
            top_n=TOP_N_CARD_CHART,
            footer=f"{stamp}\n{_terms(*terms, glossary=CARD_GLOSSARY)}\n{VARIANT_SPLIT_NOTE}\n{FDR_NOTE}",
            **_render(figsize=(18, 10)))
        if p is not None:
            written.append(p)
    return written


def build_deckdb_materials(export: di.DeckdbExport, cfg: di.DeckdbConfig, as_of: date, outputs_base: Path,
                           trend_weeks: int = 12, cache_dir: Optional[Path] = None,
                           log: Callable[[str], None] = print) -> List[Path]:
    start, key = di.format_period_start(cfg.format_events_path, as_of)
    # validate every period before writing anything
    weeks = di.complete_iso_weeks(as_of, max(1, trend_weeks))
    for ws, we in weeks:
        di.window_rows(export, ws, we)
    ms, me, _ = di.month_period(as_of)
    month_rows = di.window_rows(export, ms, me)
    di.window_rows(export, start, as_of)
    di.window_rows(export, *di.previous_month(ms))
    # the card file (versions, data_version, list keys) is checked before writing anything, too
    cards = dc.load_cards(cfg.cards_path, export) if cfg.cards_path is not None else None
    root = root_dir(outputs_base, as_of)
    written = build_week(export, as_of, trend_weeks, root)
    written += build_month(export, as_of, root)
    written += build_format_period(export, start, key, as_of, root)
    if cards is not None:
        written += build_card_layer(export, cards, as_of, start, root)
    if cache_dir is not None:
        cache = di.match_mtgo_cache(month_rows, cache_dir, ms, me)
        log(f"[deckdb] MTGO cache check (month {ms:%Y-%m}): matched {cache['matched']}, export not in cache "
            f"{cache['export_not_in_cache']}, cache not in export {cache['cache_not_in_export']}")
    log(f"[deckdb] as of {as_of}: rules {export.rules_version}, sha256 {export.sha256}, data_version "
        f"{export.data_version}; format period from {start} ({key})")
    for p in written:
        log(f"[deckdb] wrote {p}")
    return written
