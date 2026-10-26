"""Presentation set of input mode "deckdb" (order of 2026-10-26 b, point 2): ``presentation/`` next to the working
charts of every package (the working charts stay as they are).

Rules for every presentation chart: the legacy style and palette (matplotlib default blue for plain bars, the
classic trend colours green / red / grey for change, the RdBu ends of the legacy matrix for results, the legacy
GREY_CELL for "not distinguishable"); a short plain-language title; one footer line "MTGO Modern Challenge Top 32 ·
<window> · <N> events, <M> lists"; no confidence-interval whiskers -- an interval only decides the colour; horizontal
bars sorted descending, names on the left, numbers outside the bars and never covered; a 16:9 slide figure
(13.333 x 7.5 in at 144 dpi, 1 figure pt = 1 slide pt, no text below 14 pt). No definition, seed, sha256 or rules
version appears in ``presentation/``: they stay in STATUS and GLOSSARY.md; ``presentation/speaker_notes.md`` explains
every chart in one to three plain sentences.

  week     decks_share.png, archetypes_share.png (share, presence, change), trend_decks.png, trend_archetypes.png
  month    trend_decks.png, trend_archetypes.png
  format   deck_results.png (12 decks with the most lists: lists, mean place percentile, Top 8 conversion)
  cards    list_changes.png (at most 12 list changes, mana-only lands left out)
"""

from __future__ import annotations

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
    from statistics_engine import _build_style, _create_trend_chart
except ModuleNotFoundError:
    from . import deckdb_cards as dc
    from . import deckdb_input as di
    from .challenge_history_engine import GREY_CELL
    from .statistics_engine import _build_style, _create_trend_chart

DIR_NAME = "presentation"
DPI = 144
SLIDE = (1920 / DPI, 1080 / DPI)
FONT = {"title": 24, "name": 16, "value": 15, "footer": 14, "legend": 14, "panel": 16, "marker": 11}
TOP_N_RESULTS = 12
TOP_N_LIST_CHANGES = 12
LEGEND_RESULTS = "coloured = different from average; grey = not distinguishable from average"
LEGEND_CHANGE = "change vs previous week: bold colour = beyond normal week-to-week variation"

_TREND = _build_style("classic", None, None)[1]
C_UP, C_DOWN, C_FLAT = _TREND["Rising Deck"], _TREND["Falling Deck"], _TREND["Stable"]
C_BAR = plt.rcParams["axes.prop_cycle"].by_key()["color"][0]   # legacy bars: matplotlib default blue
C_ABOVE, C_BELOW = plt.get_cmap("RdBu")(0.85), plt.get_cmap("RdBu")(0.15)   # legacy matrix scale ends
C_TEXT, C_SOFT = "#222222", "#555555"
POOLED = (di.OTHER_DECKS_LABEL, di.NO_DECK_LABEL)


def footer(start: date, end: date, rows: pd.DataFrame) -> str:
    return (f"MTGO Modern Challenge Top 32 · {start.isoformat()} – {end.isoformat()} · "
            f"{rows['event_id'].nunique()} events, {len(rows)} lists")


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
    for _ in range(25):
        fig.canvas.draw()
        right = max(t.get_window_extent(fig.canvas.get_renderer()).x1 for t in texts) if texts else 0
        if right <= ax.get_window_extent().x1:
            break
        ax.set_xlim(0, ax.get_xlim()[1] + 0.08 * top)


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
    ax.set_xlim(0, max(values) * 1.05 if len(values) else 1.0)
    _bare(ax)


def _pp(v: float) -> str:
    return f"{v:+.1f} pp".replace("-", "−")


# --- week: share, presence and change in one chart ---------------------------------------------

def share_chart(tbl: pd.DataFrame, key: str, title: str, foot: str, path: Path) -> Path:
    """One row per deck (or archetype): bar = share; label '<share>% · <presence>/<events> events · <change>', the
    change bold and green / red only when its interval excludes 0. Pooled rows stay last, in a lighter bar."""
    named = tbl[~tbl[key].isin(POOLED)].sort_values(["Share_Top32_pct", key], ascending=[False, True])
    t = pd.concat([named, tbl[tbl[key] == di.OTHER_DECKS_LABEL], tbl[tbl[key] == di.NO_DECK_LABEL]])
    fig = _fig()
    ax = fig.add_axes([0.24, 0.11, 0.72, 0.74])
    colors = [(*matplotlib.colors.to_rgb(C_BAR), 0.45) if k in POOLED else C_BAR for k in t[key]]
    _hbars(ax, list(t[key]), list(t["Share_Top32_pct"]), colors)
    _fit_left(ax)
    parts = []
    for r in t.itertuples():
        head = f"{r.Share_Top32_pct:.1f}% · {r.Events_with_list}/{r.Week_events} events · "
        if r.Change_CI_excludes_0:
            change = {"weight": "bold", "color": C_UP if r.Change_pp > 0 else C_DOWN}
        else:
            change = {"color": C_SOFT}
        parts.append([(head, {}), (_pp(r.Change_pp), change)])
    _labels(ax, list(t["Share_Top32_pct"]), parts)
    _frame(fig, title, foot, LEGEND_CHANGE)
    return _save(fig, path)


