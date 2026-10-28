"""Presentation charts of input mode "deckdb" (order of 2026-10-26 b, point 2; rules and charts of the order of
2026-10-26 k, points B3-B6): the ``presentation/`` set next to the working charts of every package and the one flat
presentation folder ``outputs/presentation/<year>-W<nn>/`` (deckdb_folder.py). The working charts stay as they are.

Rules for every presentation chart: a short plain-language title; one footer line "MTGO Modern Challenge Top 32 ·
<window> · <N> events, <M> lists"; no confidence-interval whiskers -- an interval only decides the colour (emphasis
only for a value beyond normal variation); horizontal bars sorted descending, names on the left, numbers outside the
bars and never covered; a 16:9 slide figure (13.333 x 7.5 in at 144 dpi, 1 figure pt = 1 slide pt, no text below 14
pt except the small SB / trophy markers). Colours (B3): archetypes by ARCHETYPE_COLOURS, "Rogue & unclassified"
#6B7280; deck bars in the colour of their archetype with an archetype legend; on a deck trend every deck keeps one
colour between weeks (docs/deckdb_colours.csv). No definition, seed, sha256 or rules version appears on a chart: they
stay in STATUS / status.md and GLOSSARY.md; speaker notes explain every chart in one to three plain sentences.
"""

from __future__ import annotations

import csv
import hashlib
import textwrap
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

try:
    import deckdb_cards as dc
    import deckdb_input as di
    from challenge_history_engine import GREY_CELL
except ModuleNotFoundError:
    from . import deckdb_cards as dc
    from . import deckdb_input as di
    from .challenge_history_engine import GREY_CELL

DIR_NAME = "presentation"
DPI = 144
SLIDE = (1920 / DPI, 1080 / DPI)
FONT = {"title": 24, "name": 16, "value": 15, "footer": 14, "legend": 14, "panel": 16, "marker": 11, "cell": 15}
TOP_N_RESULTS = 12
TOP_N_LIST_CHANGES = 12
TOP_N_MATRIX = 12
TOP_N_NEW_CARDS = 12
CONCENTRATED = 1 / 3  # B5g: decks whose top pilot holds >= 1/3 of the week's lists are marked
LEGEND_RESULTS = "coloured = different from average; grey = not distinguishable from average"
LEGEND_CHANGE = ("change vs previous week: bold colour = beyond normal variation after correction for multiple "
                 "comparisons")

# B3 (order of 2026-10-26 k): archetype colours; "Rogue & unclassified" grey
ARCHETYPE_COLOURS = {"Aggro": "#EF4444", "Graveyard": "#6366F1", "Combo": "#3B82F6", "Midrange": "#F59E0B",
                     "Ramp": "#14B8A6", "Control": "#60A5FA", di.NO_DECK_LABEL: "#6B7280"}
# 16 deck colours (dataviz validator: lightness, chroma, adjacent CVD >= 9 and normal-vision >= 19 pass; contrast
# below 3:1 for four of them, so every line carries a direct label); docs/deckdb_colours.csv assigns them in this order
DECK_PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948", "#184f95",
                "#a63d12", "#00a6a6", "#8a3fa0", "#7a8b00", "#c2185b", "#3d5afe", "#b8860b")
COLOURS_CSV = Path("docs") / "deckdb_colours.csv"
C_UP, C_DOWN = "#1a7f37", "#cf222e"   # change beyond normal variation: up / down (text only)
C_ABOVE, C_BELOW = plt.get_cmap("RdBu")(0.85), plt.get_cmap("RdBu")(0.15)   # legacy matrix scale ends
C_ADDED, C_CUT = "#1a7f37", "#cf222e"
C_FLAG = "#B45309"
C_TROPHY = "#C99A06"
C_TEXT, C_SOFT = "#222222", "#555555"
C_BAR = "#2a78d6"
POOLED = (di.OTHER_DECKS_LABEL, di.NO_DECK_LABEL)

MARK = ""  # DeckDB K43: "triage pending: ..." only when a blocking triage was forced (set by deckdb_materials)
NOTICE = ""  # DeckDB K43 supplement: pending_notice, speaker notes and status.md only, never on a chart


# --- colours ---------------------------------------------------------------------------------------

