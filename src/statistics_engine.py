from __future__ import annotations

from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Optional

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
    if "My Deck Winrate" in out.columns:
        out["My Deck Winrate"] = as_float(out["My Deck Winrate"], percent_like=True)
    elif "My Winrate" in out.columns:
        out["My Deck Winrate"] = as_float(out["My Winrate"], percent_like=True)
    else:
        out["My Deck Winrate"] = pd.NA

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
    q1, q2, q3 = out["Importance"].quantile([0.25, 0.5, 0.75])

    def quartile_label(val: float) -> str:
        if val <= q1:
            return "Q1"
        if val <= q2:
            return "Q2"
        if val <= q3:
            return "Q3"
        return "Q4"

    out["Quartile"] = out["Importance"].apply(quartile_label)
    out["Prep Priority"] = out["Quartile"].apply(prep_priority)

    meta_med = out["Meta"].median()
    winrate_med = out["Winrate"].median()

    def perf(row: pd.Series) -> str:
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
        my_wr = pd.to_numeric(group.get("My Deck Winrate", pd.Series(dtype=float)), errors="coerce")

        wr_value = float((wr.fillna(0) * meta).sum() / meta.sum()) if meta.sum() > 0 else float(wr.dropna().mean())
        valid_my = my_wr.notna()
        if valid_my.any() and float(meta[valid_my].sum()) > 0:
            my_wr_value = float((my_wr[valid_my] * meta[valid_my]).sum() / meta[valid_my].sum())
        else:
            my_wr_value = pd.NA

        rows.append(
            {
                "Archetype": str(archetype),
                "Meta": float(meta.sum()),
                "Winrate": wr_value,
                "My Deck Winrate": my_wr_value,
                "Deck Display Name": str(archetype),
            }
        )

    return pd.DataFrame(rows).sort_values("Meta", ascending=False).reset_index(drop=True)