def trend_chart(trend: pd.DataFrame, current: pd.DataFrame, key: str, title: str, foot: str, legend_title: str,
                path: Path) -> Optional[Path]:
    """The working trend chart (legacy _create_trend_chart, same lines) with a plain title and one footer line."""
    if trend.empty:
        return None
    weeks = sorted(trend["ISO_week"].unique())
    idx = {w: i + 1 for i, w in enumerate(weeks)}
    hist = trend.rename(columns={key: "Deck"}).assign(WeekIndex=trend["ISO_week"].map(idx), Level=key)
    cur = current.rename(columns={key: "Deck Display Name", "Share_Top32_pct": "Meta"})
    path.parent.mkdir(parents=True, exist_ok=True)
    p, _ = _create_trend_chart(
        hist, cur, len(weeks), len(weeks), "deckdb", key, path.parent, *_build_style("classic", None, None)[1:],
        value_col="Share_Top32_pct", title=title, xlabel="", ylabel="% of Top 32 lists",
        week_labels={i: w for w, i in idx.items()}, show_trend_status=False, legend_title=legend_title,
        footer=foot, out_path=path, figsize=SLIDE, dpi=DPI,
        font_sizes={"title": FONT["title"], "label": FONT["name"], "tick": FONT["value"], "legend": FONT["value"],
                    "legend_title": FONT["name"], "footer": FONT["footer"], "bottom": 0.24})
    return p


# --- format period: deck results -------------------------------------------------------------------

def _result_colour(value: float, excludes: int, base: float):
    if not excludes:
        return GREY_CELL
    return C_ABOVE if value > base else C_BELOW


def results_chart(decks: pd.DataFrame, pct_base: float, conv_base: float, title: str, foot: str, path: Path) -> Path:
    """The TOP_N_RESULTS decks with the most lists ('no deck' is not a deck): lists, mean place percentile, Top 8
    conversion; a result is coloured only when its interval excludes the average."""
    t = decks[decks["Deck"] != di.NO_DECK_LABEL].sort_values(["Lists", "Deck"], ascending=[False, True]).head(
        TOP_N_RESULTS)
    fig = _fig()
    panels = [("lists", "Lists", [C_BAR] * len(t), lambda v: f"{v:.0f}", None),
              ("mean place percentile", "Mean_place_percentile",
               [_result_colour(v, e, pct_base) for v, e in zip(t["Mean_place_percentile"],
                                                                t["Percentile_CI_excludes_baseline"])],
               lambda v: f"{v:.1f}", (pct_base, f"average {pct_base:.0f}")),
              ("Top 8 conversion", "Top8_conversion_pct",
               [_result_colour(v, e, conv_base) for v, e in zip(t["Top8_conversion_pct"],
                                                                 t["Conversion_CI_excludes_baseline"])],
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


# --- card layer: list changes ------------------------------------------------------------------------

def list_changes_chart(tested: pd.DataFrame, mana_only_ids: frozenset, title: str, foot: str, path: Path
                       ) -> tuple[Optional[Path], int, int]:
    """At most TOP_N_LIST_CHANGES list changes by |within-variant component|, mana-only lands left out; bar =
    |component| in pp, green = added, red = cut; a small 'SB' marker for a sideboard card. Returns (path, shown,
    left out as mana-only)."""
    if tested.empty:
        return None, 0, 0
    lc = tested[tested["Category"] == dc.KIND_LIST]
    dropped = int(lc["oracle_id"].isin(mana_only_ids).sum())
    lc = lc[~lc["oracle_id"].isin(mana_only_ids)]
    lc = lc.reindex(lc["Within_variants_pp"].abs().sort_values(ascending=False).index).head(TOP_N_LIST_CHANGES)
    if lc.empty:
        return None, 0, dropped
    fig = _fig()
    ax = fig.add_axes([0.33, 0.11, 0.63, 0.74])
    vals = list(lc["Within_variants_pp"].abs())
    _hbars(ax, [f"{r.Deck} · {r.Card}" for r in lc.itertuples()], vals,
           [C_UP if v > 0 else C_DOWN for v in lc["Within_variants_pp"]])
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
    ax.legend(handles=[Patch(facecolor=C_UP, label="added"), Patch(facecolor=C_DOWN, label="cut")],
              loc="lower right", fontsize=FONT["legend"], frameon=False)
    _frame(fig, title, foot)
    return _save(fig, path), len(lc), dropped


def write_notes(out: Path, notes: Dict[str, str]) -> Path:
    """presentation/speaker_notes.md: one short plain-English paragraph per chart."""
    path = out / "speaker_notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"## {name}\n\n{text}" for name, text in notes.items())
    path.write_text(f"# Speaker notes\n\n{body}\n", encoding="utf-8")
    return path