class Colours:
    """docs/deckdb_colours.csv (kind, label, colour, note): fixed archetype and deck colours. A deck missing from
    the file gets a palette colour by a hash of its name (stable, but not checked for clashes) and is listed in
    *missing* (status.md asks to add it)."""

    def __init__(self, path: Path = COLOURS_CSV):
        self.archetype = dict(ARCHETYPE_COLOURS)
        self.deck: Dict[str, str] = {}
        self.missing: List[str] = []
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    (self.archetype if r["kind"] == "archetype" else self.deck)[r["label"]] = r["colour"]

    def of_archetype(self, a: str) -> str:
        return self.archetype.get(a, ARCHETYPE_COLOURS[di.NO_DECK_LABEL])

    def of_deck(self, d: str) -> str:
        if d == di.NO_DECK_LABEL:
            return self.archetype[di.NO_DECK_LABEL]
        if d not in self.deck:
            if d not in self.missing:
                self.missing.append(d)
            return DECK_PALETTE[int(hashlib.sha256(d.encode("utf-8")).hexdigest(), 16) % len(DECK_PALETTE)]
        return self.deck[d]


def footer(start: date, end: date, rows: pd.DataFrame, note: str = "") -> str:
    return (f"MTGO Modern Challenge Top 32 · {start.isoformat()} – {end.isoformat()} · "
            f"{rows['event_id'].nunique()} events, {len(rows)} lists" + (f" · {note}" if note else "")
            + (f" · {MARK}" if MARK else ""))


def _fig():
    fig = plt.figure(figsize=SLIDE, dpi=DPI)
    fig.patch.set_facecolor("white")
    return fig


def _frame(fig, title: str, foot: str, legend: str = "") -> None:
    fig.text(0.02, 0.955, title, fontsize=FONT["title"], weight="bold", color=C_TEXT, va="top")
    if legend:
        fig.text(0.02, 0.885, legend, fontsize=FONT["legend"], color=C_SOFT, va="top")
    fig.text(0.02, 0.02, foot, fontsize=FONT["footer"], color=C_SOFT, va="bottom")


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


def _bare(ax) -> None:
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#bbbbbb")
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0, labelsize=FONT["name"], colors=C_TEXT)


_CLEAR = {"boxstyle": "square,pad=0.1", "facecolor": "white", "edgecolor": "none"}  # no line drawn over a number


def _labels(ax, xs: Sequence[float], parts: Sequence[Sequence[tuple]], pad_px: float = 8.0) -> None:
    """Write each bar's label in pieces [(text, style), ...] right of the bar end, then widen the x range until
    every label ends inside the axes (nothing is clipped or drawn over a bar)."""
    fig = ax.figure
    texts: List = []
    for i, (x, row) in enumerate(zip(xs, parts)):
        off = pad_px
        for text, style in row:
            t = ax.annotate(text, xy=(x, i), xytext=(off, 0), textcoords="offset pixels", va="center", ha="left",
                            fontsize=style.get("size", FONT["value"]), color=style.get("color", C_TEXT),
                            weight=style.get("weight", "normal"), bbox=style.get("bbox", _CLEAR), annotation_clip=False,
                            zorder=5)
            fig.canvas.draw()
            off += t.get_window_extent(fig.canvas.get_renderer()).width + style.get("gap", 0)
            texts.append(t)
    top = max(xs) if len(xs) else 1.0
    for _ in range(40):
        fig.canvas.draw()
        right = max(t.get_window_extent(fig.canvas.get_renderer()).x1 for t in texts) if texts else 0
        if right <= ax.get_window_extent().x1:
            break
        ax.set_xlim(0, ax.get_xlim()[1] + 0.08 * max(top, 1e-9))


def _fit_left(ax, margin: float = 0.02) -> None:
    """Shift the axes (keeping its right edge) so that the widest y tick label starts inside the figure."""
    fig = ax.figure
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    widest = max((t.get_window_extent(r).width for t in ax.get_yticklabels()), default=0.0) / fig.bbox.width
    x0, y0, w, h = ax.get_position().bounds
    left = max(x0, widest + margin + 0.01)
    ax.set_position([left, y0, x0 + w - left, h])


def _hbars(ax, names: Sequence[str], values: Sequence[float], colors: Sequence) -> None:
    y = list(range(len(names)))
    ax.barh(y, values, color=colors, height=0.68)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.set_ylim(len(names) - 0.5, -0.5)  # first row on top
    ax.set_xlim(0, max(values) * 1.05 if len(values) and max(values) > 0 else 1.0)
    _bare(ax)


def _pp(v: float) -> str:
    return f"{v:+.1f} pp".replace("-", "−")


def _arch_legend(fig, archetypes: Sequence[str], colours: Colours) -> None:
    """One row of archetype swatches under the subtitle (the axes start below LEGEND_TOP)."""
    handles = [Patch(facecolor=colours.of_archetype(a), label=a) for a in archetypes]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.015, 0.855), ncol=len(handles),
               fontsize=FONT["legend"], frameon=False, handlelength=1.2, columnspacing=1.4, borderaxespad=0)


LEGEND_TOP = 0.79  # top of the axes of a chart with an archetype legend row


# --- week: share, presence and change in one chart (B5a, B5b) ----------------------------------------

