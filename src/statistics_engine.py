from __future__ import annotations

from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Iterable, Optional
import shutil
import re

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

PALETTE_PRESETS: dict[str, dict[str, dict[str, str]]] = {
    "classic": {
        "performance": {
            "Underplayed Winner": "#b9f03a",
            "Popular Trap": "#f07431",
            "Neutral": "#000000",
        },
        "trend": {
            "Rising Deck": "#2ecc71",
            "Falling Deck": "#e74c3c",
            "Stable": "#95a5a6",
        },
    },
    "warm": {
        "performance": {
            "Underplayed Winner": "#f2c14e",
            "Popular Trap": "#e76f51",
            "Neutral": "#3d405b",
        },
        "trend": {
            "Rising Deck": "#e9c46a",
            "Falling Deck": "#d62828",
            "Stable": "#6c757d",
        },
    },
    "neon": {
        "performance": {
            "Underplayed Winner": "#39ff14",
            "Popular Trap": "#ff5400",
            "Neutral": "#2b2d42",
        },
        "trend": {
            "Rising Deck": "#00f5d4",
            "Falling Deck": "#ff006e",
            "Stable": "#8d99ae",
        },
    },
    "colorblind": {
        "performance": {
            "Underplayed Winner": "#0072b2",
            "Popular Trap": "#d55e00",
            "Neutral": "#4d4d4d",
        },
        "trend": {
            "Rising Deck": "#009e73",
            "Falling Deck": "#d55e00",
            "Stable": "#999999",
        },
    },
}

TREND_THRESHOLD_DECK = 0.5
TREND_THRESHOLD_ARCHETYPE = 0.2
MIN_GAMES_FOR_WINRATE_CHARTS = 50
# Always drawn on the expected-wins chart, even under MIN_GAMES_FOR_WINRATE_CHARTS (labelled with
# its real n, so the wide CI says how much to trust it). Requested 2026-10-12.
ALWAYS_SHOW_WINRATE_DECKS: tuple[str, ...] = ("Domain Zoo",)
PREP_PRIORITY_COLORS: dict[str, str] = {
    "Very High Prep Priority": "red",
    "High Prep Priority": "orange",
    "Medium Prep Priority": "blue",
    "Low Prep Priority": "green",
}


@dataclass
class StatisticsRunResult:
    output_dir: Path
    input_excel: Path
    history_csv: Path
    week_index: int
    deck_rows: int
    archetype_rows: int
    files: list[Path]


def available_palettes() -> list[str]:
    return list(PALETTE_PRESETS.keys())


def _normalize_hex_color(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) != 7:
        return None
    hex_part = text[1:]
    if any(ch not in "0123456789abcdefABCDEF" for ch in hex_part):
        return None
    return f"#{hex_part.lower()}"


