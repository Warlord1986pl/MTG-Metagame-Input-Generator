"""Challenge history and analytics helpers.

This module keeps a persistent Challenge history CSV, rebuilds it from run dirs,
and produces statistics + charts for presentation.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, List, Optional
from math import comb

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


HISTORY_COLS: List[str] = [
    "EventDate",
    "Format",
    "ChallengeSize",
    "EventSlug",
    "Place",
    "Deck",
    "Archetype",
    "Pilot",
]

RECON_DECKLIST_RE = re.compile(r"^challenge_C(32|64)_(\d{4}-\d{2}-\d{2})_decklist\.csv$", re.IGNORECASE)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip()).lower()


@dataclass
class ChallengeStatisticsResult:
    output_dir: Path
    history_csv: Path
    excel_path: Path
    events_processed: int
    deck_rows: int
    chart_paths: list[Path]


def load_challenge_history(history_csv: Path) -> pd.DataFrame:
    if not history_csv.exists():
        return pd.DataFrame(columns=HISTORY_COLS)
    try:
        df = pd.read_csv(history_csv, dtype=str)
    except Exception:
        return pd.DataFrame(columns=HISTORY_COLS)
    for col in HISTORY_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[HISTORY_COLS].copy()


def append_to_challenge_history(
    history_csv: Path,
    event_info: dict,
    decklist_df: pd.DataFrame,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    slug = str(event_info.get("slug") or event_info.get("event_date") or "unknown")
    event_date = str(event_info.get("event_date") or "")
    fmt = str(event_info.get("format") or "")
    challenge_size = int(event_info.get("challenge_size") or 0)

    rows: list[dict] = []
    for _, row in decklist_df.iterrows():
        rows.append(
            {
                "EventDate": event_date,
                "Format": fmt,
                "ChallengeSize": str(challenge_size),
                "EventSlug": slug,
                "Place": str(row.get("Place") or ""),
                "Deck": str(row.get("Deck") or "").strip(),
                "Archetype": str(row.get("Archetype") or "").strip(),
                "Pilot": str(row.get("Pilot") or "").strip(),
            }
        )

    existing = load_challenge_history(history_csv)
    if not existing.empty:
        existing = existing[existing["EventSlug"].astype(str).str.strip() != slug]

    combined = pd.concat([existing, pd.DataFrame(rows, columns=HISTORY_COLS)], ignore_index=True)
    history_csv.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(history_csv, index=False)
    emit(f"[challenge-history] Saved {len(rows)} rows for C{challenge_size} {event_date} -> {history_csv}")


def _normalize_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _presence_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """Compute per-deck/archetype metrics in the selected event set."""
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["Place"] = _normalize_numeric(work["Place"])
    work["EventDate"] = work["EventDate"].astype(str).str.strip()
    total_events = int(work["EventDate"].nunique())
    if total_events <= 0:
        return pd.DataFrame()

    grp = work.groupby(group_col)
    event_count = grp["EventDate"].nunique()
    appearances = grp[group_col].count()
    avg_place = grp["Place"].mean()
    best_place = grp["Place"].min()
    top8_count = grp["Place"].apply(lambda s: int((s <= 8).sum()))
    top16_count = grp["Place"].apply(lambda s: int((s <= 16).sum()))
    winner_count = grp["Place"].apply(lambda s: int((s == 1).sum()))

    top8_events = work[work["Place"] <= 8].groupby(group_col)["EventDate"].nunique()
    top16_events = work[work["Place"] <= 16].groupby(group_col)["EventDate"].nunique()
    winner_events = work[work["Place"] == 1].groupby(group_col)["EventDate"].nunique()

    stats = pd.DataFrame({
        group_col: event_count.index,
        "Events": event_count.values,
        "Appearances": appearances.values,
        "AvgPlace": avg_place.values,
        "BestPlace": best_place.values,
        "Top8Count": top8_count.values,
        "Top16Count": top16_count.values,
        "WinnerCount": winner_count.values,
    })

    stats["PresencePct"] = (stats["Events"] / total_events * 100.0).round(2)
    stats["Top32EventCount"] = stats["Events"]
    stats["Top32EntryCount"] = stats["Appearances"]
    stats["Top32EventFreqPct"] = stats["PresencePct"]
    stats["Top8EventCount"] = stats[group_col].map(top8_events).fillna(0).astype(int)
    stats["Top16EventCount"] = stats[group_col].map(top16_events).fillna(0).astype(int)
    stats["WinnerEventCount"] = stats[group_col].map(winner_events).fillna(0).astype(int)
    stats["Top8PresencePct"] = (stats["Top8EventCount"] / total_events * 100.0).round(2)
    stats["Top16PresencePct"] = (stats["Top16EventCount"] / total_events * 100.0).round(2)
    stats["Top8EventFreqPct"] = stats["Top8PresencePct"]
    stats["Top16EventFreqPct"] = stats["Top16PresencePct"]
    stats["WinnerEventFreqPct"] = (stats["WinnerEventCount"] / total_events * 100.0).round(2)

    # Rates by appearances are bounded [0, 100]
    stats["Top8Rate"] = ((stats["Top8Count"] / stats["Appearances"].clip(lower=1)) * 100.0).round(2)
    stats["Top16Rate"] = ((stats["Top16Count"] / stats["Appearances"].clip(lower=1)) * 100.0).round(2)
    stats["WinnerRate"] = ((stats["WinnerCount"] / stats["Appearances"].clip(lower=1)) * 100.0).round(2)
    stats["Top8ShareOfTop32EntriesPct"] = stats["Top8Rate"]
    stats["Top16ShareOfTop32EntriesPct"] = stats["Top16Rate"]
    stats["WinnerShareOfTop32EntriesPct"] = stats["WinnerRate"]

    stats["AvgPlace"] = pd.to_numeric(stats["AvgPlace"], errors="coerce").round(1)
    stats["BestPlace"] = pd.to_numeric(stats["BestPlace"], errors="coerce").fillna(999).astype(int)
    return stats


def _trend_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """Trend from recent half vs previous half based on presence percentage."""
    if df.empty:
        return pd.DataFrame(columns=[group_col, "Trend", "Top32EventFreqDelta pp", "Top8EventFreqDelta pp"])

    work = df.copy()
    work["Place"] = _normalize_numeric(work["Place"])
    dates = sorted(work["EventDate"].astype(str).str.strip().unique())
    if len(dates) < 2:
        return pd.DataFrame(columns=[group_col, "Trend", "Top32EventFreqDelta pp", "Top8EventFreqDelta pp"])

    split = max(1, len(dates) // 2)
    older_dates = dates[:split]
    recent_dates = dates[split:]
    if not recent_dates:
        recent_dates = dates[-split:]

    old = work[work["EventDate"].isin(older_dates)]
    new = work[work["EventDate"].isin(recent_dates)]

    old_total = max(1, old["EventDate"].nunique())
    new_total = max(1, new["EventDate"].nunique())

    old_presence = (old.groupby(group_col)["EventDate"].nunique() / old_total) * 100.0
    new_presence = (new.groupby(group_col)["EventDate"].nunique() / new_total) * 100.0

    old_top8 = (old[old["Place"] <= 8].groupby(group_col)["EventDate"].nunique() / old_total) * 100.0
    new_top8 = (new[new["Place"] <= 8].groupby(group_col)["EventDate"].nunique() / new_total) * 100.0

    keys = sorted(set(old_presence.index.tolist()) | set(new_presence.index.tolist()))
    rows: list[dict] = []
    for key in keys:
        delta_presence = float(new_presence.get(key, 0.0) - old_presence.get(key, 0.0))
        delta_top8 = float(new_top8.get(key, 0.0) - old_top8.get(key, 0.0))
        combined = delta_presence * 0.7 + delta_top8 * 0.3
        if combined >= 10:
            label = "Rising"
        elif combined <= -10:
            label = "Falling"
        else:
            label = "Stable"
        rows.append(
            {
                group_col: key,
                "Trend": label,
                "Top32EventFreqDelta pp": round(delta_presence, 2),
                "Top8EventFreqDelta pp": round(delta_top8, 2),
            }
        )
    return pd.DataFrame(rows)


def _merge_meta_share(stats: pd.DataFrame, metagame_df: Optional[pd.DataFrame], key_col: str) -> pd.DataFrame:
    if stats.empty or metagame_df is None or metagame_df.empty:  # noqa: E501
        return stats
    if "Deck" not in metagame_df.columns or "Meta" not in metagame_df.columns:
        return stats

    meta = metagame_df.copy()
    meta["Deck"] = meta["Deck"].astype(str).str.strip()

    if key_col == "Deck":
        # Deck shares are percentages; when the same canonical deck appears in
        # multiple source rows, we need total share (sum), not average share.
        meta_group = meta.groupby("Deck", as_index=False)["Meta"].sum().rename(columns={"Meta": "Meta Share %"})
        meta_group["Meta Share %"] = pd.to_numeric(meta_group["Meta Share %"], errors="coerce").fillna(0).round(2)
        return stats.merge(meta_group, on="Deck", how="left")

    # Archetype meta from rules
    try:
        from metagame_input_generator import classify_archetype, load_rules
    except ModuleNotFoundError:
        from .metagame_input_generator import classify_archetype, load_rules

    try:
        rules = load_rules(Path(__file__).resolve().parents[1] / "docs" / "archetype_rules.csv")
    except Exception:
        return stats
    meta["Archetype"] = meta["Deck"].apply(lambda x: classify_archetype(str(x), rules))
    arch_group = meta.groupby("Archetype", as_index=False)["Meta"].sum().rename(columns={"Meta": "Meta Share %"})
    arch_group["Meta Share %"] = pd.to_numeric(arch_group["Meta Share %"], errors="coerce").fillna(0).round(2)
    return stats.merge(arch_group, on="Archetype", how="left")


def _safe_chart(fig: plt.Figure, out_path: Path) -> Optional[Path]:
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(out_path, dpi=150)
        return out_path
    except Exception:
        return None
    finally:
        plt.close(fig)


def _chart_compare_presence(
    deck_stats: pd.DataFrame,
    out_path: Path,
    title: str,
    key_col: str = "Deck",
    sort_by: str = "challenge",
    total_encounter_players: int = 1000,
    sample_size: int = 5,
    min_encounter_pct: float = 5.0,
) -> Optional[Path]:
    if deck_stats.empty:
        return None

    cols = [key_col, "Top32EventFreqPct", "Top8EventFreqPct", "Meta Share %"]
    missing = [c for c in cols if c not in deck_stats.columns]
    if missing:
        return None

    plot_df = deck_stats[cols].copy().fillna(0)
    if str(sort_by).strip().lower() == "meta":
        plot_df = plot_df.sort_values(["Meta Share %", "Top32EventFreqPct", "Top8EventFreqPct"], ascending=False)
    else:
        plot_df = plot_df.sort_values(["Top32EventFreqPct", "Top8EventFreqPct", "Meta Share %"], ascending=False)
    if plot_df.empty:
        return None

    # Use the same encounter-probability cutoff as Encounter Probability charts.
    threshold = min_encounter_pct / 100.0
    N = max(1, int(total_encounter_players))
    ss = max(1, int(sample_size))

    def _enc_prob(meta_share_pct: float) -> float:
        K = round(N * float(meta_share_pct) / 100.0)
        K = max(0, min(K, N))
        ss_capped = min(ss, N)
        max_succ = min(K, ss_capped)
        if max_succ < 1:
            return 0.0
        total = comb(N, ss_capped)
        if total == 0:
            return 0.0
        prob = sum(comb(K, k) * comb(N - K, ss_capped - k) for k in range(1, max_succ + 1))
        return prob / total

    plot_df = plot_df[plot_df["Meta Share %"].apply(_enc_prob) >= threshold]
    if plot_df.empty:
        return None

    x = list(range(len(plot_df)))
    w = 0.26
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.bar([i - w for i in x], plot_df["Top32EventFreqPct"], width=w, label="Top32 Event Frequency %")
    ax.bar(x, plot_df["Top8EventFreqPct"], width=w, label="Top8 Event Frequency %")
    ax.bar([i + w for i in x], plot_df["Meta Share %"], width=w, label="Metagame Share %")

    ax.set_title(title)
    ax.set_ylabel("Share %")
    ax.set_xticks(x)
    ax.set_xticklabels(plot_df[key_col], rotation=35, ha="right")
    ax.legend(loc="upper right")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    return _safe_chart(fig, out_path)


def _chart_delta_ranking(
    stats_df: pd.DataFrame,
    key_col: str,
    out_path: Path,
    title: str,
    total_encounter_players: int = 1000,
    sample_size: int = 5,
    min_encounter_pct: float = 5.0,
) -> Optional[Path]:
    """Vertical ranking of delta between Top32 event frequency and metagame share.
    
    Uses the same encounter-probability cutoff as Challenge vs Meta chart
    to ensure consistent deck selection across all presentations.
    """
    if stats_df.empty:
        return None
    required = {key_col, "Top32EventFreqPct", "Meta Share %"}
    if not required.issubset(set(stats_df.columns)):
        return None

    df = stats_df[[key_col, "Top32EventFreqPct", "Meta Share %"]].copy()
    df["Top32EventFreqPct"] = pd.to_numeric(df["Top32EventFreqPct"], errors="coerce").fillna(0)
    df["Meta Share %"] = pd.to_numeric(df["Meta Share %"], errors="coerce").fillna(0)
    df["Delta pp"] = (df["Top32EventFreqPct"] - df["Meta Share %"]).round(2)

    # Apply same encounter-probability cutoff as Challenge vs Meta to keep decks consistent
    threshold = min_encounter_pct / 100.0
    N = max(1, int(total_encounter_players))
    ss = max(1, int(sample_size))

    def _enc_prob(meta_share_pct: float) -> float:
        K = round(N * float(meta_share_pct) / 100.0)
        K = max(0, min(K, N))
        ss_capped = min(ss, N)
        max_succ = min(K, ss_capped)
        if max_succ < 1:
            return 0.0
        total = comb(N, ss_capped)
        if total == 0:
            return 0.0
        prob = sum(comb(K, k) * comb(N - K, ss_capped - k) for k in range(1, max_succ + 1))
        return prob / total

    plot_df = df[df["Meta Share %"].apply(_enc_prob) >= threshold].copy()
    if plot_df.empty:
        return None
    plot_df = plot_df.sort_values("Delta pp", ascending=False)

    fig, ax = plt.subplots(figsize=(13, 7))
    # Match metagame encounter chart style: bar colors come from a visual palette,
    # independent from delta sign.
    cmap = plt.cm.rainbow
    colors = cmap(np.linspace(1, 0, len(plot_df))) if len(plot_df) > 0 else []
    x = np.arange(len(plot_df))
    ax.bar(x, plot_df["Delta pp"], color=colors)
    ax.axhline(0, color="#333333", linewidth=1.2)
    ax.set_xticks(x)
    ax.set_xticklabels(plot_df[key_col], rotation=35, ha="right")
    ax.set_ylabel("Delta pp (Top32 Event Frequency % - Metagame Share %)")
    ax.set_title(title)
    ax.grid(axis="y", linestyle="--", alpha=0.25)

    for i, val in enumerate(plot_df["Delta pp"]):
        va = "bottom" if val >= 0 else "top"
        offset = 0.8 if val >= 0 else -0.8
        ax.text(i, float(val) + offset, f"{val:+.1f}", ha="center", va=va, fontsize=8)

    return _safe_chart(fig, out_path)


def _chart_conversion_matrix(
    stats_df: pd.DataFrame,
    key_col: str,
    out_path: Path,
    title: str,
) -> Optional[Path]:
    """Matrix chart with percentages for Top32/Top8/Win frequency and conversions."""
    if stats_df.empty:
        return None

    cols = [
        key_col,
        "Top32EventFreqPct",
        "Top8EventFreqPct",
        "WinnerEventFreqPct",
        "Top8ShareOfTop32EntriesPct",
        "WinnerShareOfTop32EntriesPct",
    ]
    if not set(cols).issubset(set(stats_df.columns)):
        return None

    matrix_df = stats_df[cols].copy()
    for c in cols[1:]:
        matrix_df[c] = pd.to_numeric(matrix_df[c], errors="coerce").fillna(0)
    matrix_df = matrix_df.sort_values(["Top32EventFreqPct", "Top8EventFreqPct"], ascending=False).head(14)
    if matrix_df.empty:
        return None

    value_cols = cols[1:]
    data = matrix_df[value_cols].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(12, 7))
    im = ax.imshow(data, aspect="auto", cmap="YlGnBu", vmin=0, vmax=100)
    ax.set_title(title)
    ax.set_yticks(range(len(matrix_df)))
    ax.set_yticklabels(matrix_df[key_col])
    ax.set_xticks(range(len(value_cols)))
    ax.set_xticklabels(
        [
            "Top32\nEvent %",
            "Top8\nEvent %",
            "Winner\nEvent %",
            "Top8/Top32\nEntries %",
            "Win/Top32\nEntries %",
        ],
        rotation=0,
    )
    
    # Two-layer separators keep boundaries visible on both dark and bright cells.
    x_bounds = np.arange(-0.5, len(value_cols), 1)
    y_bounds = np.arange(-0.5, len(matrix_df), 1)
    for xb in x_bounds:
        ax.axvline(x=xb, color="#000000", linewidth=1.2, alpha=0.35, zorder=3)
        ax.axvline(x=xb, color="#ffffff", linewidth=0.6, alpha=0.75, zorder=4)
    for yb in y_bounds:
        ax.axhline(y=yb, color="#000000", linewidth=1.2, alpha=0.35, zorder=3)
        ax.axhline(y=yb, color="#ffffff", linewidth=0.6, alpha=0.75, zorder=4)

    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            txt_color = "#111111" if val < 65 else "#ffffff"
            ax.text(j, i, f"{val:.1f}%", ha="center", va="center", fontsize=8, color=txt_color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("%")
    return _safe_chart(fig, out_path)


def _chart_trend_lines(df: pd.DataFrame, key_col: str, out_path: Path, title: str) -> Optional[Path]:
    if df.empty:
        return None

    work = df.copy()
    work["Place"] = _normalize_numeric(work["Place"])
    work["EventDate"] = work["EventDate"].astype(str).str.strip()
    dates = sorted(work["EventDate"].unique())
    if len(dates) < 2:
        return None

    # Top entities by Top32 event frequency across selected window.
    total_events = max(1, work["EventDate"].nunique())
    base = (work.groupby(key_col)["EventDate"].nunique() / total_events).sort_values(ascending=False)
    top_keys = base.head(10).index.tolist()
    if not top_keys:
        return None

    matrix_top32 = np.zeros((len(top_keys), len(dates)))
    matrix_top8 = np.zeros((len(top_keys), len(dates)))
    for i, key in enumerate(top_keys):
        for j, d in enumerate(dates):
            event_df = work[work["EventDate"] == d]
            present = not event_df[event_df[key_col] == key].empty
            present_top8 = not event_df[(event_df[key_col] == key) & (event_df["Place"] <= 8)].empty
            matrix_top32[i, j] = 100.0 if present else 0.0
            matrix_top8[i, j] = 100.0 if present_top8 else 0.0

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    im1 = axes[0].imshow(matrix_top32, aspect="auto", cmap="Blues", vmin=0, vmax=100)
    axes[0].set_title(f"{title} | Top32 Event Frequency")
    axes[0].set_yticks(range(len(top_keys)))
    axes[0].set_yticklabels(top_keys)

    im2 = axes[1].imshow(matrix_top8, aspect="auto", cmap="Oranges", vmin=0, vmax=100)
    axes[1].set_title(f"{title} | Top8 Event Frequency")
    axes[1].set_yticks(range(len(top_keys)))
    axes[1].set_yticklabels(top_keys)
    axes[1].set_xticks(range(len(dates)))
    axes[1].set_xticklabels(dates, rotation=35, ha="right")

    cbar1 = fig.colorbar(im1, ax=axes[0], fraction=0.02, pad=0.02)
    cbar1.set_label("%")
    cbar2 = fig.colorbar(im2, ax=axes[1], fraction=0.02, pad=0.02)
    cbar2.set_label("%")

    return _safe_chart(fig, out_path)


def rebuild_challenge_history_from_dirs(
    outputs_base: Path,
    history_csv: Path,
    format_name: str,
    challenge_mapping_csv: Optional[Path] = None,
    aliases_csv: Optional[Path] = None,
    rules_csv: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Rebuild challenge history CSV by scanning run directories for challenge decklists.

    Returns the number of unique challenge events written.
    """

    try:
        from metagame_input_generator import (
            classify_archetype,
            load_aliases,
            load_rules,
            load_user_deck_mappings,
            canonicalize_deck_name,
            scan_run_dirs,
        )
    except ModuleNotFoundError:
        from .metagame_input_generator import (
            classify_archetype,
            load_aliases,
            load_rules,
            load_user_deck_mappings,
            canonicalize_deck_name,
            scan_run_dirs,
        )

    def emit(msg: str) -> None:
        if log is not None:
            log(str(msg))

    run_dirs = scan_run_dirs(outputs_base)
    if not run_dirs:
        emit(f"[challenge-rebuild] No run directories found under: {outputs_base}")
        return 0

    mapping_rules = load_user_deck_mappings(challenge_mapping_csv or Path(""))
    alias_rules = load_aliases(aliases_csv or Path(""))
    archetype_rules = load_rules(rules_csv or Path(""))

    mapping_lookup: dict[str, tuple[str, str]] = {}
    for rule in mapping_rules:
        raw_key = normalize_name(rule.raw_name)
        if not raw_key:
            continue
        mapping_lookup[raw_key] = (str(rule.canonical_name).strip(), str(rule.archetype).strip())

    # slug -> rows (latest occurrence wins if duplicated across runs)
    events: dict[str, tuple[dict, pd.DataFrame]] = {}

    for _week_start, _week_end, run_dir in run_dirs:
        for csv_path in sorted(run_dir.glob("challenge_*_decklist.csv")):
            match = RECON_DECKLIST_RE.match(csv_path.name)
            if match is None:
                continue

            size_str, event_date = match.group(1), match.group(2)
            slug = csv_path.stem

            try:
                df = pd.read_csv(csv_path, dtype=str).fillna("")
            except Exception as exc:
                emit(f"[challenge-rebuild] Skip {csv_path.name}: read error ({exc})")
                continue

            if "Deck" not in df.columns:
                emit(f"[challenge-rebuild] Skip {csv_path.name}: missing 'Deck' column")
                continue

            if "Place" not in df.columns:
                df["Place"] = ""
            if "Pilot" not in df.columns:
                df["Pilot"] = ""
            if "Archetype" not in df.columns:
                df["Archetype"] = ""

            out_rows: list[dict] = []
            for _, row in df.iterrows():
                raw_deck = str(row.get("Deck") or "").strip()
                if not raw_deck:
                    continue

                mapped_deck, mapped_archetype = "", ""
                mapping = mapping_lookup.get(normalize_name(raw_deck))
                if mapping is not None:
                    mapped_deck, mapped_archetype = mapping

                deck_name = mapped_deck or canonicalize_deck_name(raw_deck, alias_rules)
                archetype_name = str(mapped_archetype or row.get("Archetype") or "").strip()
                if not archetype_name:
                    archetype_name = classify_archetype(deck_name, archetype_rules)

                out_rows.append(
                    {
                        "Place": str(row.get("Place") or "").strip(),
                        "Deck": deck_name,
                        "Archetype": archetype_name,
                        "Pilot": str(row.get("Pilot") or "").strip(),
                    }
                )

            if not out_rows:
                continue

            event_info = {
                "slug": slug,
                "event_date": event_date,
                "format": format_name,
                "challenge_size": int(size_str),
            }
            events[slug] = (event_info, pd.DataFrame(out_rows, columns=["Place", "Deck", "Archetype", "Pilot"]))

    if history_csv.exists():
        history_csv.unlink()

    if not events:
        emit("[challenge-rebuild] No challenge decklist files found.")
        return 0

    ordered = sorted(
        events.values(),
        key=lambda item: (str(item[0].get("event_date") or ""), int(item[0].get("challenge_size") or 0), str(item[0].get("slug") or "")),
    )

    for event_info, event_df in ordered:
        append_to_challenge_history(
            history_csv=history_csv,
            event_info=event_info,
            decklist_df=event_df,
            log=log,
        )

    emit(f"[challenge-rebuild] Done. events={len(ordered)} history={history_csv}")
    return len(ordered)