def share_chart(tbl: pd.DataFrame, key: str, title: str, foot: str, path: Path,
                colours: Optional[Colours] = None) -> Path:
    """One row per deck (or archetype) and 'Rogue & unclassified' last: bar = share; label '<share>% · <presence>/
    <events> events · <change>', the change bold and green / red only when change_beyond (BH, weekly_metrics) is 1. 'other decks' is
    no bar: one line under the chart, 'other decks: <share>% of lists across <n> decks'. Deck bars take their
    archetype's colour (column Archetype) with an archetype legend; archetype bars their own colour."""
    colours = colours or Colours()
    named = tbl[~tbl[key].isin(POOLED)].sort_values(["Share_Top32_pct", key], ascending=[False, True])
    t = pd.concat([named, tbl[tbl[key] == di.NO_DECK_LABEL]])
    other = tbl[tbl[key] == di.OTHER_DECKS_LABEL]
    fig = _fig()
    top = LEGEND_TOP if key == "Deck" else 0.85
    ax = fig.add_axes([0.24, 0.13, 0.72, top - 0.13])
    if key == "Deck":
        arch = [di.NO_DECK_LABEL if d == di.NO_DECK_LABEL else a for d, a in zip(t["Deck"], t["Archetype"])]
        bar_colours = [colours.of_archetype(a) for a in arch]
    else:
        arch = list(t[key])
        bar_colours = [colours.of_archetype(a) for a in arch]
    _hbars(ax, list(t[key]), list(t["Share_Top32_pct"]), bar_colours)
    _fit_left(ax)
    parts = []
    for r in t.itertuples():
        head = f"{r.Share_Top32_pct:.1f}% · {r.Events_with_list}/{r.Week_events} events · "
        if pd.notna(r.change_beyond) and int(r.change_beyond) == 1:  # BH rejection (weekly_metrics), not the CI alone
            change = {"weight": "bold", "color": C_UP if r.Change_pp > 0 else C_DOWN}
        else:
            change = {"color": C_SOFT}
        parts.append([(head, {}), (_pp(r.Change_pp), change)])
    _labels(ax, list(t["Share_Top32_pct"]), parts)
    if key == "Deck":
        _arch_legend(fig, [a for a in dict.fromkeys(arch)], colours)
    if not other.empty:
        o = other.iloc[0]
        n = int(o.get("Decks_in_row", o.get("Labels_in_row", 0)))
        fig.text(0.02, 0.075, f"{di.OTHER_DECKS_LABEL}: {o['Share_Top32_pct']:.1f}% of lists across {n} decks",
                 fontsize=FONT["legend"], color=C_TEXT, va="bottom")
    _frame(fig, title, foot, LEGEND_CHANGE)
    return _save(fig, path)


# --- trends (B5c) -----------------------------------------------------------------------------------

