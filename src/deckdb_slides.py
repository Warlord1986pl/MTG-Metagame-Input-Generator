"""Slide mode of input mode "deckdb" (order of 2026-10-21 j): a separate renderer for 16:9 slides.

The report charts (deckdb_materials.py, legacy chart functions) are not touched; this module draws its own
figures from the same tables (deckdb_input / deckdb_cards) at 1920x1080 px:

  - the figure is 13.333 x 7.5 in at 144 dpi, i.e. exactly one slide wide, so 1 pt in the figure is 1 pt on the
    slide; no text is smaller than MIN_PT (14 pt);
  - share bars (week, month): at most TOP_N_BARS decks plus an "other decks" row; share of Challenge Top 32 and
    Top 8 conversion, the "other decks" row on pooled lists (its Top 8 lists / its lists); no event presence;
  - conversion matrices (format period): at most TOP_N_MATRIX rows, the deck pilot measures on a second slide;
  - card changes: at most TOP_N_CARDS per slide (list changes by |within-variant component|, deck composition
    changes by |presence change|, since their within-variant component is by definition not reported);
  - trend (week level): the decks of the last week's top TOP_N_TREND, archetypes all;
  - every slide carries one source line (rules version, window, list count); the glossary of the slide's terms,
    the export file, sha256 and data_version go to the speaker notes.

    slides = build_deckdb_slides(export, cfg, as_of, out_dir)   # [(png, notes), ...]
    write_pptx(slides, out_dir / "slides.pptx")
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

try:
    import deckdb_cards as dc
    import deckdb_input as di
    import deckdb_materials as dm
except ModuleNotFoundError:
    from . import deckdb_cards as dc
    from . import deckdb_input as di
    from . import deckdb_materials as dm

DPI = 144
SLIDE_W_IN, SLIDE_H_IN = 1920 / DPI, 1080 / DPI  # 13.333 x 7.5 in: 1920 x 1080 px, 1 figure pt = 1 slide pt
MIN_PT = 14
FONT = {"title": 24, "label": 17, "tick": 16, "value": 15, "source": 14, "cell": 16, "header": 15}
TOP_N_BARS = 10
TOP_N_MATRIX = 12
TOP_N_CARDS = 8
TOP_N_TREND = 8
OTHER_DECKS = "other decks"
C_SHARE, C_CONV, C_BASE, C_TEXT, C_GRID = "#2f6db5", "#d0743c", "#555555", "#222222", "#dddddd"


@dataclass
class Slide:
    png: Path
    notes: str


def _fig():
    fig = plt.figure(figsize=(SLIDE_W_IN, SLIDE_H_IN), dpi=DPI)
    fig.patch.set_facecolor("white")
    return fig


def _frame(fig, title: str, source: str) -> None:
    fig.text(0.02, 0.955, title, fontsize=FONT["title"], weight="bold", color=C_TEXT, va="top")
    fig.text(0.02, 0.018, source, fontsize=FONT["source"], color=C_BASE, va="bottom")


def _source(export: di.DeckdbExport, start: date, end: date, lists: int) -> str:
    return (f"WORKING · DeckDB rules {export.rules_version} · {start.isoformat()} – {end.isoformat()} · "
            f"{lists} Challenge Top 32 lists")


def _notes(export: di.DeckdbExport, terms: Sequence[str], glossary=None, extra: str = "") -> str:
    g = dict(glossary or dm.GLOSSARY)
    lines = [f"{t}: {g[t]}" for t in terms]
    if extra:
        lines.append(extra)
    lines.append(f"DeckDB export {export.path.name}, sha256 {export.sha256}, data_version {export.data_version}. "
                 "Working material, input mode deckdb; winrate is not a measure (DeckDB K36).")
    return "\n\n".join(lines)


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


def _style_axis(ax) -> None:
    lo, hi = ax.get_xlim()
    ax.set_xticks([t for t in ax.get_xticks() if lo <= t <= hi])  # no tick labels outside the drawn range
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=FONT["tick"], colors=C_TEXT)
    ax.grid(axis="x", color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


# --- share bars (week, month) ------------------------------------------------------------------

def bar_rows(tbl: pd.DataFrame, key: str, top_n: int = TOP_N_BARS) -> pd.DataFrame:
    """Top *top_n* rows by lists (the 'Rogue & unclassified' row never counts as a deck) plus one pooled row for the rest:
    share = its lists / all lists, conversion = its Top 8 lists / its lists. 'Rogue & unclassified' stays its own row."""
    t = tbl[[key, "Lists", "Top8_lists"]].copy()
    total = int(t["Lists"].sum())
    no_deck = t[t[key] == di.NO_DECK_LABEL]
    decks = t[t[key] != di.NO_DECK_LABEL].sort_values(["Lists", key], ascending=[False, True])
    shown, rest = decks.head(top_n), decks.iloc[top_n:]
    parts = [shown]
    if not rest.empty:
        parts.append(pd.DataFrame([{key: f"{len(rest)} {OTHER_DECKS}", "Lists": int(rest["Lists"].sum()),
                                    "Top8_lists": int(rest["Top8_lists"].sum())}]))
    parts.append(no_deck)
    out = pd.concat(parts, ignore_index=True)
    out["Share_pct"] = out["Lists"] / total * 100.0
    out["Conversion_pct"] = out["Top8_lists"] / out["Lists"] * 100.0
    return out


def share_slide(tbl: pd.DataFrame, key: str, rows: pd.DataFrame, title: str, source: str, path: Path) -> Path:
    data = bar_rows(tbl, key)
    baseline = di.top8_baseline(rows)
    fig = _fig()
    _frame(fig, title, source)
    labels = [f"{k} ({n})" for k, n in zip(data[key], data["Lists"])]
    y = list(range(len(data)))[::-1]
    for i, (col, colour, xlabel) in enumerate((
            ("Share_pct", C_SHARE, f"{dm.M_SHARE} (%)"),
            ("Conversion_pct", C_CONV, f"{dm.M_CONVERSION} (%), pooled lists"))):
        ax = fig.add_axes([0.25 + i * 0.40, 0.20, 0.30, 0.66])
        vals = data[col].to_numpy()
        ax.barh(y, vals, color=colour, height=0.62)
        top = max(float(pd.Series(vals).max()), baseline if col == "Conversion_pct" else 0.0)
        ax.set_xlim(0, top * 1.28 or 1.0)
        for yi, v in zip(y, vals):
            ax.text(v + top * 0.015, yi, f"{v:.1f}%", va="center", fontsize=FONT["value"], color=C_TEXT, zorder=4,
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5})  # readable over the baseline
        if col == "Conversion_pct":
            ax.axvline(baseline, color=C_BASE, linestyle="--", linewidth=1.4)
            ax.set_xlabel(f"{xlabel}\ndashed: {dm.M_BASELINE} {baseline:.1f}%", fontsize=FONT["label"])
        else:
            ax.set_xlabel(xlabel, fontsize=FONT["label"])
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
        ax.set_yticks(y)
        ax.set_yticklabels(labels if i == 0 else [""] * len(y))
        if i == 1:
            ax.tick_params(axis="y", length=0)
        _style_axis(ax)
    fig.text(0.02, 0.885, f"{key} (Top 32 lists)", fontsize=FONT["label"], color=C_TEXT)
    return _save(fig, path)


# --- trend (week level) ------------------------------------------------------------------------

def trend_slide(trend: pd.DataFrame, key: str, title: str, source: str, path: Path) -> Optional[Path]:
    if trend.empty:
        return None
    weeks = sorted(trend["ISO_week"].unique())
    last = trend[(trend["ISO_week"] == weeks[-1]) & (trend[key] != di.NO_DECK_LABEL)]
    keep = list(last.sort_values(["Share_Top32_pct", key], ascending=[False, True])[key].head(TOP_N_TREND))
    piv = (trend[trend[key].isin(keep)].pivot_table(index="ISO_week", columns=key, values="Share_Top32_pct")
           .reindex(weeks).fillna(0.0)[keep])
    fig = _fig()
    _frame(fig, title, source)
    ax = fig.add_axes([0.07, 0.19, 0.62, 0.66])
    cmap = plt.get_cmap("tab10")
    for i, name in enumerate(keep):
        ax.plot(range(len(weeks)), piv[name].to_numpy(), marker="o", linewidth=2.4, color=cmap(i % 10), label=name)
    step = max(1, math.ceil(len(weeks) / 6))
    ticks = sorted(range(len(weeks) - 1, -1, -step))  # from the last week back, so the last week is labelled
    ax.set_xticks(ticks)
    ax.set_xticklabels([weeks[i] for i in ticks])
    ax.set_ylabel(f"{dm.M_SHARE} (%)", fontsize=FONT["label"])
    ax.set_xlabel("ISO week (disjoint)", fontsize=FONT["label"])
    ax.set_ylim(bottom=0)
    _style_axis(ax)
    ax.tick_params(axis="x", pad=12)
    ax.grid(axis="y", color=C_GRID, linewidth=0.8)
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=FONT["tick"], frameon=False,
              title=f"top {len(keep)} of {weeks[-1]}", title_fontsize=FONT["tick"])
    return _save(fig, path)


# --- conversion matrices (format period) -------------------------------------------------------

def _blend(c1: tuple, c2: tuple, t: float) -> tuple:
    t = min(max(t, 0.0), 1.0)
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


_WHITE, _RED, _BLUE = (1.0, 1.0, 1.0), (0.84, 0.30, 0.25), (0.22, 0.45, 0.75)


def _conv_colour(v: float, baseline: float, span: float) -> tuple:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return _WHITE
    d = (v - baseline) / (span or 1.0)
    return _blend(_WHITE, _BLUE if d > 0 else _RED, abs(d) * 0.75)


def _conv_colours(values: pd.Series, baseline: float) -> List[tuple]:
    """Diverging colours around *baseline*, scaled to the largest deviation of this column."""
    span = float((pd.to_numeric(values, errors="coerce") - baseline).abs().max())
    return [_conv_colour(v, baseline, span if span == span and span > 0 else 1.0) for v in values]


def matrix_slide(row_labels: List[str], columns: List[tuple], title: str, source: str, path: Path,
                 caption: str) -> Path:
    """*columns*: (header, relative width, texts, colours or None)."""
    fig = _fig()
    _frame(fig, title, source)
    fig.text(0.02, 0.885, caption, fontsize=FONT["tick"], color=C_TEXT)
    ax = fig.add_axes([0.02, 0.07, 0.96, 0.80])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    label_w = 0.19
    widths = [w for _, w, _, _ in columns]
    scale = (1 - label_w) / sum(widths)
    n = len(row_labels)
    head_h = 0.17
    row_h = (1 - head_h) / max(n, 1)
    x = label_w
    for header, w, texts, colours in columns:
        cw = w * scale
        ax.text(x + cw / 2, 1 - head_h / 2, header, ha="center", va="center", fontsize=FONT["header"],
                weight="bold", color=C_TEXT)
        for r in range(n):
            y0 = 1 - head_h - (r + 1) * row_h
            ax.add_patch(Rectangle((x, y0), cw, row_h, facecolor=colours[r] if colours else _WHITE,
                                   edgecolor=C_GRID, linewidth=1))
            ax.text(x + cw / 2, y0 + row_h / 2, texts[r], ha="center", va="center", fontsize=FONT["cell"],
                    color=C_TEXT)
        x += cw
    for r, lab in enumerate(row_labels):
        y0 = 1 - head_h - (r + 1) * row_h
        ax.text(label_w - 0.008, y0 + row_h / 2, lab, ha="right", va="center", fontsize=FONT["cell"], color=C_TEXT)
    return _save(fig, path)


def _pct(v) -> str:
    return "" if v is None or pd.isna(v) else f"{v:.1f}%"


def _int(v) -> str:
    return "" if v is None or pd.isna(v) else f"{int(v)}"


def conversion_slides(tbl: pd.DataFrame, key: str, rows: pd.DataFrame, baseline: float, source: str,
                      out: Path, start: date, with_pilots: bool) -> List[Path]:
    shown = tbl.sort_values(["Lists", key], ascending=[False, True]).head(TOP_N_MATRIX).reset_index(drop=True)
    decks_only = shown[shown[key] != di.NO_DECK_LABEL]["Top8_conversion_pct"]
    span = float((decks_only - baseline).abs().max()) if not decks_only.empty else 1.0
    labels = list(shown[key])
    conv = [_conv_colour(v, baseline, span) for v in shown["Top8_conversion_pct"]]
    ci = ["" if pd.isna(lo) else f"{lo:.0f}–{hi:.0f}%" for lo, hi in zip(shown["CI_low_pct"], shown["CI_high_pct"])]
    cols = [("Top 32\nlists", 1.0, [_int(v) for v in shown["Lists"]], None),
            ("Top 8\nlists", 1.0, [_int(v) for v in shown["Top8_lists"]], None),
            ("Top 8\nconversion", 1.4, [_pct(v) for v in shown["Top8_conversion_pct"]], conv),
            ("95% CI\n(pilot\nbootstrap)", 1.4, ci, None),
            (dm.M_TROPHY, 1.0, [_int(v) for v in shown["Trophies"]], None),
            (f"{dm.M_PILOTS}\nTop 32", 1.0, [_int(v) for v in shown["Pilots_Top32"]], None),
            ("pilot labels", 1.8, [str(v) for v in shown["Pilot_labels"].fillna("")], None)]
    name = "decks" if with_pilots else "archetypes"
    written = [matrix_slide(
        labels, cols, f"{key}s, format period from {start.isoformat()}: {dm.M_CONVERSION}", source,
        out / f"slide_conversion_{name}.png",
        f"Top {len(shown)} by lists. Conversion colour: red below / blue above the {dm.M_BASELINE} {baseline:.1f}%.")]
    if with_pilots:
        pil = shown[shown[key] != di.NO_DECK_LABEL].reset_index(drop=True)
        cols = [(f"{dm.M_PILOTS}\nTop 32", 1.0, [_int(v) for v in pil["Pilots_Top32"]], None),
                (f"{dm.M_PILOTS}\nTop 8", 1.0, [_int(v) for v in pil["Pilots_Top8"]], None),
                (f"{dm.M_TOP_PILOT}:\n% of Top 32\nlists", 1.4, [_pct(v) for v in pil["Top_pilot_share_Top32_pct"]],
                 None),
                (f"{dm.M_TOP_PILOT}:\n% of Top 8\nentries", 1.4, [_pct(v) for v in pil["Top_pilot_share_Top8_pct"]],
                 None),
                (f"{dm.M_TOP_PILOT}:\nconversion", 1.3, [_pct(v) for v in pil["Top_pilot_conversion_pct"]],
                 _conv_colours(pil["Top_pilot_conversion_pct"], baseline)),
                ("conversion\nwithout\ntop pilot", 1.4, [_pct(v) for v in pil["Without_top_pilot_conversion_pct"]],
                 _conv_colours(pil["Without_top_pilot_conversion_pct"], baseline))]
        written.append(matrix_slide(
            list(pil[key]), cols, f"Decks, format period from {start.isoformat()}: pilot measures", source,
            out / "slide_pilots_decks.png",
            f"Same decks, no '{di.NO_DECK_LABEL}' row. Conversion: red below / blue above the {dm.M_BASELINE} "
            f"{baseline:.1f}%, scale per column."))
    return written


# --- card changes ------------------------------------------------------------------------------

def card_slide(df: pd.DataFrame, value: str, err: tuple, rank_by: str, title: str, source: str, caption: str,
               path: Path) -> Optional[Path]:
    if df.empty:
        return None
    d = df.assign(_rank=df[rank_by].abs()).sort_values(["_rank", "Deck", "Card"], ascending=[False, True, True])
    d = d.head(TOP_N_CARDS).reset_index(drop=True)
    fig = _fig()
    _frame(fig, title, source)
    fig.text(0.02, 0.885, caption, fontsize=FONT["tick"], color=C_TEXT)
    ax = fig.add_axes([0.36, 0.14, 0.60, 0.70])
    y = list(range(len(d)))[::-1]
    vals = d[value].to_numpy()
    lo, hi = d[err[0]].to_numpy(), d[err[1]].to_numpy()
    colours = [C_SHARE if v >= 0 else C_CONV for v in vals]
    ax.barh(y, vals, color=colours, height=0.55)
    ax.errorbar(vals, y, xerr=[vals - lo, hi - vals], fmt="none", ecolor=C_TEXT, elinewidth=1.4, capsize=4)
    ax.axvline(0, color=C_BASE, linewidth=1)
    lim = max(abs(float(lo.min())), abs(float(hi.max())), 1.0) * 1.08
    ax.set_xlim(-lim, lim)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{r['Card']} ({'MD' if r['Zone'] == 'main' else 'SB'})\n"
                        f"{r['Deck']} · pilots {r['Pilots_with_card_1']} → {r['Pilots_with_card_2']}"
                        for _, r in d.iterrows()])
    ax.set_xlabel(f"{'within-variant component' if value.startswith('Within') else 'presence change'} (pp), "
                  f"bars: {dm.M_CI}", fontsize=FONT["label"])
    _style_axis(ax)
    return _save(fig, path)


# --- driver ------------------------------------------------------------------------------------

def build_deckdb_slides(export: di.DeckdbExport, cfg: di.DeckdbConfig, as_of: date, out_dir: Path,
                        trend_weeks: int = 12, log: Callable[[str], None] = print) -> List[Slide]:
    start, _key = di.format_period_start(cfg.format_events_path, as_of)
    slides: List[Slide] = []
    # week
    weeks = di.complete_iso_weeks(as_of, max(1, trend_weeks))
    ws, we = weeks[-1]
    rows = di.window_rows(export, ws, we)
    label = di.iso_week_label(ws)
    src = _source(export, ws, we, len(rows))
    notes = _notes(export, (dm.M_SHARE, dm.M_CONVERSION, dm.M_BASELINE, "week", "Rogue & unclassified"),
                   extra=f"'{OTHER_DECKS}': every deck outside the top {TOP_N_BARS}, pooled (its Top 8 lists / its "
                         "lists). Event presence is not shown on slides (report material only).")
    for key, col, name in (("Deck", "material_deck", "decks"), ("Archetype", "material_archetype", "archetypes")):
        tbl = di.share_table(rows, col, key, trophies=False)
        slides.append(Slide(share_slide(tbl, key, rows, f"{key}s, ISO week {label}", src,
                                        out_dir / f"slide_week_{name}.png"), notes))
    tstart = weeks[0][0]
    tsrc = _source(export, tstart, we, len(di.window_rows(export, tstart, we)))
    tnotes = _notes(export, (dm.M_SHARE, "week"))
    for key, col, name in (("Deck", "material_deck", "decks"), ("Archetype", "material_archetype", "archetypes")):
        p = trend_slide(di.weekly_trend(export, weeks, col, key), key,
                        f"{key}s: {dm.M_SHARE}, last {len(weeks)} ISO weeks", tsrc,
                        out_dir / f"slide_trend_{name}.png")
        if p is not None:
            slides.append(Slide(p, tnotes))
    # month
    ms, me, complete = di.month_period(as_of)
    rows = di.window_rows(export, ms, me)
    mlabel = f"{ms:%Y-%m}" + ("" if complete else " (incomplete)")
    src = _source(export, ms, me, len(rows))
    mnotes = _notes(export, (dm.M_SHARE, dm.M_CONVERSION, dm.M_BASELINE, "month", "Rogue & unclassified"),
                    extra=f"'{OTHER_DECKS}': every deck outside the top {TOP_N_BARS}, pooled.")
    for key, col, name in (("Deck", "material_deck", "decks"), ("Archetype", "material_archetype", "archetypes")):
        tbl = di.share_table(rows, col, key, trophies=False)
        slides.append(Slide(share_slide(tbl, key, rows, f"{key}s, month {mlabel}", src,
                                        out_dir / f"slide_month_{name}.png"), mnotes))
    # format period
    rows = di.window_rows(export, start, as_of)
    baseline = di.top8_baseline(rows)
    src = _source(export, start, as_of, len(rows))
    fnotes = _notes(export, (dm.M_CONVERSION, dm.M_BASELINE, dm.M_CI, "pilot labels", dm.M_TROPHY, dm.M_PILOTS,
                             dm.M_TOP_PILOT, "format period"))
    decks = di.conversion_table(rows, "material_deck", "Deck", pilots=True)
    archs = di.conversion_table(rows, "material_archetype", "Archetype", pilots=False)
    for p in conversion_slides(decks, "Deck", rows, baseline, src, out_dir, start, True):
        slides.append(Slide(p, fnotes))
    for p in conversion_slides(archs, "Archetype", rows, baseline, src, out_dir, start, False):
        slides.append(Slide(p, fnotes))
    # card layer
    if cfg.cards_path is not None:
        cards = dc.load_cards(cfg.cards_path, export)
        m1s, m1e = di.previous_month(ms)
        t = dc.change_tables(export, cards, (m1s, m1e), (ms, me))
        tested = t["tested"]
        kind = tested["Category"] if len(tested) else pd.Series(dtype=str)
        lists = len(di.window_rows(export, m1s, m1e)) + len(di.window_rows(export, ms, me))
        src = _source(export, m1s, me, lists)
        cnotes = _notes(export, ("card", "card presence", "presence change", "reported change",
                                 "change decomposition", dc.KIND_LIST, dc.KIND_COMPOSITION),
                        glossary=dm.CARD_GLOSSARY,
                        extra=f"{dm.VARIANT_SPLIT_NOTE} {dm.FDR_NOTE} Cards sha256 {cards.sha256}.")
        for df, value, err, rank_by, name, what in (
                (tested[kind == dc.KIND_LIST], "Within_variants_pp", ("Within_CI_low_pp", "Within_CI_high_pp"),
                 "Within_variants_pp", "list_changes", "List changes"),
                (tested[kind == dc.KIND_COMPOSITION], "Change_pp", ("CI_low_pp", "CI_high_pp"), "Change_pp",
                 "composition_changes", "Deck composition changes")):
            n = len(df)
            p = card_slide(
                df, value, err, rank_by, f"{what}, {m1s:%Y-%m} → {mlabel}", src,
                f"{min(n, TOP_N_CARDS)} of {n} reported, largest by |"
                f"{'within-variant component' if rank_by.startswith('Within') else 'presence change'}|; "
                "full list in the report material.",
                out_dir / f"slide_{name}.png")
            if p is not None:
                slides.append(Slide(p, cnotes))
    for s in slides:
        log(f"[deckdb slides] wrote {s.png}")
    return slides


def write_pptx(slides: List[Slide], path: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W_IN), Inches(SLIDE_H_IN)
    for s in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_picture(str(s.png), 0, 0, width=prs.slide_width, height=prs.slide_height)
        slide.notes_slide.notes_text_frame.text = s.notes
    prs.save(path)
    return path