def _create_encounter_chart(
    df: pd.DataFrame,
    week_index: int,
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
        show_df = df.copy()
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

    my_wr_series = pd.to_numeric(show_df.get("My Deck Winrate", pd.Series([pd.NA] * count)), errors="coerce")
    for bar, prob, prep, my_wr in zip(bars, show_df["Encounter Probability"], show_df["Prep Priority"], my_wr_series):
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
    ax.set_title(f"Encounter Probability ({chart_type}){threshold_text} (N={total_players}) - Week {week_index}", fontsize=16, pad=20)
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
    out_path = output_dir / f"encounter_prob_{chart_type}_W{week_index}.png"
    plt.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _create_my_deck_chart(
    df_results: pd.DataFrame,
    week_index: int,
    total_players: int,
    output_dir: Path,
    min_encounter_threshold: float,
    player_deck_name: str,
    performance_colors: dict[str, str],
    deck_colors: dict[str, str],
    legend_colors: dict[str, str],
) -> Optional[Path]:
    if "My Deck Winrate" not in df_results.columns:
        return None

    df_my = df_results[df_results["My Deck Winrate"].notna()].copy()
    if df_my.empty:
        return None

    show_df = df_my[df_my["Encounter Probability"] >= min_encounter_threshold].copy()
    if show_df.empty:
        show_df = df_my.copy()

    show_df["Problem Score"] = show_df["Encounter Probability"] * (1 - pd.to_numeric(show_df["My Deck Winrate"], errors="coerce").fillna(0.5))
    show_df = show_df.sort_values("Problem Score", ascending=False).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(max(14, len(show_df) * 1.3), 10))
    wr_vals = pd.to_numeric(show_df["My Deck Winrate"], errors="coerce").fillna(0.5)
    colors = []
    norm = plt.Normalize(vmin=0, vmax=1)
    cmap = plt.cm.RdYlGn
    for _, row in show_df.iterrows():
        wr = float(pd.to_numeric(row.get("My Deck Winrate"), errors="coerce") or 0.5)
        colors.append(cmap(norm(wr)))

    bars = ax.bar(
        range(len(show_df)),
        show_df["Encounter Probability"],
        color=colors,
        edgecolor="black",
        linewidth=0.6,
        alpha=0.92,
    )

    for bar, enc_prob, my_wr in zip(bars, show_df["Encounter Probability"], wr_vals):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            float(bar.get_height()) + 0.018,
            f"{float(enc_prob):.1%}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color="black",
        )
        bar_h = float(bar.get_height())
        if bar_h > 0.07:
            text_color = "white" if (float(my_wr) < 0.3 or float(my_wr) > 0.72) else "black"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar_h / 2,
                f"WR: {float(my_wr):.0%}",
                ha="center",
                va="center",
                fontsize=9,
                fontweight="bold",
                color=text_color,
            )

    rotation_angle = 30 if len(show_df) < 12 else 45 if len(show_df) < 20 else 60
    for i, (deck, perf) in enumerate(zip(show_df["Deck Display Name"], show_df["Performance Label"])):
        ax.text(
            i,
            -0.03,
            str(deck),
            ha="right",
            va="top",
            fontsize=9,
            rotation=rotation_angle,
            color=_legend_color(str(perf), legend_colors, performance_colors.get(str(perf), "black")),
        )

    ax.tick_params(axis="x", which="both", length=0, labelbottom=False)
    ax.spines["bottom"].set_visible(False)
    ax.set_ylabel("Encounter Probability", fontsize=13)
    ax.set_ylim(0, 1.15)
    ax.set_title(
        (
            f"{player_deck_name} Performance vs Metagame - Week {week_index}\n"
            f"Sorted by Problem Score (Encounter Prob x Loss Rate) | N={total_players}"
        ),
        fontsize=14,
        pad=20,
    )
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, orientation="vertical", fraction=0.025, pad=0.02)
    cbar.set_label("My Winrate Against This Deck", fontsize=11)
    cbar.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cbar.set_ticklabels(["0% (0-X)", "25%", "50%", "75%", "100% (X-0)"])

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
    ax.legend(handles=perf_patches, title="Deck Performance (meta)", loc="upper right", fontsize=9)

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.38, right=0.88)

    out_path = output_dir / f"my_deck_performance_W{week_index}.png"
    plt.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _create_record_probability_chart(
    player_winrate: float,
    df_results: pd.DataFrame,
    rounds: int,
    week_index: int,
    output_dir: Path,
    player_deck_name: str,
    deck_colors: dict[str, str],
    legend_colors: dict[str, str],
) -> Optional[Path]:
    records = calculate_binomial_records(player_winrate, rounds)
    labels = list(records.keys())
    probs = list(records.values())

    fig, axes = plt.subplots(1, 2, figsize=(20, 9))
    fig.suptitle(f"Record Probability Analysis - Week {week_index} ({rounds}-round event)", fontsize=15, y=1.01)

    ax1 = axes[0]
    bar_colors_left = plt.cm.RdYlGn(np.linspace(0, 1, len(labels)))
    bars = ax1.bar(labels, probs, color=bar_colors_left, edgecolor="black", linewidth=0.6, alpha=0.9)
    for bar, prob in zip(bars, probs):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.005,
            f"{prob:.1%}",
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )
    ax1.set_xlabel("Record (W-L)", fontsize=12)
    ax1.set_ylabel("Probability", fontsize=12)
    ax1.set_title(
        f"{player_deck_name} Record Distribution\nOverall Winrate: {player_winrate:.1%} | "
        f"Expected: {player_winrate * rounds:.1f}-{(1 - player_winrate) * rounds:.1f}",
        fontsize=13,
    )
    ax1.set_ylim(0, max(probs) * 1.25)
    ax1.tick_params(axis="x", labelsize=10)
    best_idx = int(np.argmax(probs)) if probs else 0
    if probs:
        bars[best_idx].set_edgecolor("gold")
        bars[best_idx].set_linewidth(3)
        ax1.text(
            bars[best_idx].get_x() + bars[best_idx].get_width() / 2,
            max(probs) * 1.15,
            "Most likely",
            ha="center",
            fontsize=9,
            color="goldenrod",
            fontweight="bold",
        )

    ax2 = axes[1]
    top = df_results.sort_values("Winrate", ascending=False).head(20).reset_index(drop=True)
    expected_wins = pd.to_numeric(top["Winrate"], errors="coerce").fillna(0.5) * rounds
    top_colors = []
    for _, row in top.iterrows():
        wr = float(pd.to_numeric(row.get("Winrate"), errors="coerce") or 0.5)
        top_colors.append(plt.cm.RdYlGn(plt.Normalize(vmin=0, vmax=1)(wr)))
    ax2.barh(range(len(top)), expected_wins, color=top_colors, edgecolor="black", linewidth=0.5, alpha=0.9)
    ax2.set_yticks(range(len(top)))
    ax2.set_yticklabels(top["Deck Display Name"].astype(str).tolist(), fontsize=8)
    ax2.set_xlabel("Expected Wins", fontsize=12)
    ax2.set_title(f"Expected Wins per Deck\nBased on deck winrate | {rounds} rounds", fontsize=13)
    ax2.set_xlim(0, rounds + 0.5)
    ax2.grid(axis="x", alpha=0.3, linestyle="--")
    for i, (wins, wr) in enumerate(zip(expected_wins, pd.to_numeric(top["Winrate"], errors="coerce").fillna(0.5))):
        ax2.text(float(wins) + 0.05, i, f"{float(wins):.2f} ({float(wr):.1%})", va="center", fontsize=8)

    sm2 = plt.cm.ScalarMappable(cmap=plt.cm.RdYlGn, norm=plt.Normalize(vmin=0, vmax=1))
    sm2.set_array([])
    cbar2 = plt.colorbar(sm2, ax=ax2, orientation="vertical", fraction=0.03, pad=0.02)
    cbar2.set_label("Deck Winrate", fontsize=10)
    cbar2.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cbar2.set_ticklabels(["0%", "25%", "50%", "75%", "100%"])

    info_text = (
        f"Comparison reference:\n"
        f"{player_deck_name}: WR={player_winrate:.1%}, Expected={player_winrate * rounds:.2f} wins\n"
        "Use this to compare your deck against top meta decks"
    )
    fig.text(0.5, -0.02, info_text, ha="center", fontsize=10, bbox=dict(boxstyle="round", facecolor="lightblue", alpha=0.5))

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.15)
    out_path = output_dir / f"record_probability_W{week_index}.png"
    plt.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _build_record_probability_excel(
    df_results: pd.DataFrame,
    player_winrate: float,
    rounds: int,
    week_index: int,
    output_dir: Path,
) -> Path:
    labels = [f"{w}-{rounds - w}" for w in range(rounds, -1, -1)]
    rows: list[dict[str, str]] = []

    player_probs = calculate_binomial_records(player_winrate, rounds)
    you_row = {"Deck": "YOU (overall winrate)", "Meta": "", "Deck Winrate": f"{player_winrate:.1%}"}
    for label in labels:
        you_row[label] = f"{player_probs.get(label, 0.0):.1%}"
    rows.append(you_row)

    for _, row in df_results.sort_values("Meta", ascending=False).iterrows():
        wr = float(pd.to_numeric(row.get("Winrate"), errors="coerce") or 0.5)
        probs = calculate_binomial_records(wr, rounds)
        item = {
            "Deck": str(row.get("Deck Display Name", row.get("Deck", ""))),
            "Meta": f"{float(pd.to_numeric(row.get('Meta'), errors='coerce') or 0.0):.1f}%",
            "Deck Winrate": f"{wr:.1%}",
        }
        for label in labels:
            item[label] = f"{probs.get(label, 0.0):.1%}"
        rows.append(item)

    out_path = output_dir / f"record_probabilities_W{week_index}.xlsx"
    pd.DataFrame(rows).to_excel(out_path, index=False)
    return out_path