def _spread(values: List[float], gap: float) -> List[float]:
    """End-label positions: the values pushed apart by at least *gap*, order kept."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    pos = [0.0] * len(values)
    last = None
    for i in order:
        p = values[i] if last is None else max(values[i], last + gap)
        pos[i] = last = p
    return pos


def shared_colours(labels: Sequence[str], colour_of) -> List[tuple[str, str]]:
    """(earlier label, later label) pairs of *labels* with the same fixed colour (the later one is drawn dashed)."""
    first: Dict[str, str] = {}
    out = []
    for lab in labels:
        c = colour_of(lab)
        if c in first:
            out.append((first[c], lab))
        else:
            first[c] = lab
    return out


def trend_chart(trend: pd.DataFrame, labels: Sequence[str], key: str, title: str, foot: str, path: Path,
                colour_of) -> Optional[Path]:
    """Share of Top 32 lists per ISO week for *labels* (one 2 px line each, a fixed colour from *colour_of*; a colour
    repeated in the chart is dashed with a diamond swatch), every line labelled at its right end; weeks without a list
    of the label count 0."""
    if trend.empty or not len(labels):
        return None
    weeks = sorted(trend["ISO_week"].unique())
    piv = trend.pivot_table(index="ISO_week", columns=key, values="Share_Top32_pct", aggfunc="sum").reindex(weeks)
    piv = piv.reindex(columns=list(labels)).fillna(0.0)
    fig = _fig()
    ax = fig.add_axes([0.07, 0.15, 0.66, 0.70])
    x = list(range(len(weeks)))
    seen: Dict[str, str] = {}
    for lab in labels:  # a colour already used by an earlier line of the chart: dashed (the colour stays fixed)
        c = colour_of(lab)
        style = "--" if c in seen else "-"
        seen.setdefault(c, lab)
        ax.plot(x, piv[lab], color=c, linewidth=2.2, linestyle=style, marker="o", markersize=5, zorder=3)
    top = float(piv.to_numpy().max()) if piv.size else 1.0
    ax.set_ylim(0, top * 1.12 if top > 0 else 1.0)
    ends = list(piv.iloc[-1])
    gap = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.052
    pos = _spread(ends, gap)
    if pos and max(pos) > ax.get_ylim()[1]:
        ax.set_ylim(0, max(pos) + gap)
    for lab, y, ypos in zip(labels, ends, pos):  # end label: a swatch in the line colour, the text in ink
        ax.plot([x[-1], x[-1] + 0.25, x[-1] + 0.45], [y, ypos, ypos], color=colour_of(lab), linewidth=1.2,
                clip_on=False, zorder=2)
        ax.plot([x[-1] + 0.55], [ypos], marker="s" if seen[colour_of(lab)] == lab else "D", markersize=10,
                color=colour_of(lab), clip_on=False, zorder=4)
        ax.annotate(f"{lab} {y:.1f}%", xy=(x[-1] + 0.7, ypos), va="center", ha="left", fontsize=FONT["value"],
                    color=C_TEXT, annotation_clip=False)
    ax.set_xticks(x)
    ax.set_xticklabels([w.split("-")[1] for w in weeks], fontsize=FONT["value"], color=C_TEXT)
    ax.set_xlim(-0.3, x[-1] + 0.3)
    ax.tick_params(axis="y", labelsize=FONT["value"], colors=C_SOFT)
    ax.set_ylabel("% of Top 32 lists", fontsize=FONT["name"], color=C_SOFT)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    _frame(fig, title, foot, f"ISO weeks {weeks[0]} – {weeks[-1]}")
    return _save(fig, path)


# --- week matrix (B5d) ------------------------------------------------------------------------------

MATRIX_COLS = [("Lists", "Top 32\nlists"), ("Events_with_list", "events\npresent"), ("Top8_lists", "Top 8\nlists"),
               ("Events_with_Top8", "events with\nTop 8"), ("Trophies", "trophies")]


def week_matrix(tbl: pd.DataFrame, key: str, title: str, foot: str, path: Path, top_n: Optional[int] = TOP_N_MATRIX
                ) -> Path:
    """Heatmap of counts in the style of the legacy matrices (YlGnBu), each column on its own 0..max scale, no
    conversion; rows = the *top_n* labels by lists (all with None), 'Rogue & unclassified' last."""
    named = tbl[tbl[key] != di.NO_DECK_LABEL].sort_values(["Lists", key], ascending=[False, True])
    if top_n is not None:
        named = named.head(top_n)
    t = pd.concat([named, tbl[tbl[key] == di.NO_DECK_LABEL]]).reset_index(drop=True)
    fig = _fig()
    ax = fig.add_axes([0.25, 0.08, 0.72, 0.72])
    cmap = plt.get_cmap("YlGnBu")
    for j, (col, _head) in enumerate(MATRIX_COLS):
        vmax = max(float(t[col].max()), 1.0)
        for i, v in enumerate(t[col]):
            frac = float(v) / vmax
            ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor=cmap(0.08 + 0.8 * frac), edgecolor="white",
                                       linewidth=2))
            ax.text(j + 0.5, i + 0.5, f"{int(v)}", ha="center", va="center", fontsize=FONT["cell"],
                    color="white" if frac > 0.55 else C_TEXT)
    ax.set_xlim(0, len(MATRIX_COLS))
    ax.set_ylim(len(t), 0)
    ax.set_xticks([j + 0.5 for j in range(len(MATRIX_COLS))])
    ax.set_xticklabels([h for _c, h in MATRIX_COLS], fontsize=FONT["name"], color=C_TEXT)
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(t))])
    ax.set_yticklabels(list(t[key]), fontsize=FONT["name"], color=C_TEXT)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    _fit_left(ax)
    _frame(fig, title, foot, "counts for this week only")
    return _save(fig, path)


# --- Top 8 of the week (B5e) ------------------------------------------------------------------------

def top8_chart(top8: pd.DataFrame, title: str, foot: str, path: Path, colours: Colours) -> Path:
    """Top 8 lists of the week per deck (horizontal bars in the archetype colour), the trophies as a star marker
    with their count; with more than 14 decks the decks with one Top 8 list and no trophy are named in one line under
    the chart."""
    g = top8.groupby("Deck").agg(Top8=("Place", "size"), Trophies=("Winner", "sum"),
                                 Archetype=("Archetype", "first")).reset_index()
    named = g[g["Deck"] != di.NO_DECK_LABEL].sort_values(["Top8", "Trophies", "Deck"], ascending=[False, False, True])
    single = named[(named["Top8"] == 1) & (named["Trophies"] == 0)] if len(named) > 14 else named.iloc[0:0]
    named = named.drop(single.index)
    t = pd.concat([named, g[g["Deck"] == di.NO_DECK_LABEL]]).reset_index(drop=True)
    fig = _fig()
    bottom = 0.08
    if not single.empty:  # decks with one Top 8 list and no trophy: one line under the chart, not a bar each
        text = "\n".join(textwrap.wrap(f"one Top 8 list each ({len(single)} decks): " + ", ".join(single["Deck"]),
                                       115, break_on_hyphens=False))
        fig.text(0.02, 0.07, text, fontsize=FONT["legend"], color=C_TEXT, va="bottom")
        bottom = 0.10 + 0.03 * text.count("\n")
    ax = fig.add_axes([0.24, bottom + 0.02, 0.72, LEGEND_TOP - bottom - 0.02])
    arch = [di.NO_DECK_LABEL if d == di.NO_DECK_LABEL else a for d, a in zip(t["Deck"], t["Archetype"])]
    _hbars(ax, list(t["Deck"]), list(t["Top8"]), [colours.of_archetype(a) for a in arch])
    _fit_left(ax)
    parts = []
    for r in t.itertuples():
        row = [(f"{r.Top8}", {"gap": 12})]
        if r.Trophies:
            row.append(("★", {"color": C_TROPHY, "size": FONT["value"] + 2, "gap": 2}))
            row.append((f"{r.Trophies}", {"color": C_TEXT}))
        parts.append(row)
    _labels(ax, list(t["Top8"]), parts)
    _arch_legend(fig, list(dict.fromkeys(arch)), colours)
    _frame(fig, title, foot, "bar = Top 8 lists this week · ★ = trophies")
    return _save(fig, path)


# --- pilots (B5g) ------------------------------------------------------------------------------------

def pilots_chart(p: pd.DataFrame, title: str, foot: str, path: Path, colours: Colours) -> Path:
    """For the ten decks of the share chart: distinct pilots (bar, archetype colour) and the top pilot's share of
    the deck's lists; decks whose top pilot holds >= 1/3 of the lists are marked."""
    t = p.sort_values(["Pilots", "Lists", "Deck"], ascending=[False, False, True]).reset_index(drop=True)
    fig = _fig()
    ax = fig.add_axes([0.24, 0.10, 0.72, LEGEND_TOP - 0.10])
    _hbars(ax, list(t["Deck"]), list(t["Pilots"]), [colours.of_archetype(a) for a in t["Archetype"]])
    _arch_legend(fig, list(dict.fromkeys(t["Archetype"])), colours)
    _fit_left(ax)
    parts = []
    for r in t.itertuples():
        row = [(f"{r.Pilots} pilots · top pilot {r.Top_pilot_share_pct:.0f}% of {r.Lists} lists", {"gap": 10})]
        if r.Concentrated:
            row.append(("one pilot ≥ 1/3", {"weight": "bold", "color": C_FLAG}))
        parts.append(row)
    _labels(ax, list(t["Pilots"]), parts)
    _frame(fig, title, foot, "bar = distinct pilots this week; marked: one pilot holds at least a third of the lists")
    return _save(fig, path)


