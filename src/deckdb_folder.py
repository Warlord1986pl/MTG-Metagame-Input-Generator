"""One flat presentation folder per ISO week (order of 2026-10-26 k, point B7): ``outputs/presentation/<year>-W<nn>/``,
next to the working directories of outputs/_working/deckdb/ (unchanged), no subdirectories, at most 13 files (the
presentation project takes at most 24 attachments; correction to the order of 2026-10-26 k):

  01_archetypes_share.png  02_archetypes_trend.png  03_decks_share.png  04_decks_trend.png
  05_week_matrix_decks.png  06_week_matrix_archetypes.png  07_top8.png  08_pilots.png  09_new_cards.png
  with the monthly material: m01_deck_results.png  m02_list_changes.png
  data.xlsx  (sheet "contents" first, then one sheet per table, SHEETS / MONTH_SHEETS)
  notes.md   (sections Status, Files, Speaker notes, Glossary)

The week is the last complete ISO week ending on or before --as-of. The monthly material (the previous month) joins
the folder when the week contains the first Sunday of its month; ``month`` forces a month (an incomplete one is
marked "incomplete month" in titles and footers). The deck results of the monthly material cover the format period
up to the end of the month (DeckDB K41). Every data sheet carries rules_version, export_sha256 and data_version; no
chart carries a definition, seed, sha256 or rules version.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    import deckdb_cards as dc
    import deckdb_input as di
    import deckdb_materials as dm
    import deckdb_presentation as dp
except ModuleNotFoundError:
    from . import deckdb_cards as dc
    from . import deckdb_input as di
    from . import deckdb_materials as dm
    from . import deckdb_presentation as dp

FOLDER_NAME = "presentation"
WEEK_FILES = {
    "01_archetypes_share.png": "share of this week's Top 32 lists per archetype, with presence and change vs last week",
    "02_archetypes_trend.png": "weekly share of every archetype over the last 12 ISO weeks",
    "03_decks_share.png": "share of the ten most played decks and of Rogue & unclassified, with presence and change; "
                          "other decks in one line under the chart",
    "04_decks_trend.png": "weekly share of the ten most played decks of this week over the last 12 ISO weeks (fixed "
                          "deck colours)",
    "05_week_matrix_decks.png": "counts of the week per deck: Top 32 lists, events present, Top 8 lists, events with "
                                "a Top 8 list, trophies",
    "06_week_matrix_archetypes.png": "the same counts per archetype",
    "07_top8.png": "Top 8 lists of the week per deck, trophies as a star marker",
    "08_pilots.png": "distinct pilots and the top pilot's share of the lists for the ten decks of the share chart",
    "09_new_cards.png": "cards appearing in a deck for the first time, played by at least 2 pilots this week (lands "
                        "that only make mana left out)",
    "data.xlsx": "every table of the folder, one sheet each; the first sheet 'contents' lists them",
    "notes.md": "Status (window, events, lists, coverage, rules version, export sha256, triage, classification "
                "changes, event IDs to verify on mtgo.com), Files, Speaker notes, Glossary",
}
MONTH_FILES = {
    "m01_deck_results.png": "the 12 decks with the most lists of the format period up to the end of the month: lists, "
                            "mean place percentile, Top 8 conversion; coloured only where the interval excludes the "
                            "average",
    "m02_list_changes.png": "the biggest list changes from the month before to this month (at most 12, mana-only "
                            "lands left out), added or cut",
}
MAX_FILES = 13
SHEETS = {
    "decks": "the deck rows of the share chart: share, presence, pilots, change vs last week with its interval",
    "archetypes": "the archetype rows of the share chart",
    "top8": "the eight Top 8 lists of every event of the week: place, deck, variant, archetype, winner",
    "new_cards": "every new card of the week (reported and observations), mana-only lands flagged",
    "pilots": "pilots per deck of the share chart: lists, distinct pilots, top pilot's lists and share",
    "share_history_decks": "weekly share per deck over the trend weeks",
    "share_history_archetypes": "weekly share per archetype over the trend weeks",
}
MONTH_SHEETS = {
    "m_deck_results": "results of every deck in the format period up to the end of the month, with the "
                      "pilot-bootstrap intervals (DeckDB K41)",
    "m_list_changes": "every reported list change of the month vs the month before (mana-only lands flagged)",
}
FOLDER_TERMS = ("event", "Challenge Top 32", dm.M_SHARE, dm.M_PRESENCE, dm.M_TOP_WEEK, dm.M_OTHER, di.NO_DECK_LABEL, dm.M_CHANGE,
                dm.M_CHANGE_CI, dm.M_WEEK_PILOTS, dm.M_WEEK_TOP8, dm.M_TROPHY, "variant label", dm.M_NEW_CARD_WEEK,
                dm.M_PILOTS, dm.M_COVERAGE, "week", dm.M_NOT_WEEKLY)
MONTH_TERMS = (dm.M_PERCENTILE, dm.M_CONVERSION, dm.M_BASELINE, dm.M_CI, dm.M_COLOUR_RULE, "format period", "month",
               "pilot labels")
EXTRA_GLOSSARY = [
    ("top pilot share (week)", "lists of the deck's most frequent pilot this week / the deck's lists this week; marked "
                               "when it is at least 1/3."),
    ("mana-only land", "a land whose text only makes mana (DeckDB K42, column mana_only_land of export-cards); left "
                       "out of the card charts, flagged in the card data."),
    ("classification change", "a list whose deck in this week's export differs from the export of the previous week "
                              "(the latest export with data up to the previous Sunday; DeckDB rules changed in "
                              "between); counted over the lists present in both exports."),
]


def folder_dir(outputs_base: Path, week_start: date) -> Path:
    return outputs_base / FOLDER_NAME / di.iso_week_label(week_start)


def month_for(week_end: date) -> Optional[tuple[date, date]]:
    """The previous month when the week (ending on Sunday *week_end*) contains the first Sunday of its month."""
    if week_end.day > 7:
        return None
    return di.previous_month(week_end.replace(day=1))


def previous_export(export: di.DeckdbExport, prev_week_end: date) -> Optional[Path]:
    """The export the previous week's folder was made from: the assignment export of the same directory whose last
    event date is the latest one on or before *prev_week_end* (ties: the newest file)."""
    best = None
    for p in export.path.parent.glob("assignments_*.csv"):
        if p.resolve() == export.path.resolve():
            continue
        try:
            last = pd.read_csv(p, usecols=["event_date"], dtype=str)["event_date"].str.strip().max()
        except (ValueError, KeyError):
            continue
        if last <= prev_week_end.isoformat() and (best is None or (last, p.stat().st_mtime) > best[0]):
            best = ((last, p.stat().st_mtime), p)
    return best[1] if best else None


def classification_changes(export: di.DeckdbExport, prev_path: Optional[Path], since: Optional[date] = None
                           ) -> tuple[Optional[pd.DataFrame], str]:
    """(moves old deck -> new deck over the lists of both exports [from *since*], description of the comparison)."""
    if prev_path is None:
        return None, "no earlier export in the export directory"
    old = pd.read_csv(prev_path, dtype=str, keep_default_na=False)
    old["event_id"], old["decktournamentid"] = old["event_id"].map(di._norm_id), old["decktournamentid"].map(di._norm_id)
    old["old"] = old["deck"].where(old["status"] == di.STATUS_ASSIGNED, di.NO_DECK_LABEL)
    cur = export.rows[["event_id", "decktournamentid", "event_date", "material_deck"]]
    m = cur.merge(old[["event_id", "decktournamentid", "old"]], on=["event_id", "decktournamentid"])
    if since is not None:
        m = m[m["event_date"] >= since.isoformat()]
    ch = m[m["old"] != m["material_deck"]]
    moves = ch.groupby(["old", "material_deck"]).size().reset_index(name="Lists").rename(
        columns={"old": "From", "material_deck": "To"}).sort_values(["Lists", "From"], ascending=[False, True])
    rv = str(old["rules_version"].iloc[0]) if len(old) else "?"
    return moves.reset_index(drop=True), (f"{prev_path.name} (rules {rv}, last event {old['event_date'].max()}) vs "
                                         f"{export.path.name} (rules {export.rules_version}): {len(m)} common lists, "
                                         f"{len(ch)} changed deck")


def _stamp(df: pd.DataFrame, export: di.DeckdbExport) -> pd.DataFrame:
    out = df.copy()
    out["rules_version"], out["export_sha256"], out["data_version"] = (export.rules_version, export.sha256,
                                                                       export.data_version)
    return out


def _pilots(rows: pd.DataFrame, decks: List[str], arch: Dict[str, str]) -> pd.DataFrame:
    out = []
    for d in decks:
        g = rows[rows["material_deck"] == d]
        top = int(g["player"].value_counts().iloc[0]) if len(g) else 0
        share = top / len(g) if len(g) else 0.0
        out.append({"Deck": d, "Archetype": arch.get(d, ""), "Lists": len(g), "Pilots": int(g["player"].nunique()),
                    "Top_pilot_lists": top, "Top_pilot_share_pct": share * 100.0,
                    "Concentrated": int(share >= dp.CONCENTRATED)})
    return pd.DataFrame(out)


def build_presentation_folder(export: di.DeckdbExport, cfg: di.DeckdbConfig, as_of: date, outputs_base: Path,
                              triage: Optional[di.TriageState] = None, month: Optional[str] = None,
                              trend_weeks: int = 12, log: Callable[[str], None] = print) -> List[Path]:
    """The flat folder of the last complete ISO week up to *as_of*; *month* ("YYYY-MM") forces the monthly material
    of that month (incomplete when the export ends inside it)."""
    weeks = di.complete_iso_weeks(as_of, max(2, trend_weeks))
    (ps, pe), (ws, we) = weeks[-2], weeks[-1]
    rows, prev = di.window_rows(export, ws, we), di.window_rows(export, ps, pe)
    m_period = None
    if month:
        ms = date.fromisoformat(f"{month}-01")
        me_full = (ms + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        me = min(me_full, date.fromisoformat(export.last_event_date), as_of)
        m_period = (ms, me, me >= me_full)
    elif month_for(we) is not None:
        ms, me = month_for(we)
        m_period = (ms, me, True)
    if m_period is not None:
        di.window_rows(export, m_period[0], m_period[1])
        di.window_rows(export, *di.previous_month(m_period[0]))
        # K41: deck results over the format period, from its start up to the end of the month of the material
        fstart = di.format_period_start(cfg.format_events_path, m_period[1])[0]
        di.window_rows(export, fstart, m_period[1])
        m_period = (*m_period, fstart)
    cards = dc.load_cards(cfg.cards_path, export) if cfg.cards_path is not None else None
    out = folder_dir(outputs_base, ws)
    out.mkdir(parents=True, exist_ok=True)
    for p in out.iterdir():  # the folder is generated: a rerun replaces every file (flat, no subdirectories)
        if p.is_file() and p.suffix in (".png", ".csv", ".md", ".xlsx"):
            p.unlink()
    dp.MARK = triage.note if triage is not None else ""
    dp.NOTICE = triage.notice if triage is not None else ""
    try:
        return _build(export, cards, out, as_of, weeks[-max(1, trend_weeks):], ps, pe, ws, we, rows, prev, triage,
                      m_period, log)
    finally:
        dp.MARK = dp.NOTICE = ""


def _build(export, cards, out: Path, as_of: date, tweeks, ps, pe, ws, we, rows, prev, triage, m_period, log
           ) -> List[Path]:
    colours = dp.Colours()
    label = di.iso_week_label(ws)
    cov = di.coverage(rows)
    arch = di.deck_archetype_map(rows).to_dict()
    decks = di.week_share_table(rows, prev, "material_deck", "Deck", top_n=di.WEEK_TOP_N).rename(
        columns={"Labels_in_row": "Decks_in_row"})
    decks.insert(1, "Archetype", decks["Deck"].map(arch).fillna(""))
    archs = di.week_share_table(rows, prev, "material_archetype", "Archetype").drop(columns="Labels_in_row")
    m_decks = di.share_table(rows, "material_deck", "Deck", trophies=True)
    m_archs = di.share_table(rows, "material_archetype", "Archetype", trophies=True)
    trend_d = di.weekly_trend(export, tweeks, "material_deck", "Deck")
    trend_a = di.weekly_trend(export, tweeks, "material_archetype", "Archetype")
    top8 = di.top8_table(rows)
    named = [d for d in decks["Deck"] if d not in dp.POOLED]
    pilots = _pilots(rows, named, arch)
    if cards is not None:
        nc = dc.new_cards_in_week(export, cards, ws, we)
        mo = cards.mana_only_ids or frozenset()
        nc["Mana_only_land"] = nc["oracle_id"].isin(mo).astype(int)
        nc_shown = nc[(nc["Reported"] == 1) & (nc["Mana_only_land"] == 0)]
    else:
        nc = pd.DataFrame([{"Info": "no card export configured (cards_path)"}])
        nc_shown = pd.DataFrame(columns=["Deck", "Card", "Pilots_with_card", "Zone"])
    foot = dp.footer(ws, we, rows)
    tfoot = dp.footer(tweeks[0][0], tweeks[-1][1], di.window_rows(export, tweeks[0][0], tweeks[-1][1]))
    n = len(tweeks)
    w: List[Path] = []
    notes: Dict[str, str] = {}
    w.append(dp.share_chart(archs, "Archetype", "Archetypes this week", foot, out / "01_archetypes_share.png", colours))
    notes["01_archetypes_share.png"] = (
        "Share of this week's Challenge Top 32 lists for every archetype, with the number of events it appeared in and "
        f"the change from last week; bold green or red marks a change beyond normal week-to-week variation "
        f"({dm._highlighted(archs, 'Archetype')}).")
    trend_arch = [a for a in archs["Archetype"]]
    w.append(dp.trend_chart(trend_a, trend_arch, "Archetype", f"Archetypes over the last {n} weeks", tfoot,
                            out / "02_archetypes_trend.png", colours.of_archetype))
    notes["02_archetypes_trend.png"] = (f"Weekly share of every archetype over the last {n} ISO weeks. Single weeks move "
                                        "a lot; read the direction over several weeks.")
    w.append(dp.share_chart(decks, "Deck", "Most played decks this week", foot, out / "03_decks_share.png", colours))
    other = decks[decks["Deck"] == di.OTHER_DECKS_LABEL]
    notes["03_decks_share.png"] = (
        "The ten most played decks of the week and Rogue & unclassified (lists that match no defined deck), coloured "
        "by archetype; each label gives the share, the events with the deck and the change from last week "
        f"({dm._highlighted(decks, 'Deck')})."
        + (f" All other decks together are {other.iloc[0]['Share_Top32_pct']:.1f}% of the lists "
           f"({int(other.iloc[0]['Decks_in_row'])} decks)." if not other.empty else ""))
    w.append(dp.trend_chart(trend_d, named, "Deck", f"Top decks over the last {n} weeks", tfoot,
                            out / "04_decks_trend.png", colours.of_deck))
    notes["04_decks_trend.png"] = (f"Weekly share of this week's ten most played decks over the last {n} ISO weeks; "
                                   "each deck keeps its colour from week to week.")
    w.append(dp.week_matrix(m_decks, "Deck", "This week by deck", foot, out / "05_week_matrix_decks.png"))
    notes["05_week_matrix_decks.png"] = (
        f"Counts of this week for the {min(dp.TOP_N_MATRIX, len(m_decks))} decks with the most lists: Top 32 lists, "
        "events with the deck, Top 8 lists, events where it reached the Top 8, and trophies. Each column has its own "
        "colour scale; these are counts, not rates.")
    w.append(dp.week_matrix(m_archs, "Archetype", "This week by archetype", foot, out / "06_week_matrix_archetypes.png",
                            top_n=None))
    notes["06_week_matrix_archetypes.png"] = "The same counts for every archetype."
    w.append(dp.top8_chart(top8, "Top 8 this week", foot, out / "07_top8.png", colours))
    tw = top8[top8["Winner"] == 1]["Deck"].value_counts()
    notes["07_top8.png"] = (f"How many of the {len(top8)} Top 8 lists of the week's {cov['events']} events each deck "
                            "had; the star counts trophies (" + (", ".join(f"{d} {k}" for d, k in tw.items()) or "none")
                            + ").")
    w.append(dp.pilots_chart(pilots, "Who plays the top decks", foot, out / "08_pilots.png", colours))
    conc = list(pilots.loc[pilots["Concentrated"] == 1, "Deck"])
    notes["08_pilots.png"] = (
        "Distinct pilots behind each of the ten most played decks and the share of the deck's lists from its most "
        "frequent pilot. " + (f"Marked ({', '.join(conc)}): one pilot holds at least a third of the lists, so the share "
                              "says more about that pilot than about the deck." if conc else
                              "No deck depends on one pilot for a third of its lists this week."))
    w.append(dp.new_cards_chart(nc_shown, "New cards this week", foot, out / "09_new_cards.png"))
    notes["09_new_cards.png"] = (
        (f"{len(nc_shown)} card(s) showed up in a deck for the first time and were played by at least 2 pilots this "
         "week; 'SB' marks a sideboard card. Lands that only make mana are left out." if len(nc_shown) else
         "No card appeared in a deck for the first time with at least 2 pilots this week."))
    data = {"decks": decks, "archetypes": archs, "top8": top8, "new_cards": nc, "pilots": pilots,
            "share_history_decks": trend_d, "share_history_archetypes": trend_a}
    month_note = ""
    if m_period is not None:
        mw, mnotes, mdata, month_note = _month(export, cards, out, *m_period)
        w += mw
        notes.update(mnotes)
        data.update(mdata)
    w.append(_workbook(export, out / "data.xlsx", data))
    status = _status(export, label, ws, we, rows, cov, triage, colours, month_note, as_of, decks)
    w.append(_notes(export, out, label, status, notes, m_period is not None))
    files = [p for p in out.iterdir() if p.is_file()]
    if len(files) > MAX_FILES:
        raise di.DeckdbInputError(f"{out}: {len(files)} plików, najwyżej {MAX_FILES} (projekt prezentacji przyjmuje "
                                  "najwyżej 24 załączniki)")
    for p in w:
        log(f"[deckdb presentation folder] wrote {p}")
    return w


def _workbook(export, path: Path, data: Dict[str, pd.DataFrame]) -> Path:
    """data.xlsx: the sheet 'contents' (sheet, one sentence on what it holds) first, then one sheet per table, each
    with rules_version, export_sha256 and data_version."""
    desc = {**SHEETS, **MONTH_SHEETS}
    contents = pd.DataFrame([{"sheet": name, "contents": desc[name] + "."} for name in data],
                            columns=["sheet", "contents"])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        contents.to_excel(xw, sheet_name="contents", index=False)
        for name, df in data.items():
            body = _stamp(df, export) if not df.empty else pd.DataFrame([{"Info": "no rows"}])
            body.to_excel(xw, sheet_name=name, index=False)
    return path


def _month(export, cards, out: Path, ms: date, me: date, complete: bool, fstart: date):
    rows = di.window_rows(export, ms, me)
    inc = "" if complete else " (incomplete month)"
    note = "" if complete else "incomplete month"
    # K41: deck results over the format period, from its start up to the end of the month of the material
    frows = di.window_rows(export, fstart, me)
    res = di.conversion_table(frows, "material_deck", "Deck", pilots=True)
    res.insert(1, "Archetype", res["Deck"].map(di.deck_archetype_map(frows)))
    pct_base, conv_base = di.percentile_baseline(frows), di.top8_baseline(frows)
    period = f"{fstart.day} {fstart:%b %Y} – {me.day} {me:%b %Y}"
    w = [dp.results_chart(res, pct_base, conv_base, f"Deck results, {period}{inc}",
                          dp.footer(fstart, me, frows, "format period" + (f" · {note}" if note else "")),
                          out / "m01_deck_results.png")]
    shown = res[res["Deck"] != di.NO_DECK_LABEL].head(dp.TOP_N_RESULTS)
    hit = [r["Deck"] for _, r in shown.iterrows() if r["Percentile_CI_excludes_baseline"]
           or r["Conversion_CI_excludes_baseline"]]
    notes = {"m01_deck_results.png": (
        f"The {len(shown)} decks with the most Top 32 lists in the format period, {period}{inc}, counted from the "
        "last major format change. Mean place percentile shows where a deck's lists finish among the published lists "
        "(100 = first, 0 = last); Top 8 conversion is the share of its lists that reached the Top 8. Only values "
        "clearly away from the average are coloured" + (f" (here: {', '.join(hit)})" if hit else " (none here)")
        + ".")}
    data = {"m_deck_results": res}
    if cards is not None:
        m1s, m1e = di.previous_month(ms)
        t = dc.change_tables(export, cards, (m1s, m1e), (ms, me))
        tested = t["tested"]
        mo = cards.mana_only_ids or frozenset()
        cfoot = dp.footer(m1s, me, pd.concat([di.window_rows(export, m1s, m1e), rows]), note)
        p, n_shown, dropped = dp.list_changes_chart(tested, mo, f"Biggest list changes, {m1s:%B} to {ms:%B}{inc}",
                                                    cfoot, out / "m02_list_changes.png")
        if p is not None:
            w.append(p)
        notes["m02_list_changes.png"] = (
            f"The {n_shown} biggest changes in how players build a deck's lists from {m1s:%B} to {ms:%B}{inc}: a card "
            "that more lists of the same deck variant added (green) or cut (red), in percentage points of the deck's "
            f"lists. Lands that only make mana are left out ({dropped}); 'SB' marks a sideboard card."
            if p is not None else "No list change passed the tests this month.")
        lc = tested[tested["Category"] == dc.KIND_LIST].copy() if not tested.empty else tested
        if not lc.empty:
            lc["Mana_only_land"] = lc["oracle_id"].isin(mo).astype(int)
        data["m_list_changes"] = lc
    return w, notes, data, (f"{ms.isoformat()} .. {me.isoformat()}" + ("" if complete else " - INCOMPLETE month, "
                                                                           "test material")
                            + f"; deck results over the format period {fstart.isoformat()} .. {me.isoformat()}")


def _status(export, label: str, ws: date, we: date, rows: pd.DataFrame, cov: dict, triage, colours,
            month_note: str, as_of: date, decks: pd.DataFrame) -> List[str]:
    """The lines of the Status section of notes.md."""
    prev_path = previous_export(export, we - timedelta(days=7))
    moves, desc = classification_changes(export, prev_path)
    # this week's lists are new (not in the previous week's export): the month of the week instead
    mmoves, _ = classification_changes(export, prev_path, we.replace(day=1)) if prev_path is not None else (None, "")
    ev = di.event_table(rows)
    lines = []
    top = (triage.note or triage.notice) if triage is not None else ""
    if top:
        lines += [f"**{top}**", ""]
    lines += [
        f"- Week: ISO {label}, {ws.isoformat()} .. {we.isoformat()} (as of {as_of.isoformat()})",
        f"- Events: {cov['events']}; Challenge Top 32 lists: {cov['lists']}",
        f"- Participant coverage: {cov['coverage_pct']:.1f}% ({cov['lists']} lists / {cov['participants']} players "
        "summed over the events)",
        f"- {di.NO_DECK_LABEL}: {int((rows['status'] != di.STATUS_ASSIGNED).sum())} lists (Rogue "
        f"{int((rows['status'] == di.STATUS_ROGUE).sum())}, unassigned {int((rows['status'] == di.STATUS_UNASSIGNED).sum())})",
        f"- DeckDB rules version: {export.rules_version}",
        f"- Export: {export.path.name}, sha256 {export.sha256}",
        f"- data_version: {export.data_version}",
        "- Triage (DeckDB K43): " + (f"{triage.status} ({triage.path.name})" if triage is not None else "not read"),
        "- Monthly material: " + (month_note or "not included (the week does not contain the first Sunday of the "
                                               "month)"),
    ]
    named = [d for d in decks["Deck"] if d not in dp.POOLED]
    clash = dp.shared_colours(named, colours.of_deck)
    if clash:
        lines.append("- Decks sharing a fixed colour on the deck trend (the second one dashed): "
                     + ", ".join(f"{a} / {b}" for a, b in clash) + " (docs/deckdb_colours.csv)")
    if colours.missing:
        lines.append("- Deck colours missing from docs/deckdb_colours.csv (a palette colour by name was used): "
                     + ", ".join(colours.missing))
    lines += ["", "### Classification changes since the previous week", "", desc + "."]
    for title, mv in (("All common lists", moves),
                      (f"Lists from {we.replace(day=1).isoformat()} (this week's lists are new, not in the previous "
                       "export)", mmoves)):
        if mv is not None:
            lines += ["", f"{title}:", ""] + ([f"- {r.From} → {r.To}: {r.Lists}" for r in mv.itertuples()]
                                              or ["- none"])
    lines += ["", "### Events of the week (verify on mtgo.com)", "", "| EventID | Date | Players | Lists |",
              "|---|---|---|---|"]
    lines += [f"| {r.EventID} | {r.Date} | {r.Event_players} | {r.Lists} |" for r in ev.itertuples()]
    return lines


def _notes(export, out: Path, label: str, status: List[str], notes: Dict[str, str], with_month: bool) -> Path:
    """notes.md: Status, Files, Speaker notes, Glossary (in this order)."""
    files = dict(WEEK_FILES)
    if with_month:
        files.update(MONTH_FILES)
    present = {p.name for p in out.iterdir()} | {"data.xlsx", "notes.md"}
    file_lines = [f"- `{name}` - {text}." for name, text in sorted(files.items(), key=lambda kv: _order(kv[0]))
                  if name in present]
    missing = [name for name in files if name not in present]
    if missing:
        file_lines += ["", "Not produced this time: " + ", ".join(f"`{name}`" for name in missing) + "."]
    sheets = {**SHEETS, **(MONTH_SHEETS if with_month else {})}
    file_lines += ["", "Sheets of `data.xlsx` (after `contents`): " + ", ".join(f"`{s}`" for s in sheets) + "."]
    mark = dp.MARK or dp.NOTICE
    speaker = ([f"**{mark}**", ""] if mark else []) + [line for name, text in notes.items()
                                                       for line in (f"### {name}", "", text, "")]
    g = dict(dm.GLOSSARY + dm.CARD_GLOSSARY)
    terms = list(FOLDER_TERMS) + (list(MONTH_TERMS) + [dc.KIND_LIST] if with_month else [])
    glossary = ([f"DeckDB rules `{export.rules_version}`, export `{export.path.name}`, sha256 `{export.sha256}`. "
                 "Every data sheet carries `rules_version`, `export_sha256` and `data_version`.", ""]
                + [f"- **{t}** - {g[t]}" for t in terms if t in g] + [f"- **{t}** - {d}" for t, d in EXTRA_GLOSSARY])
    body = ([f"# Presentation {label}", "",
             "MTGO Modern Challenge Top 32, input mode deckdb (DeckDB export only). Slide order = file number.", "",
             "## Status", ""] + status + ["", "## Files", ""] + file_lines + ["", "## Speaker notes", ""] + speaker
            + ["## Glossary", ""] + glossary)
    p = out / "notes.md"
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def _order(name: str) -> tuple:
    if name[:2].isdigit():
        return (0, name)
    if name.startswith("m0"):
        return (1, name)
    return (2, name)