def _normalize_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _extract_week_range_from_input(input_excel: Path) -> tuple[Optional[str], Optional[str]]:
    """Parse week range from parent run directory name.

    Supports both naming styles:
      - WNN_YYYY-MM-DD_to_YYYY-MM-DD
      - YYYY-MM-DD_to_YYYY-MM-DD
    """
    run_dir_name = input_excel.parent.name
    match = re.search(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})", run_dir_name)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _build_style(
    palette_name: str,
    deck_colors: Optional[dict[str, str]],
    legend_colors: Optional[dict[str, str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    selected = PALETTE_PRESETS.get(str(palette_name or "").strip().lower(), PALETTE_PRESETS["classic"])
    performance_colors = dict(selected["performance"])
    trend_colors = dict(selected["trend"])
    deck_map: dict[str, str] = {}
    legend_map: dict[str, str] = {}
    for name, color in (deck_colors or {}).items():
        normalized_name = _normalize_key(name)
        normalized_color = _normalize_hex_color(str(color))
        if normalized_name and normalized_color:
            deck_map[normalized_name] = normalized_color
    for name, color in (legend_colors or {}).items():
        normalized_name = _normalize_key(name)
        normalized_color = _normalize_hex_color(str(color))
        if normalized_name and normalized_color:
            legend_map[normalized_name] = normalized_color
    return performance_colors, trend_colors, deck_map, legend_map


def _deck_color_override(deck_name: str, deck_colors: dict[str, str]) -> Optional[str]:
    return deck_colors.get(_normalize_key(deck_name))


def _legend_color(label: str, legend_colors: dict[str, str], fallback: str) -> str:
    return legend_colors.get(_normalize_key(label), fallback)


def prep_priority(quartile: str) -> str:
    mapping = {
        "Q4": "Very High Prep Priority",
        "Q3": "High Prep Priority",
        "Q2": "Medium Prep Priority",
        "Q1": "Low Prep Priority",
    }
    return mapping.get(quartile, "Low Prep Priority")


def trend_label(current: float, past_avg: float, threshold: float = 1.0) -> str:
    if current - past_avg > threshold:
        return "Rising Deck"
    if current - past_avg < -threshold:
        return "Falling Deck"
    return "Stable"


DEFAULT_TREND_LOOKBACK_WEEKS = 4


def resolve_trend_status(
    history_meta: Iterable[float],
    current: float,
    threshold: float,
    lookback_weeks: int = DEFAULT_TREND_LOOKBACK_WEEKS,
) -> str:
    """Single source of truth for trend direction. _create_trend_chart (PNG legends) and
    run_statistics (the tabular Trend Label column) both call this, for both Deck and Archetype
    levels -- before 2026-08-24 they computed direction two different ways (this function's
    4-week average vs. a full-window average in the PNG path) and could disagree for the same
    deck in the same document (e.g. Broodscale Combo: legend ^, table "Falling Deck" for the
    2026-08-10..08-23 window).

    `history_meta` must already be scoped to one key and one Level, in chronological
    (oldest-first) order, and must NOT include the current week's own value -- comparing a week
    against itself always reads "Stable" and would mask real movement.

    Base = mean of the most recent `lookback_weeks` entries (fewer if less history exists, matching
    the pre-existing Deck-level behavior); with no prior history at all for the key, the direction
    is "Stable" rather than guessed at.
    """
    values = [float(v) for v in history_meta if v is not None and pd.notna(v)]
    if not values:
        return "Stable"
    window = values[-lookback_weeks:]
    past_avg = sum(window) / len(window)
    return trend_label(float(current), past_avg, threshold=threshold)


def calculate_binomial_records(winrate: float, rounds: int) -> dict[str, float]:
    p = max(0.0, min(1.0, float(winrate)))
    out: dict[str, float] = {}
    for wins in range(rounds + 1):
        losses = rounds - wins
        out[f"{wins}-{losses}"] = comb(rounds, wins) * (p**wins) * ((1 - p) ** losses)
    return out


def hypergeometric_probability(total_players: int, copies: int, sample_size: int, threshold: int = 1) -> float:
    if total_players <= 0 or sample_size <= 0 or threshold <= 0:
        return 0.0
    sample_size = min(sample_size, total_players)
    max_successes = min(copies, sample_size)
    threshold = min(threshold, max_successes)
    total = comb(total_players, sample_size)
    if total == 0:
        return 0.0
    probability = 0.0
    for k in range(threshold, max_successes + 1):
        probability += comb(copies, k) * comb(total_players - copies, sample_size - k)
    return probability / total


def _normalize_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    def as_float(series: pd.Series, percent_like: bool = False) -> pd.Series:
        raw = series.astype(str)
        cleaned = raw.str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
        vals = pd.to_numeric(cleaned, errors="coerce")
        if percent_like and (raw.str.contains("%", regex=False).any() or (vals.notna().any() and vals.max() > 1)):
            vals = vals / 100.0
        return vals

    if "Meta" in out.columns:
        out["Meta"] = as_float(out["Meta"], percent_like=False)
    if "Winrate" in out.columns:
        out["Winrate"] = as_float(out["Winrate"], percent_like=True)

    return out


def _calculate_metrics(df: pd.DataFrame, total_players: int, rounds: int) -> pd.DataFrame:
    out = df.copy()
    out["Encounter Copies"] = (total_players * out["Meta"] / 100).round().astype(int)
    out["Encounter Probability"] = out.apply(
        lambda r: hypergeometric_probability(total_players, int(r["Encounter Copies"]), rounds, 1), axis=1
    )

    max_meta = out["Meta"].max() if pd.notna(out["Meta"].max()) and out["Meta"].max() > 0 else 1
    min_wr = out["Winrate"].min()
    max_wr = out["Winrate"].max()
    wr_range = (max_wr - min_wr) if pd.notna(max_wr) and pd.notna(min_wr) and (max_wr - min_wr) > 0 else 1

    out["Importance"] = 0.7 * (out["Meta"] / max_meta) + 0.3 * ((out["Winrate"] - min_wr) / wr_range)
    # No usable winrate (NaN, see challenge_meta.WR_FLAG_*): the Meta weight is rescaled to 1 so the
    # deck still gets an Importance / Quartile / Prep Priority (decision 2026-10-12).
    no_wr = out["Winrate"].isna()
    out.loc[no_wr, "Importance"] = out.loc[no_wr, "Meta"] / max_meta
    out["Importance_Flag"] = np.where(no_wr, "meta_only", "")
    q1, q2, q3 = out["Importance"].quantile([0.25, 0.5, 0.75])

    def quartile_label(val: float) -> str:
        if pd.isna(val):
            return ""
        if val <= q1:
            return "Q1"
        if val <= q2:
            return "Q2"
        if val <= q3:
            return "Q3"
        return "Q4"

    out["Quartile"] = out["Importance"].apply(quartile_label)
    out["Prep Priority"] = out["Quartile"].apply(lambda q: prep_priority(q) if q else "")

    meta_med = out["Meta"].median()
    winrate_med = out["Winrate"].median()

    def perf(row: pd.Series) -> str:
        if pd.isna(row["Winrate"]):
            return ""
        if row["Meta"] < meta_med and row["Winrate"] > winrate_med:
            return "Underplayed Winner"
        if row["Meta"] > meta_med and row["Winrate"] < winrate_med:
            return "Popular Trap"
        return "Neutral"

    out["Performance Label"] = out.apply(perf, axis=1)
    return out


def _aggregate_by_archetype(df_results: pd.DataFrame) -> pd.DataFrame:
    df = df_results.copy()
    if "Archetype" not in df.columns:
        df["Archetype"] = "Rogue"
    else:
        df["Archetype"] = df["Archetype"].fillna("Rogue").astype(str).replace("<NA>", "Rogue")

    rows = []
    for archetype, group in df.groupby("Archetype", dropna=False):
        meta = pd.to_numeric(group["Meta"], errors="coerce").fillna(0)
        wr = pd.to_numeric(group["Winrate"], errors="coerce")
        known = wr.notna()

        if known.any() and meta[known].sum() > 0:
            wr_value = float((wr[known] * meta[known]).sum() / meta[known].sum())
        else:
            wr_value = float(wr.dropna().mean()) if known.any() else float("nan")

        row = {
            "Archetype": str(archetype),
            "Meta": float(meta.sum()),
            "Winrate": wr_value,
            "Deck Display Name": str(archetype),
        }
        if "MetaSource" in group.columns:
            row["MetaSource"] = str(group["MetaSource"].dropna().astype(str).iloc[0]) if group["MetaSource"].notna().any() else ""
        if "Meta_Videre" in group.columns:
            row["Meta_Videre"] = float(pd.to_numeric(group["Meta_Videre"], errors="coerce").sum(min_count=1))
        if "WR_Flag" in group.columns:
            missing = group.loc[~known.values, "Deck"].astype(str).tolist() if "Deck" in group.columns else []
            row["WR_Flag"] = (
                ("winrate excludes (no usable winrate): " + ", ".join(sorted(missing))) if missing and known.any()
                else ("no usable winrate" if missing else "")
            )
        rows.append(row)

    return pd.DataFrame(rows).sort_values("Meta", ascending=False).reset_index(drop=True)


def _create_encounter_chart(
    df: pd.DataFrame,
    file_tag: str,
    total_players: int,
    chart_type: str,
    output_dir: Path,
    min_encounter_threshold: float,
    performance_colors: dict[str, str],
    deck_colors: dict[str, str],
    legend_colors: dict[str, str],
) -> Optional[Path]:
    show_df = df[df["Encounter Probability"] >= min_encounter_threshold].copy()
    if show_df.empty:
        return None

    show_df = show_df.sort_values("Encounter Probability", ascending=False).reset_index(drop=True)
    count = len(show_df)
    fig, ax = plt.subplots(figsize=(20, 12))

    cmap = plt.cm.rainbow
    base_colors = cmap(np.linspace(1, 0, count)) if count > 0 else []
    colors = []
    for i, (_, row) in enumerate(show_df.iterrows()):
        deck_name = str(row.get("Deck Display Name") or row.get("Deck") or "")
        override = _deck_color_override(deck_name, deck_colors)
        colors.append(override if override is not None else base_colors[i])
    bars = ax.bar(range(count), show_df["Encounter Probability"], color=colors, alpha=0.8, edgecolor="black", linewidth=0.5)

    for bar, prob, prep in zip(bars, show_df["Encounter Probability"], show_df["Prep Priority"]):
        label = f"{float(prob):.1%}"
        prep_color = _legend_color(str(prep), legend_colors, PREP_PRIORITY_COLORS.get(str(prep), "black"))
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.015, label, ha="center", va="bottom", fontsize=11, color=prep_color)

    rotation_angle = 30 if count < 12 else 45 if count < 20 else 60
    for i, (deck, perf_label) in enumerate(zip(show_df["Deck Display Name"], show_df["Performance Label"])):
        ax.text(
            i,
            -0.03,
            str(deck),
            ha="right",
            va="top",
            fontsize=9,
            rotation=rotation_angle,
            color=_legend_color(str(perf_label), legend_colors, performance_colors.get(str(perf_label), "black")),
        )

    ax.tick_params(axis="x", which="both", length=0, labelbottom=False)
    ax.spines["bottom"].set_visible(False)
    ax.set_ylabel("Encounter Probability", fontsize=14)
    threshold_text = f" (min. {min_encounter_threshold:.1%})" if min_encounter_threshold > 0 else ""
    ax.set_title(f"Encounter Probability ({chart_type}){threshold_text} (N={total_players}) - {file_tag}", fontsize=16, pad=20)
    ax.set_ylim(0, 1.05)

    perf_patches = [
        Patch(
            facecolor=_legend_color("Underplayed Winner", legend_colors, performance_colors["Underplayed Winner"]),
            label="Underplayed Winner",
        ),
        Patch(
            facecolor=_legend_color("Popular Trap", legend_colors, performance_colors["Popular Trap"]),
            label="Popular Trap",
        ),
        Patch(
            facecolor=_legend_color("Neutral", legend_colors, performance_colors["Neutral"]),
            label="Neutral",
        ),
    ]
    prep_patches = [
        Patch(facecolor=_legend_color("Very High Prep Priority", legend_colors, "red"), label="Very High"),
        Patch(facecolor=_legend_color("High Prep Priority", legend_colors, "orange"), label="High"),
        Patch(facecolor=_legend_color("Medium Prep Priority", legend_colors, "blue"), label="Medium"),
        Patch(facecolor=_legend_color("Low Prep Priority", legend_colors, "green"), label="Low"),
    ]

    leg1 = ax.legend(handles=prep_patches, title="Prep Priority", loc="upper right", bbox_to_anchor=(0.98, 0.98), fontsize=10)
    leg2 = ax.legend(handles=perf_patches, title="Performance Colors", loc="upper right", bbox_to_anchor=(0.98, 0.78), fontsize=10)
    ax.add_artist(leg1)

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.45, right=0.85)
    out_path = output_dir / f"encounter_prob_{chart_type}_{file_tag}.png"
    plt.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _wilson_ci(p_hat: float, n: float, z: float = 1.959963984540054) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion. Not symmetric around p_hat -- see
    the record-probability chart, which draws it as asymmetric yerr bars for exactly that reason.
    n<=0 collapses to (p_hat, p_hat): a game-count-less deck gets no interval, not a fake one.
    """
    if n <= 0:
        return p_hat, p_hat
    denom = 1.0 + (z * z) / n
    center = (p_hat + (z * z) / (2 * n)) / denom
    margin = (z * ((p_hat * (1 - p_hat) / n + (z * z) / (4 * n * n)) ** 0.5)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _create_record_probability_chart(
    df_results: pd.DataFrame,
    rounds: int,
    file_tag: str,
    output_dir: Path,
    deck_colors: dict[str, str],
    legend_colors: dict[str, str],
) -> Optional[Path]:
    n_bars = int((pd.to_numeric(df_results.get("Winrate Game Count"), errors="coerce") >= MIN_GAMES_FOR_WINRATE_CHARTS).sum()) if "Winrate Game Count" in df_results.columns else 20
    fig, ax2 = plt.subplots(1, 1, figsize=(max(20, 0.7 * n_bars), 12))
    fig.suptitle(f"Expected Wins per Deck - {file_tag} ({rounds}-round event)", fontsize=16, y=0.99)

    games_col = None
    for candidate in ["Winrate Game Count", "Match Count", "Matches", "Games"]:
        if candidate in df_results.columns:
            games_col = candidate
            break

    eligible = pd.DataFrame()
    no_winrate_names: list[str] = []
    if games_col is not None:
        ranked = df_results.copy()
        ranked["_wr"] = pd.to_numeric(ranked["Winrate"], errors="coerce")
        # A deck whose winrate is unusable (NaN, see challenge_meta.WR_FLAG_*) is never drawn --
        # not even at a fake 50% -- it is listed in the footnote below instead.
        no_wr = ranked["_wr"].isna()
        no_winrate_names = ranked.loc[no_wr].sort_values("Meta", ascending=False)["Deck Display Name"].astype(str).tolist()
        ranked = ranked[~no_wr].sort_values("_wr", ascending=False)
        ranked["_games"] = pd.to_numeric(ranked[games_col], errors="coerce").fillna(0)
        always = ranked["Deck Display Name"].astype(str).isin(ALWAYS_SHOW_WINRATE_DECKS) | ranked["Deck"].astype(str).isin(ALWAYS_SHOW_WINRATE_DECKS)
        eligible = ranked[(ranked["_games"] >= MIN_GAMES_FOR_WINRATE_CHARTS) | always].copy()

    if eligible.empty:
        ax2.set_title(
            f"Expected Wins per Deck\nOnly decks with >= {MIN_GAMES_FOR_WINRATE_CHARTS} games",
            fontsize=13,
        )
        ax2.set_ylabel("Expected Wins", fontsize=14)
        ax2.set_ylim(0, rounds + 0.5)
        ax2.grid(axis="y", alpha=0.3, linestyle="--")
        if games_col is None:
            msg = "No game-count column found in input data."
        else:
            msg = f"No decks reached >= {MIN_GAMES_FOR_WINRATE_CHARTS} games."
        ax2.text(
            0.5,
            0.5,
            msg,
            transform=ax2.transAxes,
            ha="center",
            va="center",
            fontsize=11,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )
    else:
        # No top-N cut (was head(20)): every deck with enough games is drawn.
        top = eligible.reset_index(drop=True)
        winrates = top["_wr"].astype(float)
        expected_wins = winrates * rounds
        ci_lo = []
        ci_hi = []
        for wr, n in zip(winrates, top["_games"]):
            lo, hi = _wilson_ci(float(wr), float(n))
            ci_lo.append(lo * rounds)
            ci_hi.append(hi * rounds)
        yerr_lo = [max(0.0, w - lo) for w, lo in zip(expected_wins, ci_lo)]
        yerr_hi = [max(0.0, hi - w) for w, hi in zip(expected_wins, ci_hi)]

        top_colors = []
        for _, row in top.iterrows():
            wr = float(row["_wr"])
            top_colors.append(plt.cm.RdYlGn(plt.Normalize(vmin=0, vmax=1)(wr)))
        count = len(top)
        ax2.bar(
            range(count),
            expected_wins,
            yerr=[yerr_lo, yerr_hi],
            color=top_colors,
            edgecolor="black",
            linewidth=0.5,
            alpha=0.9,
            error_kw=dict(ecolor="black", elinewidth=1, capsize=3),
        )
        ax2.set_xticks(range(count))
        rotation_angle = 30 if count < 12 else 45 if count < 20 else 60
        labels = [
            f"{str(deck)} ({wr:.0%}, CI {lo * 100:.0f} to {hi * 100:.0f}, n={int(g)})"
            for deck, wr, lo, hi, g in zip(
                top["Deck Display Name"].astype(str), winrates,
                [c / rounds for c in ci_lo], [c / rounds for c in ci_hi], top["_games"],
            )
        ]
        ax2.set_xticklabels(labels, fontsize=9, rotation=rotation_angle, ha="right")
        ax2.set_ylabel("Expected Wins", fontsize=14)
        ax2.set_title(
            f"Based on deck winrate + 95% Wilson CI\n{rounds} rounds | min {MIN_GAMES_FOR_WINRATE_CHARTS} games",
            fontsize=13,
        )
        ax2.set_ylim(0, rounds + 1.0)
        ax2.grid(axis="y", alpha=0.3, linestyle="--")
        for i, (wins, hi) in enumerate(zip(expected_wins, ci_hi)):
            ax2.text(i, float(hi) + 0.1, f"{float(wins):.2f}", ha="center", va="bottom", fontsize=9)

    if no_winrate_names:
        flags = ""
        if "WR_Flag" in df_results.columns:
            flag_set = sorted({str(f) for f in df_results.loc[df_results["Deck Display Name"].astype(str).isin(no_winrate_names), "WR_Flag"] if str(f) not in ("", "nan")})
            flags = f" ({', '.join(flag_set)})" if flag_set else ""
        fig.text(
            0.01, 0.005,
            f"Not drawn, winrate unusable{flags}: " + ", ".join(no_winrate_names),
            ha="left", va="bottom", fontsize=10, style="italic",
        )

    sm2 = plt.cm.ScalarMappable(cmap=plt.cm.RdYlGn, norm=plt.Normalize(vmin=0, vmax=1))
    sm2.set_array([])
    cbar2 = plt.colorbar(sm2, ax=ax2, orientation="vertical", fraction=0.03, pad=0.02)
    cbar2.set_label("Deck Winrate", fontsize=10)
    cbar2.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cbar2.set_ticklabels(["0%", "25%", "50%", "75%", "100%"])

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.4)
    out_path = output_dir / f"record_probability_{file_tag}.png"
    plt.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _build_record_probability_excel(
    df_results: pd.DataFrame,
    rounds: int,
    file_tag: str,
    output_dir: Path,
) -> Path:
    labels = [f"{w}-{rounds - w}" for w in range(rounds, -1, -1)]
    rows: list[dict[str, str]] = []

    field_winrates = pd.to_numeric(df_results.get("Winrate"), errors="coerce").dropna()
    field_median_wr = float(field_winrates.median()) if not field_winrates.empty else 0.5
    field_probs = calculate_binomial_records(field_median_wr, rounds)
    field_row = {
        "Deck": "Field Median (median deck winrate, this window)",
        "Meta": "",
        "Deck Winrate": f"{field_median_wr:.1%}",
    }
    for label in labels:
        field_row[label] = f"{field_probs.get(label, 0.0):.1%}"
    rows.append(field_row)

    has_flag = "WR_Flag" in df_results.columns
    if has_flag:
        field_row["WR_Flag"] = ""
    for _, row in df_results.sort_values("Meta", ascending=False).iterrows():
        wr_raw = pd.to_numeric(row.get("Winrate"), errors="coerce")
        item = {
            "Deck": str(row.get("Deck Display Name", row.get("Deck", ""))),
            "Meta": f"{float(pd.to_numeric(row.get('Meta'), errors='coerce') or 0.0):.1f}%",
            "Deck Winrate": f"{float(wr_raw):.1%}" if pd.notna(wr_raw) else float("nan"),
        }
        probs = calculate_binomial_records(float(wr_raw), rounds) if pd.notna(wr_raw) else {}
        for label in labels:
            item[label] = f"{probs[label]:.1%}" if label in probs else float("nan")
        if has_flag:
            flag = row.get("WR_Flag")
            item["WR_Flag"] = "" if flag is None or pd.isna(flag) else str(flag)
        rows.append(item)

    out_path = output_dir / f"record_probabilities_{file_tag}.xlsx"
    pd.DataFrame(rows).to_excel(out_path, index=False)
    return out_path


def _create_trend_chart(
    df_history: pd.DataFrame,
    df_current: pd.DataFrame,
    weeks_back: int,
    week_index: int,
    file_tag: str,
    chart_type: str,
    output_dir: Path,
    trend_colors: dict[str, str],
    deck_colors: dict[str, str],
    legend_colors: dict[str, str],
    caption: Optional[str] = None,
    value_col: str = "Meta",
    title: Optional[str] = None,
    xlabel: str = "Week",
    ylabel: str = "Meta %",
    week_labels: Optional[dict[int, str]] = None,
    show_trend_status: bool = True,
    legend_title: str = "Decks (Trend Status)",
    footer: Optional[str] = None,
    out_path: Optional[Path] = None,
    figsize: tuple = (18, 11),
    dpi: int = 300,
) -> tuple[Optional[Path], dict[str, str]]:
    """Returns (png_path, status_by_key): status_by_key is the exact direction used to build
    each legend entry (the trend_symbol computation below), keyed by Deck/Archetype display
    name -- so callers can cross-check the tabular Trend Label column against what the legend
    actually shows, instead of re-deriving it separately (see run_statistics's post-generation
    guard). Empty dict whenever no chart was produced.

    The keyword arguments after *caption* default to the legacy chart. Input mode "deckdb" uses
    them for its share-of-Top-32 trend: own value column and labels, ISO-week tick labels
    (*week_labels*), no trend status (*show_trend_status*=False), a *footer* and an explicit path.
    """
    if df_history.empty or "WeekIndex" not in df_history.columns:
        return None, {}

    if chart_type == "Archetype":
        if "Level" not in df_history.columns:
            return None, {}
        level_hist = df_history[df_history["Level"] == "Archetype"].copy()
    else:
        if "Level" in df_history.columns:
            level_hist = df_history[(df_history["Level"] == "Deck") | (df_history["Level"].isna())].copy()
        else:
            level_hist = df_history.copy()

    if level_hist.empty or "Deck" not in level_hist.columns:
        return None, {}

    min_week = max(1, int(week_index - weeks_back + 1))
    hist = level_hist[pd.to_numeric(level_hist["WeekIndex"], errors="coerce") >= min_week].copy()
    if hist.empty:
        return None, {}

    top = df_current.sort_values("Meta", ascending=False).head(10)["Deck Display Name"].astype(str).tolist()
    hist = hist[hist["Deck"].astype(str).isin(top)].copy()
    if hist.empty:
        return None, {}

    fig, ax = plt.subplots(figsize=figsize)
    cmap = plt.cm.rainbow
    color_count = max(1, len(top) - 1)
    base_deck_colors = {deck: cmap(1 - i / color_count) for i, deck in enumerate(top)}

    legend_elements = []
    status_by_key: dict[str, str] = {}
    threshold = TREND_THRESHOLD_ARCHETYPE if chart_type == "Archetype" else TREND_THRESHOLD_DECK
    for deck in top:
        series = hist[hist["Deck"].astype(str) == deck].sort_values("WeekIndex")
        if series.empty:
            continue
        values = pd.to_numeric(series[value_col], errors="coerce").fillna(0)
        weeks = pd.to_numeric(series["WeekIndex"], errors="coerce").astype(int)
        line_color = _deck_color_override(deck, deck_colors) or base_deck_colors[deck]
        if not show_trend_status:
            ax.plot(weeks, values, marker="o", linewidth=2.5, markersize=8, color=line_color)
            legend_elements.append(
                Line2D([0], [0], color=line_color, linewidth=2.5, marker="o", markersize=6, label=deck)
            )
            continue

        # Direction is resolved from level_hist (the FULL, un-windowed history for this key),
        # not from `hist` above (already sliced to the weeks_back display window) -- otherwise
        # the trend arrow would silently depend on the chart's weeks_back parameter instead of
        # the shared 4-week definition in resolve_trend_status, and could disagree with the
        # tabular Trend Label column whenever weeks_back != 4.
        full_series = level_hist[level_hist["Deck"].astype(str) == deck].sort_values("WeekIndex")
        full_weeks = pd.to_numeric(full_series["WeekIndex"], errors="coerce")
        full_values = pd.to_numeric(full_series["Meta"], errors="coerce")
        current_week_mask = full_weeks == week_index
        if current_week_mask.any():
            current = float(full_values[current_week_mask].iloc[-1])
            prior_values = full_values[~current_week_mask].dropna().tolist()
        elif not full_values.empty:
            current = float(full_values.iloc[-1])
            prior_values = full_values.iloc[:-1].dropna().tolist()
        else:
            current = 0.0
            prior_values = []
        status = resolve_trend_status(prior_values, current, threshold=threshold)
        status_by_key[deck] = status
        trend_symbol = "^" if status == "Rising Deck" else "v" if status == "Falling Deck" else "-"
        ax.plot(weeks, values, marker="o", linewidth=2.5, markersize=8, color=line_color)
        legend_elements.append(
            Line2D([0], [0], color=line_color, linewidth=2.5, marker="o", markersize=6, label=f"{deck} {trend_symbol}")
        )

    title = title or f"Meta Trend ({chart_type}) - Last {weeks_back} Weeks"
    ax.set_title(f"{title}\n{caption}" if caption else title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", alpha=0.3)

    if week_labels:
        ticks = sorted(w for w in week_labels if w >= min_week)
        ax.set_xticks(ticks)
        ax.set_xticklabels([week_labels[w] for w in ticks], rotation=35, ha="right")
    else:
        ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax.legend(handles=legend_elements, title=legend_title, loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9, framealpha=0.95, title_fontsize=10)

    if show_trend_status:
        trend_box_text = "^ Rising  |  - Stable  |  v Falling"
        box_face = _legend_color("Trend Box", legend_colors, "wheat")
        ax.text(0.5, -0.08, trend_box_text, transform=ax.transAxes, ha="center", fontsize=10, bbox=dict(boxstyle="round", facecolor=box_face, alpha=0.5))

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.12, right=0.82)
    if footer:
        fig.text(0.01, 0.005, footer, fontsize=8, ha="left", va="bottom", color="#444444")
        plt.subplots_adjust(bottom=min(0.45, 0.12 + 0.02 * (footer.count("\n") + 1)))

    out_path = out_path or output_dir / f"meta_trend_{chart_type}_{file_tag}_last{weeks_back}w.png"
    plt.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path, status_by_key


def run_statistics(
    input_excel: Path,
    output_dir: Path,
    history_csv: Path,
    total_players: int = 1000,
    rounds: int = 5,
    min_encounter_pct: float = 5.0,
    weeks_back: int = 4,
    output_profile: str = "full",
    palette_name: str = "classic",
    deck_colors: Optional[dict[str, str]] = None,
    legend_colors: Optional[dict[str, str]] = None,
    log=None,
    week_index: Optional[int] = None,
    trend_caption: Optional[str] = None,
) -> StatisticsRunResult:
    def emit(message: str) -> None:
        if log is not None:
            log(str(message))

    if not input_excel.exists():
        raise FileNotFoundError(f"Input file not found: {input_excel}")

    normalized_profile = str(output_profile or "full").strip().lower()
    if normalized_profile not in {"full", "light"}:
        normalized_profile = "full"

    performance_colors, trend_colors, normalized_deck_colors, normalized_legend_colors = _build_style(
        palette_name=palette_name,
        deck_colors=deck_colors,
        legend_colors=legend_colors,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for child in list(output_dir.iterdir()):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except Exception:
            pass
    emit(f"[stats] Loading input: {input_excel}")

    df_new = pd.read_excel(input_excel)
    df_new = _normalize_numeric(df_new)

    required = {"Deck", "Meta", "Winrate"}
    missing = [name for name in required if name not in df_new.columns]
    if missing:
        raise RuntimeError(f"Input is missing required columns: {', '.join(missing)}")

    df_new["Deck"] = df_new["Deck"].astype(str).str.strip()
    if "Deck Display Name" not in df_new.columns:
        df_new["Deck Display Name"] = df_new["Deck"]
    if "Archetype" not in df_new.columns:
        df_new["Archetype"] = pd.NA

    df_history = pd.DataFrame()
    if history_csv.exists():
        try:
            df_history = pd.read_csv(history_csv)
            emit(f"[stats] History loaded: {len(df_history)} rows")
        except Exception as err:
            emit(f"[stats][warn] Failed to read history: {err}")
            df_history = pd.DataFrame()

    week_start_tag, week_end_tag = _extract_week_range_from_input(input_excel)
    existing_week_for_range: Optional[int] = None
    if (
        len(df_history) > 0
        and week_start_tag is not None
        and week_end_tag is not None
        and {"WeekStart", "WeekEnd", "WeekIndex"}.issubset(df_history.columns)
    ):
        range_mask = (
            df_history["WeekStart"].astype(str).str.strip().eq(week_start_tag)
            & df_history["WeekEnd"].astype(str).str.strip().eq(week_end_tag)
        )
        if range_mask.any():
            existing_week = pd.to_numeric(df_history.loc[range_mask, "WeekIndex"], errors="coerce").dropna()
            if not existing_week.empty:
                existing_week_for_range = int(existing_week.max())

    if week_index is None:
        if existing_week_for_range is not None:
            week_index = existing_week_for_range
            emit(f"[stats] Reusing existing week index for range {week_start_tag}_to_{week_end_tag}: {week_index}")
        elif len(df_history) > 0 and "WeekIndex" in df_history.columns:
            max_week = pd.to_numeric(df_history["WeekIndex"], errors="coerce").max()
            week_index = int(max_week) + 1 if pd.notna(max_week) else 1
        else:
            week_index = 1
    emit(f"[stats] Week index: {week_index}")
    file_tag = f"{week_start_tag}_to_{week_end_tag}" if (week_start_tag and week_end_tag) else f"W{week_index:02d}"
    emit(f"[stats] Output profile: {normalized_profile}")
    emit(f"[stats] Palette: {palette_name}")
    if normalized_deck_colors:
        emit(f"[stats] Custom deck colors: {len(normalized_deck_colors)}")
    if normalized_legend_colors:
        emit(f"[stats] Custom legend colors: {len(normalized_legend_colors)}")

    total_players = max(10, int(total_players))
    rounds = max(1, min(int(rounds), total_players))
    min_encounter_threshold = max(0.0, min(float(min_encounter_pct) / 100.0, 1.0))

    df_new = _calculate_metrics(df_new, total_players, rounds)
    df_new["WeekIndex"] = week_index
    df_new["Level"] = "Deck"
    df_new["WeekStart"] = week_start_tag if week_start_tag is not None else pd.NA
    df_new["WeekEnd"] = week_end_tag if week_end_tag is not None else pd.NA

    # Only strictly earlier weeks feed the trend: re-running an older week after a later one is
    # already in history must not let that later week's Meta leak into this week's Trend Label.
    history_for_trend = df_history
    if len(df_history) > 0 and "WeekIndex" in df_history.columns:
        not_prior_mask = pd.to_numeric(df_history["WeekIndex"], errors="coerce") >= week_index
        history_for_trend = df_history[~not_prior_mask].copy()

    def _trend_history_by_deck(level: str) -> dict[str, list[float]]:
        # Scoped to one Level so a Deck and an Archetype bucket that happen to share a name can
        # never silently pull each other's Meta values into this key's trend average (they share
        # the "Deck" column as the lookup key across both levels -- see df_arch["Deck"] below).
        if history_for_trend.empty or "Deck" not in history_for_trend.columns or "WeekIndex" not in history_for_trend.columns:
            return {}
        scoped = history_for_trend
        if "Level" in scoped.columns:
            scoped = scoped[scoped["Level"] == level]
        if scoped.empty or "Meta" not in scoped.columns:
            return {}
        scoped = scoped.sort_values("WeekIndex")
        return {deck: grp["Meta"].tolist() for deck, grp in scoped.groupby("Deck")}

    deck_trend_history = _trend_history_by_deck("Deck")
    df_new["Trend Label"] = df_new.apply(
        lambda row: resolve_trend_status(
            deck_trend_history.get(row["Deck"], []), float(row["Meta"]), threshold=TREND_THRESHOLD_DECK
        ),
        axis=1,
    )

    # Pillar / Emerging Threat / Declining Threat are unimplemented placeholder columns, not a
    # computed result -- nothing in this module ever sets them to True on either level. Do not
    # treat "False" here as a verified "no pillars/threats this week"; a future implementation
    # needs its own historical function, distinct from resolve_trend_status above. Flagged
    # 2026-08-24 alongside the Trend Label unification so this doesn't get mistaken for output.
    df_new["Pillar"] = False
    df_new["Emerging Threat"] = False
    df_new["Declining Threat"] = False

    if len(df_history) > 0 and "WeekIndex" in df_history.columns:
        week_mask = pd.to_numeric(df_history["WeekIndex"], errors="coerce") == week_index
        removed_rows = int(week_mask.sum())
        if removed_rows > 0:
            emit(f"[stats] Replacing existing rows for week index {week_index}: {removed_rows} rows removed")
        df_history = df_history[~week_mask]

    df_arch = _aggregate_by_archetype(df_new)
    df_arch = _calculate_metrics(df_arch, total_players, rounds)
    df_arch["WeekIndex"] = week_index
    df_arch["Deck"] = df_arch["Archetype"]
    df_arch["Level"] = "Archetype"
    df_arch["WeekStart"] = week_start_tag if week_start_tag is not None else pd.NA
    df_arch["WeekEnd"] = week_end_tag if week_end_tag is not None else pd.NA

    arch_trend_history = _trend_history_by_deck("Archetype")
    df_arch["Trend Label"] = df_arch.apply(
        lambda row: resolve_trend_status(
            arch_trend_history.get(row["Deck"], []), float(row["Meta"]), threshold=TREND_THRESHOLD_ARCHETYPE
        ),
        axis=1,
    )
    df_arch["Pillar"] = False  # placeholder -- see the note above df_new["Pillar"]
    df_arch["Emerging Threat"] = False
    df_arch["Declining Threat"] = False

    df_history = pd.concat([df_history, df_new], ignore_index=True)
    df_history = pd.concat([df_history, df_arch], ignore_index=True)

    emit("[stats] Saving tabular outputs...")
    deck_excel = output_dir / f"deck_analysis_{file_tag}.xlsx"
    arch_excel = output_dir / f"deck_analysis_ARCHETYPE_{file_tag}.xlsx"
    df_results_with_trend = output_dir / f"deck_analysis_WITH_TRENDS_{file_tag}.xlsx"
    history_out = output_dir / f"Metagame_History_{file_tag}.csv"

    if normalized_profile == "full":
        df_new.to_excel(deck_excel, index=False)
        df_arch.to_excel(arch_excel, index=False)
    df_new.to_excel(df_results_with_trend, index=False)
    df_history.to_csv(history_out, index=False)
    history_csv.parent.mkdir(parents=True, exist_ok=True)
    df_history.to_csv(history_csv, index=False)

    emit("[stats] Building charts...")
    files: list[Path] = [df_results_with_trend, history_out]
    if normalized_profile == "full":
        files = [deck_excel, arch_excel, df_results_with_trend, history_out]

    deck_chart = _create_encounter_chart(
        df_new,
        file_tag,
        total_players,
        "Deck",
        output_dir,
        min_encounter_threshold,
        performance_colors,
        normalized_deck_colors,
        normalized_legend_colors,
    )
    if normalized_profile == "full":
        arch_chart = _create_encounter_chart(
            df_arch,
            file_tag,
            total_players,
            "Archetype",
            output_dir,
            min_encounter_threshold,
            performance_colors,
            normalized_deck_colors,
            normalized_legend_colors,
        )
        record_chart = _create_record_probability_chart(
            df_new,
            rounds,
            file_tag,
            output_dir,
            normalized_deck_colors,
            normalized_legend_colors,
        )
        record_excel = _build_record_probability_excel(df_new, rounds, file_tag, output_dir)
        for maybe in [deck_chart, arch_chart, record_chart, record_excel]:
            if maybe is not None:
                files.append(maybe)
    else:
        for maybe in [deck_chart]:
            if maybe is not None:
                files.append(maybe)

    unique_weeks = pd.to_numeric(df_history["WeekIndex"], errors="coerce").nunique() if "WeekIndex" in df_history.columns else 0
    if unique_weeks > 1:
        weeks_back = max(2, min(int(weeks_back), int(pd.to_numeric(df_history["WeekIndex"], errors="coerce").max())))
        deck_trend, deck_trend_status = _create_trend_chart(
            df_history,
            df_new,
            weeks_back,
            week_index,
            file_tag,
            "Deck",
            output_dir,
            trend_colors,
            normalized_deck_colors,
            normalized_legend_colors,
            caption=trend_caption,
        )
        if normalized_profile == "full":
            arch_trend, arch_trend_status = _create_trend_chart(
                df_history,
                df_arch,
                weeks_back,
                week_index,
                file_tag,
                "Archetype",
                output_dir,
                trend_colors,
                normalized_deck_colors,
                normalized_legend_colors,
                caption=trend_caption,
            )
            for maybe in [deck_trend, arch_trend]:
                if maybe is not None:
                    files.append(maybe)

            # Regression guard (a): a flat Archetype-level Trend Label column is exactly the
            # 2026-08-24 bug (the column was hardcoded to "Stable"). With real week-over-week
            # history and >=2 archetypes in scope this must never happen again -- hard failure,
            # not a warning, so a silently-flat column can never reach the dossier.
            if len(df_arch) >= 2:
                arch_labels = set(df_arch["Trend Label"].astype(str))
                if len(arch_labels) <= 1:
                    emit(
                        f"[stats] ABORT: Trend Label is constant ({arch_labels!r}) across all "
                        f"{len(df_arch)} Archetype rows for week {week_index} ({file_tag})"
                    )
                    raise RuntimeError(
                        f"Trend Label is constant across all Archetype rows ({arch_labels!r}, "
                        f"{len(df_arch)} rows) for week {week_index} ({file_tag}) -- this is the "
                        "flat-column regression, refusing to write deck_analysis_ARCHETYPE."
                    )

            # Regression guard (b): the PNG legend and the tabular Trend Label column must never
            # disagree for the same key -- that was the root cause diagnosed on 2026-08-24 (two
            # independent trend definitions feeding the same document). Compare every key present
            # in both the chart legend and its corresponding table; both sides key on "Deck"
            # (df_arch["Deck"] holds the Archetype name, set above) -- the same column
            # resolve_trend_status was fed from.
            mismatches: list[str] = []
            for key, chart_status in deck_trend_status.items():
                table_rows = df_new.loc[df_new["Deck"].astype(str) == key, "Trend Label"]
                if table_rows.empty:
                    continue
                table_status = str(table_rows.iloc[0])
                if table_status != chart_status:
                    mismatches.append(f"Deck {key!r}: legend={chart_status!r} table={table_status!r}")
            for key, chart_status in arch_trend_status.items():
                table_rows = df_arch.loc[df_arch["Deck"].astype(str) == key, "Trend Label"]
                if table_rows.empty:
                    continue
                table_status = str(table_rows.iloc[0])
                if table_status != chart_status:
                    mismatches.append(f"Archetype {key!r}: legend={chart_status!r} table={table_status!r}")
            if mismatches:
                emit(f"[stats] ABORT: PNG/table trend mismatch for {len(mismatches)} key(s): {mismatches}")
                raise RuntimeError(
                    f"PNG trend legend and Trend Label column disagree for {len(mismatches)} "
                    f"key(s) in week {week_index} ({file_tag}): " + "; ".join(mismatches)
                )
        else:
            if deck_trend is not None:
                files.append(deck_trend)

    emit(f"[stats] Completed. Files generated: {len(files)}")
    return StatisticsRunResult(
        output_dir=output_dir,
        input_excel=input_excel,
        history_csv=history_csv,
        week_index=week_index,
        deck_rows=len(df_new),
        archetype_rows=len(df_arch),
        files=files,
    )


def rebuild_history_from_dirs(
    outputs_base: Path,
    history_csv: Path,
    total_players: int = 1000,
    rounds: int = 5,
    min_encounter_pct: float = 5.0,
    weeks_back: int = 4,
    output_profile: str = "full",
    palette_name: str = "classic",
    deck_colors: Optional[dict[str, str]] = None,
    legend_colors: Optional[dict[str, str]] = None,
    log=None,
) -> int:
    """Scans all run directories under outputs_base, rebuilds metagame_history.csv from scratch.

    Returns the number of weeks successfully processed.
    """
    from metagame_input_generator import scan_run_dirs  # local import to avoid circular at module level

    def emit(msg: str) -> None:
        if log is not None:
            log(str(msg))

    run_dirs = scan_run_dirs(outputs_base)
    if not run_dirs:
        emit("[rebuild] No run directories found under: " + str(outputs_base))
        return 0

    emit(f"[rebuild] Found {len(run_dirs)} run directories. Clearing history and rebuilding...")

    # Reset history CSV so we start from scratch
    if history_csv.exists():
        history_csv.unlink()

    processed = 0
    for seq, (week_start, week_end, run_dir) in enumerate(run_dirs, start=1):
        grouped_xlsx = run_dir / "metagame_input_grouped.xlsx"
        if not grouped_xlsx.exists():
            emit(f"[rebuild] Skip W{seq:02d} {run_dir.name}: metagame_input_grouped.xlsx not found")
            continue

        stats_dir = run_dir / "statistics" / "metagame"
        emit(f"[rebuild] W{seq:02d} {run_dir.name}")
        try:
            run_statistics(
                input_excel=grouped_xlsx,
                output_dir=stats_dir,
                history_csv=history_csv,
                total_players=total_players,
                rounds=rounds,
                min_encounter_pct=min_encounter_pct,
                weeks_back=weeks_back,
                output_profile=output_profile,
                palette_name=palette_name,
                deck_colors=deck_colors,
                legend_colors=legend_colors,
                log=log,
                week_index=seq,
            )
            processed += 1
        except Exception as exc:
            emit(f"[rebuild][error] {run_dir.name}: {exc}")

    emit(f"[rebuild] Done. Processed {processed}/{len(run_dirs)} weeks.")
    return processed