# --- new cards (B5f) ----------------------------------------------------------------------------------

def new_cards_chart(nc: pd.DataFrame, title: str, foot: str, path: Path) -> Path:
    """Table chart: deck, card, pilots, zone (SB only for a sideboard card); at most TOP_N_NEW_CARDS rows."""
    fig = _fig()
    _frame(fig, title, foot, "first time the card appears in the deck, played by at least 2 pilots this week")
    if nc.empty:
        fig.text(0.5, 0.5, "No new cards this week", ha="center", va="center", fontsize=FONT["title"], color=C_SOFT)
        return _save(fig, path)
    t = nc.head(TOP_N_NEW_CARDS)
    ax = fig.add_axes([0.04, 0.10, 0.92, 0.74])
    ax.axis("off")
    cols = [("Deck", 0.0, "left"), ("Card", 0.36, "left"), ("Pilots", 0.80, "right"), ("Zone", 0.92, "center")]
    n = len(t) + 1
    h = 1.0 / max(n, 8)
    for c, x, ha in cols:
        ax.text(x, 1 - h / 2, c.lower(), ha=ha, va="center", fontsize=FONT["name"], color=C_SOFT, weight="bold",
                transform=ax.transAxes)
    for i, r in enumerate(t.itertuples(), start=1):
        y = 1 - (i + 0.5) * h
        if i % 2:
            ax.add_patch(plt.Rectangle((0, y - h / 2), 1, h, transform=ax.transAxes, facecolor="#f3f4f6",
                                       edgecolor="none"))
        vals = [r.Deck, r.Card, f"{int(r.Pilots_with_card)}", "SB" if r.Zone != "main" else ""]
        for (c, x, ha), v in zip(cols, vals):
            ax.text(x + (0.005 if ha == "left" else 0), y, v, ha=ha, va="center", fontsize=FONT["value"],
                    color=C_TEXT, transform=ax.transAxes)
    if len(nc) > len(t):
        fig.text(0.04, 0.07, f"{len(nc) - len(t)} more in data_new_cards.csv", fontsize=FONT["legend"], color=C_SOFT)
    return _save(fig, path)