def _create_trend_chart(
    df_history: pd.DataFrame,
    df_current: pd.DataFrame,
    weeks_back: int,
    week_index: int,
    chart_type: str,
    output_dir: Path,
    trend_colors: dict[str, str],
    deck_colors: dict[str, str],
    legend_colors: dict[str, str],
) -> Optional[Path]:
    if df_history.empty or "WeekIndex" not in df_history.columns:
        return None

    if chart_type == "Archetype":
        if "Level" not in df_history.columns:
            return None
        hist = df_history[df_history["Level"] == "Archetype"].copy()
    else:
        if "Level" in df_history.columns:
            hist = df_history[(df_history["Level"] == "Deck") | (df_history["Level"].isna())].copy()
        else:
            hist = df_history.copy()

    if hist.empty or "Deck" not in hist.columns:
        return None

    min_week = max(1, int(week_index - weeks_back + 1))
    hist = hist[pd.to_numeric(hist["WeekIndex"], errors="coerce") >= min_week].copy()
    if hist.empty:
        return None

    top = df_current.sort_values("Meta", ascending=False).head(10)["Deck Display Name"].astype(str).tolist()
    hist = hist[hist["Deck"].astype(str).isin(top)].copy()
    if hist.empty:
        return None

    fig, ax = plt.subplots(figsize=(18, 11))
    cmap = plt.cm.rainbow
    color_count = max(1, len(top) - 1)
    base_deck_colors = {deck: cmap(1 - i / color_count) for i, deck in enumerate(top)}

    legend_elements = []
    for deck in top:
        series = hist[hist["Deck"].astype(str) == deck].sort_values("WeekIndex")
        if series.empty:
            continue
        values = pd.to_numeric(series["Meta"], errors="coerce").fillna(0)
        weeks = pd.to_numeric(series["WeekIndex"], errors="coerce").astype(int)
        if len(values) >= 2:
            current = float(values.iloc[-1])
            past_avg = float(values.iloc[:-1].mean()) if len(values) > 1 else current
            threshold = TREND_THRESHOLD_ARCHETYPE if chart_type == "Archetype" else TREND_THRESHOLD_DECK
            status = trend_label(current, past_avg, threshold=threshold)
        else:
            status = "Stable"
        trend_symbol = "^" if status == "Rising Deck" else "v" if status == "Falling Deck" else "-"
        line_color = _deck_color_override(deck, deck_colors) or base_deck_colors[deck]
        ax.plot(weeks, values, marker="o", linewidth=2.5, markersize=8, color=line_color)
        legend_elements.append(
            Line2D([0], [0], color=line_color, linewidth=2.5, marker="o", markersize=6, label=f"{deck} {trend_symbol}")
        )

    ax.set_title(f"Meta Trend ({chart_type}) - Last {weeks_back} Weeks")
    ax.set_xlabel("Week")
    ax.set_ylabel("Meta %")
    ax.grid(True, linestyle="--", alpha=0.3)

    ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax.legend(handles=legend_elements, title="Decks (Trend Status)", loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9, framealpha=0.95, title_fontsize=10)

    trend_box_text = "^ Rising  |  - Stable  |  v Falling"
    box_face = _legend_color("Trend Box", legend_colors, "wheat")
    ax.text(0.5, -0.08, trend_box_text, transform=ax.transAxes, ha="center", fontsize=10, bbox=dict(boxstyle="round", facecolor=box_face, alpha=0.5))

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.12, right=0.82)

    out_path = output_dir / f"meta_trend_{chart_type}_W{week_index}_last{weeks_back}w.png"
    plt.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def run_statistics(
    input_excel: Path,
    output_dir: Path,
    history_csv: Path,
    total_players: int = 1000,
    rounds: int = 5,
    min_encounter_pct: float = 5.0,
    player_deck_name: str = "My Deck",
    player_winrate: float = 0.5,
    weeks_back: int = 4,
    output_profile: str = "full",
    palette_name: str = "classic",
    deck_colors: Optional[dict[str, str]] = None,
    legend_colors: Optional[dict[str, str]] = None,
    log=None,
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

    if len(df_history) > 0 and "WeekIndex" in df_history.columns:
        max_week = pd.to_numeric(df_history["WeekIndex"], errors="coerce").max()
        week_index = int(max_week) + 1 if pd.notna(max_week) else 1
    else:
        week_index = 1
    emit(f"[stats] Week index: {week_index}")
    emit(f"[stats] Output profile: {normalized_profile}")
    emit(f"[stats] Palette: {palette_name}")
    if normalized_deck_colors:
        emit(f"[stats] Custom deck colors: {len(normalized_deck_colors)}")
    if normalized_legend_colors:
        emit(f"[stats] Custom legend colors: {len(normalized_legend_colors)}")

    total_players = max(10, int(total_players))
    rounds = max(1, min(int(rounds), total_players))
    min_encounter_threshold = max(0.0, min(float(min_encounter_pct) / 100.0, 1.0))
    player_winrate = float(player_winrate)
    if player_winrate > 1:
        player_winrate = player_winrate / 100.0
    player_winrate = max(0.01, min(player_winrate, 0.99))

    df_new = _calculate_metrics(df_new, total_players, rounds)
    df_new["WeekIndex"] = week_index
    df_new["Level"] = "Deck"

    if len(df_history) > 0 and "Deck" in df_history.columns and "WeekIndex" in df_history.columns:
        tmp = df_history.sort_values("WeekIndex").groupby("Deck").tail(4)
        trend_meta = tmp.groupby("Deck")["Meta"].mean().to_dict() if "Meta" in tmp.columns else {}
        df_new["Trend Label"] = df_new.apply(
            lambda row: trend_label(float(row["Meta"]), float(trend_meta.get(row["Deck"], row["Meta"])), threshold=TREND_THRESHOLD_DECK),
            axis=1,
        )
    else:
        df_new["Trend Label"] = "Stable"

    df_new["Pillar"] = False
    df_new["Emerging Threat"] = False
    df_new["Declining Threat"] = False

    if len(df_history) > 0 and "Deck" in df_history.columns and "WeekIndex" in df_history.columns:
        mask = (pd.to_numeric(df_history["WeekIndex"], errors="coerce") == week_index) & df_history["Deck"].isin(df_new["Deck"])
        df_history = df_history[~mask]
    df_history = pd.concat([df_history, df_new], ignore_index=True)

    df_arch = _aggregate_by_archetype(df_new)
    df_arch = _calculate_metrics(df_arch, total_players, rounds)
    df_arch["WeekIndex"] = week_index
    df_arch["Deck"] = df_arch["Archetype"]
    df_arch["Level"] = "Archetype"
    df_arch["Trend Label"] = "Stable"
    df_arch["Pillar"] = False
    df_arch["Emerging Threat"] = False
    df_arch["Declining Threat"] = False

    if "Level" in df_history.columns and "WeekIndex" in df_history.columns:
        arch_mask = (pd.to_numeric(df_history["WeekIndex"], errors="coerce") == week_index) & (df_history["Level"] == "Archetype")
        df_history = df_history[~arch_mask]
    df_history = pd.concat([df_history, df_arch], ignore_index=True)

    emit("[stats] Saving tabular outputs...")
    deck_excel = output_dir / f"deck_analysis_W{week_index}.xlsx"
    arch_excel = output_dir / f"deck_analysis_ARCHETYPE_W{week_index}.xlsx"
    df_results_with_trend = output_dir / f"deck_analysis_WITH_TRENDS_W{week_index}.xlsx"
    history_out = output_dir / f"Metagame_History_W{week_index}.csv"

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
        week_index,
        total_players,
        "Deck",
        output_dir,
        min_encounter_threshold,
        performance_colors,
        normalized_deck_colors,
        normalized_legend_colors,
    )
    my_deck_chart = _create_my_deck_chart(
        df_new,
        week_index,
        total_players,
        output_dir,
        min_encounter_threshold,
        player_deck_name,
        performance_colors,
        normalized_deck_colors,
        normalized_legend_colors,
    )

    if normalized_profile == "full":
        arch_chart = _create_encounter_chart(
            df_arch,
            week_index,
            total_players,
            "Archetype",
            output_dir,
            min_encounter_threshold,
            performance_colors,
            normalized_deck_colors,
            normalized_legend_colors,
        )
        record_chart = _create_record_probability_chart(
            player_winrate,
            df_new,
            rounds,
            week_index,
            output_dir,
            player_deck_name,
            normalized_deck_colors,
            normalized_legend_colors,
        )
        record_excel = _build_record_probability_excel(df_new, player_winrate, rounds, week_index, output_dir)
        for maybe in [deck_chart, arch_chart, my_deck_chart, record_chart, record_excel]:
            if maybe is not None:
                files.append(maybe)
    else:
        for maybe in [deck_chart, my_deck_chart]:
            if maybe is not None:
                files.append(maybe)

    unique_weeks = pd.to_numeric(df_history["WeekIndex"], errors="coerce").nunique() if "WeekIndex" in df_history.columns else 0
    if unique_weeks > 1:
        weeks_back = max(2, min(int(weeks_back), int(pd.to_numeric(df_history["WeekIndex"], errors="coerce").max())))
        deck_trend = _create_trend_chart(
            df_history,
            df_new,
            weeks_back,
            week_index,
            "Deck",
            output_dir,
            trend_colors,
            normalized_deck_colors,
            normalized_legend_colors,
        )
        if normalized_profile == "full":
            arch_trend = _create_trend_chart(
                df_history,
                df_arch,
                weeks_back,
                week_index,
                "Archetype",
                output_dir,
                trend_colors,
                normalized_deck_colors,
                normalized_legend_colors,
            )
            for maybe in [deck_trend, arch_trend]:
                if maybe is not None:
                    files.append(maybe)
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