def run_challenge_statistics(
    history_csv: Path,
    output_dir: Path,
    format_name: str,
    last_n_events: int = 8,
    week_start: Optional[date] = None,
    week_end: Optional[date] = None,
    metagame_df: Optional[pd.DataFrame] = None,
    log: Optional[Callable[[str], None]] = None,
    total_encounter_players: int = 1000,
    sample_size: int = 5,
    min_encounter_pct: float = 5.0,
) -> ChallengeStatisticsResult:
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    hist = load_challenge_history(history_csv)
    if hist.empty:
        return ChallengeStatisticsResult(output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [])

    fmt_norm = normalize_name(format_name)
    hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
    if hist.empty:
        return ChallengeStatisticsResult(output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [])

    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["ChallengeSize"] = hist["ChallengeSize"].astype(str).str.strip()
    hist["Deck"] = hist["Deck"].astype(str).str.strip()
    hist["Archetype"] = hist["Archetype"].astype(str).str.strip().replace("", "Unknown")

    def _trim(df: pd.DataFrame, n: int) -> pd.DataFrame:
        dates = sorted(df["EventDate"].unique(), reverse=True)[: max(1, n)]
        return df[df["EventDate"].isin(dates)].copy()

    if week_start is not None and week_end is not None:
        if week_end < week_start:
            return ChallengeStatisticsResult(output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [])

        hist_dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
        in_window = (hist_dates >= week_start) & (hist_dates <= week_end)
        hist_window = hist[in_window.fillna(False)].copy()

        if hist_window.empty:
            emit(
                "[challenge-stats] No challenge events in selected window "
                f"{week_start.isoformat()}..{week_end.isoformat()}"
            )
            return ChallengeStatisticsResult(output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [])

        emit(
            "[challenge-stats] Using strict date window "
            f"{week_start.isoformat()}..{week_end.isoformat()} "
            f"({hist_window['EventDate'].nunique()} event date(s))"
        )
        c64 = hist_window[hist_window["ChallengeSize"] == "64"].copy()
        c32 = hist_window[hist_window["ChallengeSize"] == "32"].copy()
        all_df = hist_window.copy()
    else:
        c64 = _trim(hist[hist["ChallengeSize"] == "64"], last_n_events)
        c32 = _trim(hist[hist["ChallengeSize"] == "32"], last_n_events)
        all_df = _trim(hist, last_n_events * 2)

    deck_c64 = _presence_table(c64, "Deck")
    deck_c32 = _presence_table(c32, "Deck")
    deck_all = _presence_table(all_df, "Deck")

    arch_c64 = _presence_table(c64, "Archetype")
    arch_c32 = _presence_table(c32, "Archetype")
    arch_all = _presence_table(all_df, "Archetype")

    if not deck_c64.empty:
        deck_c64["ChallengeSize"] = "64"
    if not deck_c32.empty:
        deck_c32["ChallengeSize"] = "32"
    if not deck_all.empty:
        deck_all["ChallengeSize"] = "ALL"

    if not arch_c64.empty:
        arch_c64["ChallengeSize"] = "64"
    if not arch_c32.empty:
        arch_c32["ChallengeSize"] = "32"
    if not arch_all.empty:
        arch_all["ChallengeSize"] = "ALL"

    deck_trend = _trend_table(all_df, "Deck")
    arch_trend = _trend_table(all_df, "Archetype")

    if not deck_all.empty:
        deck_all = deck_all.merge(deck_trend, on="Deck", how="left")
        deck_all = _merge_meta_share(deck_all, metagame_df, "Deck")
    if not arch_all.empty:
        arch_all = arch_all.merge(arch_trend, on="Archetype", how="left")
        arch_all = _merge_meta_share(arch_all, metagame_df, "Archetype")

    # Save workbook
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path = output_dir / "challenge_statistics.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        wrote_any_sheet = False
        if not deck_c64.empty:
            deck_c64.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="C64_Decks", index=False
            )
            wrote_any_sheet = True
        if not deck_c32.empty:
            deck_c32.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="C32_Decks", index=False
            )
            wrote_any_sheet = True
        if not deck_all.empty:
            deck_all.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="ALL_Decks", index=False
            )
            wrote_any_sheet = True
        if not arch_c64.empty:
            arch_c64.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="C64_Archetypes", index=False
            )
            wrote_any_sheet = True
        if not arch_c32.empty:
            arch_c32.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="C32_Archetypes", index=False
            )
            wrote_any_sheet = True
        if not arch_all.empty:
            arch_all.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="ALL_Archetypes", index=False
            )
            wrote_any_sheet = True
        if not wrote_any_sheet:
            pd.DataFrame([{"Info": "No challenge rows available for selected format/window."}]).to_excel(
                writer, sheet_name="Summary", index=False
            )

    chart_paths: list[Path] = []
    # Presentation charts
    p = _chart_compare_presence(
        deck_all,
        output_dir / "challenge_vs_meta_decks.png",
        "Decks: Top32 Event Frequency / Top8 Event Frequency vs Metagame Share",
        key_col="Deck",
        sort_by="challenge",
        total_encounter_players=total_encounter_players,
        sample_size=sample_size,
        min_encounter_pct=min_encounter_pct,
    )
    if p is not None:
        chart_paths.append(p)

    p = _chart_compare_presence(
        arch_all,
        output_dir / "challenge_vs_meta_archetypes.png",
        "Archetypes: Top32 Event Frequency / Top8 Event Frequency vs Metagame Share",
        key_col="Archetype",
        sort_by="challenge",
        total_encounter_players=total_encounter_players,
        sample_size=sample_size,
        min_encounter_pct=min_encounter_pct,
    )
    if p is not None:
        chart_paths.append(p)

    p = _chart_compare_presence(
        deck_all,
        output_dir / "challenge_vs_meta_sorted_by_meta_decks.png",
        "Decks Sorted by Metagame Share: Challenge Top32 / Top8 vs Metagame",
        key_col="Deck",
        sort_by="meta",
        total_encounter_players=total_encounter_players,
        sample_size=sample_size,
        min_encounter_pct=min_encounter_pct,
    )
    if p is not None:
        chart_paths.append(p)

    p = _chart_compare_presence(
        arch_all,
        output_dir / "challenge_vs_meta_sorted_by_meta_archetypes.png",
        "Archetypes Sorted by Metagame Share: Challenge Top32 / Top8 vs Metagame",
        key_col="Archetype",
        sort_by="meta",
        total_encounter_players=total_encounter_players,
        sample_size=sample_size,
        min_encounter_pct=min_encounter_pct,
    )
    if p is not None:
        chart_paths.append(p)

    p = _chart_conversion_matrix(
        deck_all,
        key_col="Deck",
        out_path=output_dir / "challenge_conversion_matrix_decks.png",
        title="Deck Conversion Matrix (Top32 / Top8 / Winner)",
    )
    if p is not None:
        chart_paths.append(p)

    p = _chart_conversion_matrix(
        arch_all,
        key_col="Archetype",
        out_path=output_dir / "challenge_conversion_matrix_archetypes.png",
        title="Archetype Conversion Matrix (Top32 / Top8 / Winner)",
    )
    if p is not None:
        chart_paths.append(p)

    # NOTE: Trend heatmaps intentionally excluded from presentation pack.
    # Kept available via _chart_trend_lines helper for future optional use.

    p = _chart_delta_ranking(
        deck_all,
        key_col="Deck",
        out_path=output_dir / "challenge_delta_ranking_decks.png",
        title="Deck Delta Ranking: Top32 Event Frequency vs Metagame",
        total_encounter_players=total_encounter_players,
        sample_size=sample_size,
        min_encounter_pct=min_encounter_pct,
    )
    if p is not None:
        chart_paths.append(p)

    p = _chart_delta_ranking(
        arch_all,
        key_col="Archetype",
        out_path=output_dir / "challenge_delta_ranking_archetypes.png",
        title="Archetype Delta Ranking: Top32 Event Frequency vs Metagame",
        total_encounter_players=total_encounter_players,
        sample_size=sample_size,
        min_encounter_pct=min_encounter_pct,
    )
    if p is not None:
        chart_paths.append(p)

    event_count = int(all_df["EventSlug"].nunique()) if not all_df.empty else 0
    deck_rows = int(len(deck_all)) if not deck_all.empty else 0

    emit(f"[challenge-stats] Excel saved: {excel_path}")
    for cp in chart_paths:
        emit(f"[challenge-stats] Chart saved: {cp}")

    return ChallengeStatisticsResult(
        output_dir=output_dir,
        history_csv=history_csv,
        excel_path=excel_path,
        events_processed=event_count,
        deck_rows=deck_rows,
        chart_paths=chart_paths,
    )