# --- deck results (B6) ----------------------------------------------------------------------------------

def _result_colour(value: float, excludes: int, base: float):
    if not excludes:
        return GREY_CELL
    return C_ABOVE if value > base else C_BELOW


def results_chart(decks: pd.DataFrame, pct_base: float, conv_base: float, title: str, foot: str, path: Path) -> Path:
    """The TOP_N_RESULTS decks with the most lists ('Rogue & unclassified' is not a deck): lists, mean place
    percentile, Top 8 conversion; a result is coloured only when its interval excludes the average."""
    t = decks[decks["Deck"] != di.NO_DECK_LABEL].sort_values(["Lists", "Deck"], ascending=[False, True]).head(
        TOP_N_RESULTS)
    fig = _fig()
    panels = [("lists", "Lists", [C_BAR] * len(t), lambda v: f"{v:.0f}", None),
              ("mean place percentile", "Mean_place_percentile",
               [_result_colour(v, e, pct_base) for v, e in zip(t["Mean_place_percentile"],
                                                                t["Percentile_BH_rejects"])],
               lambda v: f"{v:.1f}", (pct_base, f"average {pct_base:.0f}")),
              ("Top 8 conversion", "Top8_conversion_pct",
               [_result_colour(v, e, conv_base) for v, e in zip(t["Top8_conversion_pct"],
                                                                 t["Conversion_BH_rejects"])],
               lambda v: f"{v:.1f}%", (conv_base, f"average {conv_base:.1f}%"))]
    lefts, width = (0.25, 0.505, 0.76), 0.2
    for k, (head, col, colors, fmt, base) in enumerate(panels):
        ax = fig.add_axes([lefts[k], 0.12, width, 0.64])
        _hbars(ax, list(t["Deck"]), list(t[col]), colors)
        if k:
            ax.set_yticklabels([])
        ax.set_title(head if base is None else f"{head}\n{base[1]}", fontsize=FONT["panel"], color=C_TEXT,
                     loc="left")
        if base is not None:
            ax.axvline(base[0], color=C_SOFT, linewidth=1.2, linestyle="--", zorder=0)
        _labels(ax, list(t[col]), [[(fmt(v), {})] for v in t[col]])
    _frame(fig, title, foot, LEGEND_RESULTS)
    return _save(fig, path)


# --- deck results as deviations from the average with a significance band (m01, 2026-10-27) ---------------

LEGEND_BANDS = ("band = how far a deck must be from the rest of the field to differ from it, band adjusted for "
                "multiple comparisons")
C_BAND = "#eceef1"
C_GREY_BAR = "#9ca3af"  # a bar inside its band (not distinguishable), darker than the band
C_ZERO = "#6b7280"
BAND_COLOURS = {"green": C_UP, "red": C_DOWN, "grey": C_GREY_BAR}  # the bars and the legend swatches of m01
BAND_LEGEND = (("green", "above the rest"), ("red", "below the rest"), ("grey", "not distinguishable"))


def _band_legend(fig) -> list:
    """Swatches of m01 in the colours of its bars (BAND_COLOURS) and of the band; returned for the colour check."""
    handles = [Patch(facecolor=BAND_COLOURS[c], label=text) for c, text in BAND_LEGEND]
    handles.append(Patch(facecolor=C_BAND, edgecolor="#bbbbbb", label="band (adjusted for multiple comparisons)"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.015, 0.845), ncol=len(handles),
               fontsize=FONT["legend"], frameon=False, handlelength=1.2, columnspacing=1.4, borderaxespad=0)
    return handles


RESULT_PANELS = (  # (panel title, value, band low, band high, flag, per-row baseline, axis unit, label formats)
    ("mean place percentile", "Mean_place_percentile", "Percentile_band_low", "Percentile_band_high",
     "Percentile_BH_rejects", "Percentile_baseline_rest", "points vs rest of the field", lambda v: f"{v:.1f}",
     lambda b: f"vs rest of field (all {b:.1f})"),
    ("Top 8 conversion", "Top8_conversion_pct", "Conversion_band_low", "Conversion_band_high", "Conversion_BH_rejects",
     "Conversion_baseline_rest", "pp vs rest of the field", lambda v: f"{v:.1f}%",
     lambda b: f"vs rest of field (all {b:.1f}%)"),
)


def result_bands(t: pd.DataFrame, base, col: str, lo: str, hi: str, flag: str) -> pd.DataFrame:
    """Per row: est, deviation = est - base (*base* a number or the name of a per-row column, e.g. baseline_rest),
    the band around 0 from -(band_high - est) to +(est - band_low) (asymmetric when the interval is), the colour from
    the geometry (green: the bar ends beyond the right edge, red: beyond the left edge, grey: inside; grey without an
    interval and on 'Rogue & unclassified') and the sheet's flag (BH_rejects). A colour that disagrees with the flag
    raises DeckdbInputError (no fallback)."""
    out = []
    for _, r in t.iterrows():
        est = float(r[col])
        b_row = float(r[base]) if isinstance(base, str) else float(base)
        dev = est - b_row
        has_ci = not (pd.isna(r[lo]) or pd.isna(r[hi]))
        left = -(float(r[hi]) - est) if has_ci else float("nan")
        right = est - float(r[lo]) if has_ci else float("nan")
        if r["Deck"] == di.NO_DECK_LABEL or not has_ci:
            colour = "grey"
        else:
            colour = "green" if dev > right else "red" if dev < left else "grey"
        fl = int(r[flag])
        if r["Deck"] != di.NO_DECK_LABEL and (colour != "grey") != bool(fl):
            raise di.DeckdbInputError(f"m01: kolor z geometrii ({colour}) różny od flagi {flag}={fl} dla {r['Deck']} "
                                      f"(est {est:.3f}, baza {b_row:.3f}, CI {r[lo]}..{r[hi]})")
        out.append({"Deck": r["Deck"], "Measure": col, "Baseline": b_row, "Est": est, "Deviation": dev,
                    "CI_low": r[lo], "CI_high": r[hi], "Band_left": left, "Band_right": right,
                    "Colour_geometry": colour, "Flag_sheet": fl,
                    "Note": "" if has_ci or r["Deck"] == di.NO_DECK_LABEL else "low pilot count"})
    return pd.DataFrame(out)


def results_deviation_chart(decks: pd.DataFrame, pct_base: float, conv_base: float, title: str, foot: str,
                            path: Path) -> tuple[Path, pd.DataFrame]:
    """The TOP_N_RESULTS decks with the most lists: lists (bars), then mean place percentile and Top 8 conversion as
    deviations from the rest of the field (baseline_rest: the period's lists without the deck; 0 vertical in the
    middle of the panel, the same symmetric range for every row of a panel), each row with its significance band from
    the pilot-bootstrap interval at level 1 - T of the BH correction (DeckDB K41, change of 2026-10-28; no whiskers);
    the colour must equal BH_rejects. The label is the deck's own value outside the bar or band. Returns (path, the
    control table of every row and panel)."""
    t = decks[decks["Deck"] != di.NO_DECK_LABEL].sort_values(["Lists", "Deck"], ascending=[False, True]).head(
        TOP_N_RESULTS).reset_index(drop=True)
    fig = _fig()
    ax = fig.add_axes([0.25, 0.14, 0.15, 0.58])
    _hbars(ax, list(t["Deck"]), list(t["Lists"]), [C_BAR] * len(t))
    ax.set_title("lists", fontsize=FONT["panel"], color=C_TEXT, loc="left")
    _labels(ax, list(t["Lists"]), [[(f"{v:.0f}", {})] for v in t["Lists"]])
    colour = BAND_COLOURS
    control = []
    for k, (head, col, lo, hi, flag, base_col, unit, fmt, avg) in enumerate(RESULT_PANELS):
        base = pct_base if k == 0 else conv_base
        bar_colours: Dict[str, set] = {}
        b = result_bands(t, base_col, col, lo, hi, flag)
        control.append(b)
        ax = fig.add_axes([0.45 + k * 0.28, 0.14, 0.24, 0.58])
        y = list(range(len(b)))
        for i, r in b.iterrows():
            if not pd.isna(r["Band_left"]):
                ax.barh(i, r["Band_right"] - r["Band_left"], left=r["Band_left"], height=0.84, color=C_BAND,
                        zorder=1, linewidth=0)
            bar = ax.barh(i, r["Deviation"], height=0.5, color=colour[r["Colour_geometry"]], zorder=2)[0]
            bar_colours.setdefault(r["Colour_geometry"], set()).add(matplotlib.colors.to_hex(bar.get_facecolor()))
        ax.axvline(0, color=C_ZERO, linewidth=1.2, zorder=3)
        reach = max([abs(v) for v in list(b["Deviation"]) + list(b["Band_left"].dropna())
                     + list(b["Band_right"].dropna())] + [1e-9])
        ax.set_xlim(-reach * 1.08, reach * 1.08)
        ax.set_ylim(len(b) - 0.5, -0.5)
        ax.set_yticks(y)
        ax.set_yticklabels([])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color("#bbbbbb")
        ax.tick_params(axis="x", labelsize=FONT["footer"], colors=C_SOFT)
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel(unit, fontsize=FONT["footer"], color=C_SOFT)
        ax.set_title(f"{head}\n{avg(base)}", fontsize=FONT["panel"], color=C_TEXT, loc="left")
        texts = []
        for i, r in b.iterrows():  # outside the bar and the band, never on them
            pos = r["Deviation"] >= 0
            edges = [r["Deviation"]] + ([r["Band_right"] if pos else r["Band_left"]]
                                        if not pd.isna(r["Band_left"]) else [])
            x = max(edges) if pos else min(edges)
            txt = fmt(r["Est"]) + (f" · {r['Note']}" if r["Note"] else "")
            texts.append(ax.annotate(txt, xy=(x, i), xytext=(6 if pos else -6, 0), textcoords="offset points",
                                     ha="left" if pos else "right", va="center", fontsize=FONT["value"],
                                     color=C_TEXT, annotation_clip=False, zorder=5))
        for _ in range(40):  # widen the symmetric range until every label is inside the panel
            fig.canvas.draw()
            r_ = fig.canvas.get_renderer()
            box = ax.get_window_extent()
            if all(box.x0 <= t_.get_window_extent(r_).x0 and t_.get_window_extent(r_).x1 <= box.x1 for t_ in texts):
                break
            lim = ax.get_xlim()[1] * 1.08
            ax.set_xlim(-lim, lim)
        legend_hex = {c: matplotlib.colors.to_hex(BAND_COLOURS[c]) for c, _ in BAND_LEGEND}
        bad = {c: v for c, v in bar_colours.items() if v != {legend_hex[c]}}
        if bad:  # a bar drawn in a colour other than its legend swatch
            raise di.DeckdbInputError(f"m01: kolor słupka różny od legendy: {bad} (legenda {legend_hex})")
    _band_legend(fig)
    _frame(fig, title, foot, LEGEND_BANDS)
    return _save(fig, path), pd.concat(control, ignore_index=True)


# --- card layer: list changes (B6) ------------------------------------------------------------------------

def list_changes_chart(tested: pd.DataFrame, mana_only_ids: frozenset, title: str, foot: str, path: Path
                       ) -> tuple[Optional[Path], int, int]:
    """At most TOP_N_LIST_CHANGES list changes by |within-variant component|, mana-only lands left out; bar =
    |component| in pp, green = added, red = cut; label '<deck> · <card>'; a small 'SB' marker for a sideboard card.
    Returns (path, shown, left out as mana-only)."""
    if tested.empty:
        return None, 0, 0
    lc = tested[tested["Category"] == dc.KIND_LIST]
    dropped = int(lc["oracle_id"].isin(mana_only_ids).sum())
    lc = lc[~lc["oracle_id"].isin(mana_only_ids)]
    lc = lc.reindex(lc["Within_variants_pp"].abs().sort_values(ascending=False).index).head(TOP_N_LIST_CHANGES)
    if lc.empty:
        return None, 0, dropped
    fig = _fig()
    ax = fig.add_axes([0.33, 0.11, 0.63, 0.71])
    vals = list(lc["Within_variants_pp"].abs())
    _hbars(ax, [f"{r.Deck} · {r.Card}" for r in lc.itertuples()], vals,
           [C_ADDED if v > 0 else C_CUT for v in lc["Within_variants_pp"]])
    _fit_left(ax)
    marker = {"size": FONT["marker"], "color": C_SOFT,
              "bbox": {"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": C_SOFT, "linewidth": 0.8}}
    parts = []
    for r in lc.itertuples():
        row = [(_pp(r.Within_variants_pp), {"gap": 10})]
        if r.Zone != "main":
            row.append(("SB", marker))
        parts.append(row)
    _labels(ax, vals, parts)
    fig.legend(handles=[Patch(facecolor=C_ADDED, label="added"), Patch(facecolor=C_CUT, label="cut")],
               loc="upper left", bbox_to_anchor=(0.015, 0.895), ncol=2, fontsize=FONT["legend"], frameon=False,
               handlelength=1.2, borderaxespad=0)
    _frame(fig, title, foot)
    return _save(fig, path), len(lc), dropped


def write_notes(out: Path, notes: Dict[str, str]) -> Path:
    """speaker_notes.md: one short plain-English paragraph per chart; the triage marker (forced blocking triage) or
    the pending_notice line on top."""
    path = out / "speaker_notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"## {name}\n\n{text}" for name, text in notes.items())
    mark = f"**{MARK}**\n\n" if MARK else (f"**{NOTICE}**\n\n" if NOTICE else "")
    path.write_text(f"# Speaker notes\n\n{mark}{body}\n", encoding="utf-8")
    return path

