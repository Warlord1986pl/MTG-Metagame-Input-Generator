"""Challenge history and analytics helpers.

This module keeps a persistent Challenge history CSV, rebuilds it from run dirs,
and produces statistics + charts for presentation.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
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
    "EventID",
    "Place",
    "Deck",
    "Archetype",
    "Pilot",
]

RECON_DECKLIST_RE = re.compile(
    r"^challenge_C(\d+)_(\d{4}-\d{2}-\d{2})(?:_(\d+))?_decklist\.csv$", re.IGNORECASE
)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip()).lower()


def _clean_history_df_before_write(df: pd.DataFrame) -> pd.DataFrame:
    """Defensive guard against pandas silently upcasting the EventID column to float during
    some intermediate .loc/concat operation (observed in practice, root cause not pinned down --
    a numeric-looking string column drifting to float64 and coming back out as "12345.0" instead
    of "12345"). Always call right before to_csv() for any of the history files.
    """
    if "EventID" in df.columns:
        df = df.copy()
        df["EventID"] = df["EventID"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


@dataclass
class ChallengeStatisticsResult:
    output_dir: Path
    history_csv: Path
    excel_path: Path
    events_processed: int
    deck_rows: int
    chart_paths: list[Path]
    complete: bool = False


def load_challenge_history(history_csv: Path) -> pd.DataFrame:
    if not history_csv.exists():
        return pd.DataFrame(columns=HISTORY_COLS)
    try:
        df = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
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
                "EventID": str(event_info.get("event_id") or ""),
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
    combined = _clean_history_df_before_write(combined)
    combined.to_csv(history_csv, index=False, encoding="utf-8-sig")
    emit(f"[challenge-history] Saved {len(rows)} rows for C{challenge_size} {event_date} -> {history_csv}")


def sync_challenge_history_window(
    history_csv: Path,
    format_name: str,
    start_date: "date",
    end_date: "date",
    dataset,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Replace (not append) all history rows for *format_name* whose EventDate falls in
    [start_date, end_date] with the mtgo.com-sourced, EventID-keyed rows in *dataset*
    (a ChallengeDatasetResult from challenge_mtgo_source.build_challenge_dataset).

    Any pre-existing rows in that window -- regardless of which legacy EventSlug scheme wrote
    them -- are purged first, so the window can never end up with duplicate/contaminated data
    from an older ingestion path.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    existing = load_challenge_history(history_csv)
    if not existing.empty:
        existing = _clean_history_df_before_write(existing)

    fresh_event_ids = {str(e.event_id).strip() for e in dataset.challenge_events}

    to_drop = pd.Series(dtype=bool)
    dropped = 0
    if not existing.empty:
        existing_dates = pd.to_datetime(existing["EventDate"], errors="coerce").dt.date
        fmt_norm = normalize_name(format_name)
        same_format = existing["Format"].astype(str).apply(normalize_name) == fmt_norm
        in_window = (existing_dates >= start_date) & (existing_dates <= end_date)
        in_scope = same_format & in_window.fillna(False)

        # Only purge rows for events the fresh dataset actually re-fetched this run. An event
        # persisted by an earlier sync but absent from *this* run's registry (rolled off
        # mtgo.com's rolling /decklists index, a transient fetch hiccup, etc.) must be left
        # exactly as-is -- never silently deleted just because this run didn't see it again.
        still_present = existing["EventID"].astype(str).str.strip().isin(fresh_event_ids)
        to_drop = in_scope & still_present
        dropped = int(to_drop.sum())

        missing_ids = sorted(
            set(existing.loc[in_scope & ~still_present, "EventID"].astype(str).str.strip()) - {""}
        )
        if missing_ids:
            emit(
                f"[challenge-history] {len(missing_ids)} previously-persisted event(s) in this window "
                f"are missing from the freshly fetched registry, left untouched: {missing_ids}"
            )

    # Preserve any already-resolved (EventID, Pilot) -> (Deck, Archetype) from the rows about to be
    # purged, whether that resolution came from a human (Review Queue) or an earlier classifier
    # auto-resolve. This value is STICKY: once a row has a real Deck (anything other than
    # NEEDS_MANUAL_REVIEW), a resync can never overwrite it with a different fresh guess, even if
    # the fresh computation is also "resolved" (e.g. MTGGoldfish re-labeling a deck between
    # fetches). Only rows that are still NEEDS_MANUAL_REVIEW get the freshly (re)computed label.
    preserved: dict[tuple[str, str], tuple[str, str]] = {}
    if dropped:
        window_existing = existing[to_drop]
        resolved_rows = window_existing[window_existing["Deck"] != "NEEDS_MANUAL_REVIEW"]
        for _, r in resolved_rows.iterrows():
            key = (str(r["EventID"]).strip(), str(r["Pilot"]).strip())
            preserved[key] = (str(r["Deck"]), str(r["Archetype"]))

    if dropped:
        existing = existing[~to_drop].copy()
        emit(
            f"[challenge-history] Purged {dropped} stale row(s) for {format_name} "
            f"{start_date.isoformat()}..{end_date.isoformat()} (superseded by EventID-keyed sync; "
            f"{len(preserved)} already-resolved deck(s) carried forward)"
        )

    new_rows: list[dict] = []
    preserved_count = 0
    for event in dataset.challenge_events:
        for row in dataset.event_rows.get(event.event_id, []):
            deck, archetype = row.deck, row.archetype
            key = (str(event.event_id).strip(), str(row.pilot).strip())
            # A previously-resolved value (human or classifier) always wins over whatever this
            # run freshly computes for the same (event_id, pilot) -- not just when the fresh
            # value happens to be NEEDS_MANUAL_REVIEW. A resync must never silently swap out an
            # already-confirmed label for a different guess.
            if key in preserved:
                deck, archetype = preserved[key]
                preserved_count += 1
            new_rows.append(
                {
                    "EventDate": event.date,
                    "Format": format_name,
                    "ChallengeSize": str(event.size),
                    "EventSlug": event.slug,
                    "EventID": event.event_id,
                    "Place": str(row.place),
                    "Deck": deck,
                    "Archetype": archetype,
                    "Pilot": row.pilot,
                }
            )
    if preserved_count:
        emit(f"[challenge-history] Restored {preserved_count} previously-resolved deck(s) that the fresh classification alone would have re-flagged")

    combined = pd.concat([existing, pd.DataFrame(new_rows, columns=HISTORY_COLS)], ignore_index=True)
    history_csv.parent.mkdir(parents=True, exist_ok=True)
    combined = _clean_history_df_before_write(combined)
    combined.to_csv(history_csv, index=False, encoding="utf-8-sig")
    emit(
        f"[challenge-history] Synced {len(new_rows)} row(s) ({len(dataset.challenge_events)} events) for "
        f"{format_name} {start_date.isoformat()}..{end_date.isoformat()} -> {history_csv}"
    )
    return len(new_rows)


def apply_challenge_review_selections(
    selections: List[dict],
    history_csv: Path,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """Core, in-memory version: write Deck corrections straight into the persistent Challenge
    history, matched by (event_id, pilot). Recomputes Archetype for every corrected row through
    the same alias/mapping/rules pipeline used everywhere else.

    *selections*: list of dicts with keys "event_id", "pilot", "resolved_deck". Entries with a
    blank resolved_deck are skipped (not an error). Used directly by the GUI's review dialog (no
    CSV round-trip) and by apply_review_corrections() (the CLI/file-based entry point) below.

    Returns {"applied": int, "skipped_blank": int, "not_found": [...], "ambiguous": [...]}.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    try:
        from metagame_input_generator import (
            classify_archetype,
            load_aliases,
            load_rules,
            load_user_deck_mappings,
            canonicalize_deck_name,
            mapping_lookup,
            normalize_name as _normalize_name,
        )
    except ModuleNotFoundError:
        from .metagame_input_generator import (
            classify_archetype,
            load_aliases,
            load_rules,
            load_user_deck_mappings,
            canonicalize_deck_name,
            mapping_lookup,
            normalize_name as _normalize_name,
        )

    to_apply = [s for s in selections if str(s.get("resolved_deck") or "").strip()]
    skipped_blank = len(selections) - len(to_apply)
    if not to_apply:
        emit("[apply-review] No selections with a filled-in resolved_deck; nothing to do.")
        return {"applied": 0, "skipped_blank": skipped_blank, "not_found": [], "ambiguous": []}

    hist = load_challenge_history(history_csv)
    if hist.empty:
        raise ValueError(f"History file is empty or missing: {history_csv}")
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    hist["Pilot"] = hist["Pilot"].astype(str).str.strip()

    docs_dir = Path(__file__).resolve().parents[1] / "docs"
    aliases = load_aliases(docs_dir / "deck_aliases.csv")
    rules = load_rules(docs_dir / "archetype_rules.csv")
    challenge_mappings = load_user_deck_mappings(docs_dir / "challenge_deck_mapping.csv")
    mapping_by_raw = mapping_lookup(challenge_mappings)

    applied = 0
    not_found: list[str] = []
    ambiguous: list[str] = []

    for sel in to_apply:
        event_id = str(sel["event_id"]).strip()
        pilot = str(sel["pilot"]).strip()
        raw_resolved = str(sel["resolved_deck"]).strip()

        match = (hist["EventID"] == event_id) & (hist["Pilot"] == pilot)
        matched_idx = hist.index[match]
        if len(matched_idx) == 0:
            not_found.append(f"event_id={event_id} pilot={pilot!r}")
            continue
        if len(matched_idx) > 1:
            ambiguous.append(f"event_id={event_id} pilot={pilot!r} ({len(matched_idx)} matching rows)")
            continue

        mapping = mapping_by_raw.get(_normalize_name(raw_resolved))
        if mapping is not None and mapping.canonical_name:
            deck_label = mapping.canonical_name
            archetype = mapping.archetype or classify_archetype(deck_label, rules)
        else:
            deck_label = canonicalize_deck_name(raw_resolved, aliases)
            archetype = classify_archetype(deck_label, rules)

        idx = matched_idx[0]
        hist.loc[idx, "Deck"] = deck_label
        hist.loc[idx, "Archetype"] = archetype
        applied += 1
        emit(f"[apply-review] event={event_id} pilot={pilot!r}: Deck -> {deck_label!r} (Archetype: {archetype})")

    if applied:
        hist = _clean_history_df_before_write(hist)
        hist.to_csv(history_csv, index=False, encoding="utf-8-sig")
        emit(f"[apply-review] Wrote {applied} correction(s) to {history_csv}")
    if not_found:
        emit(f"[apply-review] WARNING: {len(not_found)} row(s) had no matching history row: {not_found}")
    if ambiguous:
        emit(f"[apply-review] WARNING: {len(ambiguous)} row(s) matched more than one history row: {ambiguous}")

    return {"applied": applied, "skipped_blank": skipped_blank, "not_found": not_found, "ambiguous": ambiguous}


def apply_review_corrections(
    review_csv: Path,
    history_csv: Path,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """CLI/file-based entry point: read 'resolved_deck' values from a challenge_needs_review_*.csv
    and apply them via apply_challenge_review_selections(). See that function's docstring for the
    matching/archetype-recompute behavior.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    review = pd.read_csv(review_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    required = {"event_id", "pilot", "resolved_deck"}
    missing_cols = required - set(review.columns)
    if missing_cols:
        raise ValueError(f"{review_csv} is missing required column(s): {sorted(missing_cols)}")

    selections = review[["event_id", "pilot", "resolved_deck"]].to_dict("records")
    return apply_challenge_review_selections(selections, history_csv, log=emit)


def find_same_day_tier_collisions(
    history_csv: Path, format_name: Optional[str] = None
) -> pd.DataFrame:
    """Scan the full persisted history for (EventDate, ChallengeSize) groups that contain more
    than one distinct EventSlug, across all tiers -- not just C32/C64.

    For each colliding group, reconstructs each slug's roster and compares content signatures
    (see `roster_content_signature` in metagame_input_generator.py) to flag whether it is a
    safe-to-merge MTGO republish duplicate (matching rosters) or a genuine collision that needs
    manual review (differing rosters). Read-only: does not modify *history_csv*.

    Returns a DataFrame with columns: EventDate, ChallengeSize, SlugCount, EventSlugs,
    ContentHashesMatch, Signatures.
    """
    try:
        from metagame_input_generator import roster_content_signature
    except ModuleNotFoundError:
        from .metagame_input_generator import roster_content_signature

    hist = load_challenge_history(history_csv)
    if hist.empty:
        return pd.DataFrame(
            columns=["EventDate", "ChallengeSize", "SlugCount", "EventSlugs", "ContentHashesMatch", "Signatures"]
        )

    if format_name is not None:
        fmt_norm = normalize_name(format_name)
        hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()

    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["ChallengeSize"] = hist["ChallengeSize"].astype(str).str.strip()
    hist["EventSlug"] = hist["EventSlug"].astype(str).str.strip()

    rows: list[dict] = []
    for (event_date, challenge_size), group in hist.groupby(["EventDate", "ChallengeSize"], sort=True):
        slugs = sorted(group["EventSlug"].unique())
        if len(slugs) <= 1:
            continue

        signatures = [
            roster_content_signature(group[group["EventSlug"] == slug][["Pilot", "Deck"]])
            for slug in slugs
        ]
        rows.append(
            {
                "EventDate": event_date,
                "ChallengeSize": challenge_size,
                "SlugCount": len(slugs),
                "EventSlugs": ", ".join(slugs),
                "ContentHashesMatch": len(set(signatures)) == 1,
                "Signatures": ", ".join(signatures),
            }
        )

    return pd.DataFrame(
        rows, columns=["EventDate", "ChallengeSize", "SlugCount", "EventSlugs", "ContentHashesMatch", "Signatures"]
    )


def _normalize_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _presence_table(df: pd.DataFrame, group_col: str, total_events: int) -> pd.DataFrame:
    """Compute per-deck/archetype metrics in the selected event set.

    *total_events* (N_tier or N_all) is always supplied by the caller -- computed as the count of
    distinct EventID in the relevant scope -- never re-derived here from dates or row counts.
    """
    if df.empty or total_events <= 0:
        return pd.DataFrame()

    work = df.copy()
    work["Place"] = _normalize_numeric(work["Place"])
    work["EventID"] = work["EventID"].astype(str).str.strip()

    grp = work.groupby(group_col)
    event_count = grp["EventID"].nunique()
    appearances = grp[group_col].count()
    avg_place = grp["Place"].mean()
    best_place = grp["Place"].min()
    top8_count = grp["Place"].apply(lambda s: int((s <= 8).sum()))
    top16_count = grp["Place"].apply(lambda s: int((s <= 16).sum()))
    winner_count = grp["Place"].apply(lambda s: int((s == 1).sum()))

    top8_events = work[work["Place"] <= 8].groupby(group_col)["EventID"].nunique()
    top16_events = work[work["Place"] <= 16].groupby(group_col)["EventID"].nunique()
    winner_events = work[work["Place"] == 1].groupby(group_col)["EventID"].nunique()

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

    # Contributing events for this row, so a bad/unexpected event can be identified and the row
    # re-derived without regenerating the whole workbook.
    event_ids_by_key = grp["EventID"].apply(lambda s: ";".join(sorted(set(s))))
    event_dates_by_key = work.groupby(group_col)["EventDate"].apply(lambda s: ";".join(sorted(set(s.astype(str)))))
    stats["EventIDs"] = stats[group_col].map(event_ids_by_key)
    stats["EventDates"] = stats[group_col].map(event_dates_by_key)
    return stats


_TIER_COUNT_COLS: List[str] = [
    "Events",
    "Appearances",
    "Top8Count",
    "Top16Count",
    "WinnerCount",
    "Top32EventCount",
    "Top32EntryCount",
    "Top8EventCount",
    "Top16EventCount",
    "WinnerEventCount",
]


def _sum_tier_tables(tier_tables: Dict[str, pd.DataFrame], group_col: str, n_all: int) -> pd.DataFrame:
    """ALL_* is the literal column-wise sum of the per-tier presence tables -- never a fresh
    aggregation over a separately-assembled "all tiers" dataframe.
    """
    frames = [df for df in tier_tables.values() if df is not None and not df.empty]
    if not frames or n_all <= 0:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["_sum_place"] = combined["AvgPlace"] * combined["Appearances"]

    grp = combined.groupby(group_col)
    summed = grp[_TIER_COUNT_COLS].sum()
    sum_place = grp["_sum_place"].sum()
    best_place = grp["BestPlace"].min()

    stats = summed.reset_index()
    stats["AvgPlace"] = (sum_place.reindex(stats[group_col]).values / stats["Appearances"].clip(lower=1).values).round(1)
    stats["BestPlace"] = best_place.reindex(stats[group_col]).astype(int).values

    stats["PresencePct"] = (stats["Events"] / n_all * 100.0).round(2)
    stats["Top32EventFreqPct"] = stats["PresencePct"]
    stats["Top8PresencePct"] = (stats["Top8EventCount"] / n_all * 100.0).round(2)
    stats["Top16PresencePct"] = (stats["Top16EventCount"] / n_all * 100.0).round(2)
    stats["Top8EventFreqPct"] = stats["Top8PresencePct"]
    stats["Top16EventFreqPct"] = stats["Top16PresencePct"]
    stats["WinnerEventFreqPct"] = (stats["WinnerEventCount"] / n_all * 100.0).round(2)

    stats["Top8Rate"] = ((stats["Top8Count"] / stats["Appearances"].clip(lower=1)) * 100.0).round(2)
    stats["Top16Rate"] = ((stats["Top16Count"] / stats["Appearances"].clip(lower=1)) * 100.0).round(2)
    stats["WinnerRate"] = ((stats["WinnerCount"] / stats["Appearances"].clip(lower=1)) * 100.0).round(2)
    stats["Top8ShareOfTop32EntriesPct"] = stats["Top8Rate"]
    stats["Top16ShareOfTop32EntriesPct"] = stats["Top16Rate"]
    stats["WinnerShareOfTop32EntriesPct"] = stats["WinnerRate"]

    def _union_semicolon(series: pd.Series) -> str:
        vals: set = set()
        for s in series.dropna():
            vals.update(x for x in str(s).split(";") if x)
        return ";".join(sorted(vals))

    event_ids_all = grp["EventIDs"].apply(_union_semicolon)
    event_dates_all = grp["EventDates"].apply(_union_semicolon)
    stats["EventIDs"] = stats[group_col].map(event_ids_all)
    stats["EventDates"] = stats[group_col].map(event_dates_all)
    return stats


def _pilot_table(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-pilot metrics: wins, top-8, top-16, appearances, best deck."""
    if df.empty or "Pilot" not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    work["Place"] = _normalize_numeric(work["Place"])
    work["Pilot"] = work["Pilot"].astype(str).str.strip().replace("", pd.NA)
    work = work.dropna(subset=["Pilot"])
    work = work[work["Pilot"] != ""]
    if work.empty:
        return pd.DataFrame()

    grp = work.groupby("Pilot")
    wins = grp["Place"].apply(lambda s: int((s == 1).sum()))
    top8 = grp["Place"].apply(lambda s: int((s <= 8).sum()))
    top16 = grp["Place"].apply(lambda s: int((s <= 16).sum()))
    appearances = grp["Place"].count()
    avg_place = grp["Place"].mean().round(1)
    best_place = grp["Place"].min().fillna(999).astype(int)

    def _best_deck(sub: pd.DataFrame) -> str:
        if "Deck" not in sub.columns or sub.empty:
            return ""
        best_row = sub.loc[sub["Place"].idxmin()] if sub["Place"].notna().any() else sub.iloc[0]
        return str(best_row.get("Deck", "")).strip()

    best_deck = grp.apply(_best_deck)

    stats = pd.DataFrame({
        "Pilot": wins.index,
        "Wins": wins.values,
        "Top8": top8.values,
        "Top16": top16.values,
        "Appearances": appearances.values,
        "AvgPlace": avg_place.values,
        "BestPlace": best_place.values,
        "BestDeck": best_deck.values,
    })
    stats = stats.sort_values(["Wins", "Top8", "Top16", "Appearances"], ascending=[False, False, False, False])
    stats = stats.reset_index(drop=True)
    stats.insert(0, "Rank", range(1, len(stats) + 1))
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


def _load_run_local_metagame_df(output_dir: Path) -> Optional[pd.DataFrame]:
    """Load the run-local metagame input when challenge stats live under
    <run_dir>/statistics/challenges.

    This keeps challenge charts aligned with the exact weekly run even if a
    caller accidentally passes a global history dataframe instead of the local
    metagame snapshot.
    """
    try:
        run_dir = output_dir.parent.parent
    except Exception:
        return None

    candidate = run_dir / "metagame_input.csv"
    if not candidate.exists():
        return None

    try:
        df = pd.read_csv(candidate)
    except Exception:
        return None

    if "Deck" not in df.columns or "Meta" not in df.columns:
        return None
    return df


_RUN_DIR_WINDOW_RE = re.compile(r"^W\d+_(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})$")


def _parse_run_dir_window(run_dir: Path) -> Optional["tuple[date, date]"]:
    """The (start, end) window baked into a weekly run directory's own name, e.g.
    W32_2026-07-27_to_2026-08-09 -> (2026-07-27, 2026-08-09). This is the window the run-local
    metagame_input.csv (the league/meta scrape) was actually built for -- used to catch a Challenge
    stats recompute being pointed at a different, narrower/wider window than the metagame data it's
    merging Meta Share from.
    """
    m = _RUN_DIR_WINDOW_RE.match(run_dir.name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    except ValueError:
        return None


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
    # A strict top-14 cut can drop decks that only spike in a few Top8s within
    # the selected window (for example, Domain Zoo in shorter challenge ranges).
    # Keep a slightly wider panel so the matrix stays readable while preserving
    # those meaningful appearances.
    matrix_df = matrix_df.sort_values(["Top32EventFreqPct", "Top8EventFreqPct"], ascending=False).head(20)
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

    # First pass: find every (event_date, challenge_size) that has at least one mtgo.com-sourced
    # decklist file (filename carries a real event_id). Those supersede any legacy MTGGoldfish-era
    # file for the same (date, size) that lacks an event_id -- the legacy file is very likely the
    # same physical event re-exported without an id during an earlier weekly run whose window
    # happened to overlap this one, not a second real tournament (a real second tournament on the
    # same day/tier also gets its own id'd file under the new source, so it is never suppressed).
    dated_ids: dict[tuple[str, str], set[str]] = {}
    for _week_start, _week_end, run_dir in run_dirs:
        for csv_path in sorted(run_dir.glob("challenge_*_decklist.csv")):
            match = RECON_DECKLIST_RE.match(csv_path.name)
            if match is None or not match.group(3):
                continue
            dated_ids.setdefault((match.group(2), match.group(1)), set()).add(match.group(3))

    # key -> rows (latest occurrence wins if duplicated across runs)
    events: dict[str, tuple[dict, pd.DataFrame]] = {}

    for _week_start, _week_end, run_dir in run_dirs:
        for csv_path in sorted(run_dir.glob("challenge_*_decklist.csv")):
            match = RECON_DECKLIST_RE.match(csv_path.name)
            if match is None:
                continue

            size_str, event_date, event_id = match.group(1), match.group(2), match.group(3) or ""
            slug = csv_path.stem

            if not event_id and (event_date, size_str) in dated_ids:
                emit(
                    f"[challenge-rebuild] Skip {csv_path.name}: superseded by mtgo.com-sourced "
                    f"event_id(s) {sorted(dated_ids[(event_date, size_str)])} for C{size_str} {event_date}"
                )
                continue

            key = f"{event_date}|{size_str}|{event_id}" if event_id else slug

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
                "event_id": event_id,
            }
            events[key] = (event_info, pd.DataFrame(out_rows, columns=["Place", "Deck", "Archetype", "Pilot"]))

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


_DEGENERACY_CHECK_COLS: List[str] = [
    "AvgPlace",
    "BestPlace",
    "Top8Count",
    "Top16Count",
    "WinnerCount",
    "PresencePct",
    "Top32EventFreqPct",
    "Top8EventFreqPct",
    "Top16EventFreqPct",
    "WinnerEventFreqPct",
    "Top8Rate",
    "Top16Rate",
    "WinnerRate",
]


_EVENT_FREQ_PCT_COLS: set = {
    "PresencePct",
    "Top32EventFreqPct",
    "Top8EventFreqPct",
    "Top16EventFreqPct",
    "WinnerEventFreqPct",
}

# Below this many distinct events in scope, an event-frequency-% column (Events/N_events*100)
# tying across every row stops being informative: with e.g. 5 events, "every archetype showed up
# in all 5" is a perfectly ordinary outcome for broad archetype buckets, not a sign that a
# merge/groupby dropped real values. Count-based columns (Top8Count, WinnerCount, ...) are not
# denominator-scaled this way and keep being checked regardless of n_events.
_SMALL_SAMPLE_EVENT_THRESHOLD = 6


def _check_no_degenerate_columns(label: str, tbl: pd.DataFrame, n_events: Optional[int] = None) -> List[str]:
    """A metric column that is entirely constant or entirely zero across every row of a tab
    with enough rows to vary usually means a merge/groupby dropped its real values (e.g. every
    row silently fell back to the same default) rather than a genuine tie. Tables with under 3
    rows are skipped -- "every archetype has BestPlace 1" is unremarkable when there is only one
    archetype in the window.
    """
    problems: List[str] = []
    if tbl is None or len(tbl) < 3:
        return problems
    small_sample = n_events is not None and n_events < _SMALL_SAMPLE_EVENT_THRESHOLD
    for col in _DEGENERACY_CHECK_COLS:
        if col not in tbl.columns:
            continue
        if small_sample and col in _EVENT_FREQ_PCT_COLS:
            continue
        values = pd.to_numeric(tbl[col], errors="coerce").dropna()
        if values.empty:
            continue
        if (values == 0).all():
            problems.append(f"{label}: column {col!r} is zero for every row ({len(values)} rows)")
        elif values.nunique() <= 1:
            problems.append(f"{label}: column {col!r} is constant ({values.iloc[0]}) across every row ({len(values)} rows)")
    return problems


def _check_challenge_invariants(
    hist_window: pd.DataFrame,
    tiers: List[str],
    n_tier: Dict[str, int],
    n_all: int,
    tier_deck_tables: Dict[str, pd.DataFrame],
    tier_arch_tables: Dict[str, pd.DataFrame],
    deck_all: pd.DataFrame,
    arch_all: pd.DataFrame,
    pilot_df: pd.DataFrame,
    premier_event_ids: Optional[set] = None,
) -> "tuple[List[str], List[str]]":
    """The invariants. Returns (problems, warnings): problems are hard failures (xlsx not
    written), warnings are logged but never block the write.
    """
    problems: List[str] = []
    warnings: List[str] = []
    work = hist_window.copy()
    work["Place"] = _normalize_numeric(work["Place"])

    # each EventID maps to exactly one EventDate
    date_per_event = work.groupby("EventID")["EventDate"].nunique()
    bad_date = date_per_event[date_per_event > 1]
    if not bad_date.empty:
        for eid in bad_date.index:
            dates = sorted(work.loc[work["EventID"] == eid, "EventDate"].unique())
            problems.append(f"event {eid}: maps to more than one EventDate ({dates})")

    # 13: final_rank == {1..32} per event (re-verified end to end, in case anything downstream
    # of the mtgo.com parse step dropped or duplicated a row).
    for eid, grp in work.groupby("EventID"):
        places = sorted(grp["Place"].dropna().astype(int).tolist())
        if places != list(range(1, 33)):
            problems.append(f"event {eid}: Place values are not exactly 1..32 (got {places})")

    # 9: no event_id in more than one tier
    tier_per_event = work.groupby("EventID")["ChallengeSize"].nunique()
    bad_tier = tier_per_event[tier_per_event > 1]
    if not bad_tier.empty:
        problems.append(f"event_id(s) present in more than one tier: {list(bad_tier.index)}")

    # 10: no premier event in any Challenge tab
    if premier_event_ids:
        overlap = set(work["EventID"].unique()) & set(premier_event_ids)
        if overlap:
            problems.append(f"premier event_id(s) found inside Challenge history: {sorted(overlap)}")

    # 12: every event contributes exactly 32 decklists
    counts = work.groupby("EventID").size()
    bad_counts = counts[counts != 32]
    if not bad_counts.empty:
        problems.append(f"event(s) without exactly 32 decklists: {bad_counts.to_dict()}")

    for tier in tiers:
        n = n_tier.get(tier, 0)
        for label, tbl in (("Decks", tier_deck_tables.get(tier)), ("Archetypes", tier_arch_tables.get(tier))):
            if tbl is None or tbl.empty:
                continue
            sum_winner = int(tbl["WinnerCount"].sum())
            if sum_winner != n:  # 1
                problems.append(f"C{tier}_{label}: sum(WinnerCount)={sum_winner} != N_tier={n}")
            sum_winner_events = int(tbl["WinnerEventCount"].sum())
            if sum_winner_events != n:  # 4
                problems.append(f"C{tier}_{label}: sum(WinnerEventCount)={sum_winner_events} != N_tier={n}")
            sum_top32entries = int(tbl["Top32EntryCount"].sum())
            if sum_top32entries != 32 * n:  # 2
                problems.append(f"C{tier}_{label}: sum(Top32EntryCount)={sum_top32entries} != 32*N_tier={32 * n}")
            sum_top8 = int(tbl["Top8Count"].sum())
            if sum_top8 != 8 * n:  # 3
                problems.append(f"C{tier}_{label}: sum(Top8Count)={sum_top8} != 8*N_tier={8 * n}")

            problems.extend(_check_no_degenerate_columns(f"C{tier}_{label}", tbl, n_events=n))

            bad_rows = tbl[  # 5
                (tbl["WinnerEventCount"] > tbl["Top8EventCount"])
                | (tbl["Top8EventCount"] > tbl["Top32EventCount"])
                | (tbl["Top32EventCount"] > n)
            ]
            if not bad_rows.empty:
                problems.append(
                    f"C{tier}_{label}: {len(bad_rows)} row(s) violate "
                    "WinnerEventCount<=Top8EventCount<=Top32EventCount<=N_tier"
                )
            bad_rows2 = tbl[  # 6
                (tbl["WinnerCount"] > tbl["Top8Count"]) | (tbl["Top8Count"] > tbl["Top32EntryCount"])
            ]
            if not bad_rows2.empty:
                problems.append(
                    f"C{tier}_{label}: {len(bad_rows2)} row(s) violate WinnerCount<=Top8Count<=Top32EntryCount"
                )

            if n > 0:  # 7 -- grid check against the unrounded ratio; 0.01 accounts for the
                       # display .round(2), not for a denominator bug (which misses by whole points)
                for pct_col, count_col in (
                    ("Top32EventFreqPct", "Top32EventCount"),
                    ("Top8EventFreqPct", "Top8EventCount"),
                    ("WinnerEventFreqPct", "WinnerEventCount"),
                ):
                    expected = tbl[count_col] / n * 100.0
                    diff = (tbl[pct_col] - expected).abs()
                    bad_grid = tbl[diff > 0.01]
                    if not bad_grid.empty:
                        problems.append(f"C{tier}_{label}: {pct_col} not on the 1/{n} grid for {len(bad_grid)} row(s)")

    if n_all != sum(n_tier.values()):  # 8a
        problems.append(f"N_all={n_all} != sum(N_tier)={sum(n_tier.values())}")

    for label, all_tbl, tier_tables in (
        ("Decks", deck_all, tier_deck_tables),
        ("Archetypes", arch_all, tier_arch_tables),
    ):
        if all_tbl.empty:
            continue
        for count_col in _TIER_COUNT_COLS:  # 8b -- non-fatal: ALL_* is a literal column-wise sum
            # of the per-tier tables (never a fresh aggregation), so this should be structurally
            # impossible; if it ever fires it's surfaced as a warning naming the mismatch, not an
            # abort, and the ALL_* tabs are still written (just never allowed to feed a computed
            # column elsewhere).
            total_from_tiers = sum(int(t[count_col].sum()) for t in tier_tables.values() if t is not None and not t.empty)
            total_all = int(all_tbl[count_col].sum())
            if total_from_tiers != total_all:
                warnings.append(f"ALL_{label}: sum({count_col})={total_all} != sum over tiers={total_from_tiers} (possible double-count)")

        problems.extend(_check_no_degenerate_columns(f"ALL_{label}", all_tbl, n_events=n_all))

    if not pilot_df.empty:  # 11
        sum_wins = int(pilot_df["Wins"].sum())
        sum_top8_pilots = int(pilot_df["Top8"].sum())
        sum_appear = int(pilot_df["Appearances"].sum())
        if sum_wins != n_all:
            problems.append(f"BestPilots: sum(Wins)={sum_wins} != N_all={n_all}")
        if sum_top8_pilots != 8 * n_all:
            problems.append(f"BestPilots: sum(Top8)={sum_top8_pilots} != 8*N_all={8 * n_all}")
        if sum_appear != 32 * n_all:
            problems.append(f"BestPilots: sum(Appearances)={sum_appear} != 32*N_all={32 * n_all}")

        if not deck_all.empty:
            winners_by_deck = work[work["Place"] == 1].groupby("Deck").size()
            deck_winner_count = deck_all.set_index("Deck")["WinnerCount"]
            for deck, expected_winners in winners_by_deck.items():
                actual = int(deck_winner_count.get(deck, 0))
                if actual != int(expected_winners):
                    problems.append(
                        f"BestPilots/ALL_Decks mismatch for deck {deck!r}: raw winner rows={expected_winners} "
                        f"vs ALL_Decks WinnerCount={actual}"
                    )

    return problems, warnings


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
    premier_event_ids: Optional[set] = None,
    completeness: Optional[object] = None,
    allow_partial: bool = False,
) -> ChallengeStatisticsResult:
    """*completeness*, if given, is a challenge_mtgo_source.CompletenessSummary (duck-typed here to
    avoid a module dependency): registry_count, fetched_count, complete, missing, tier_counts_registry,
    tier_counts_fetched, premier_checked, premier_count, premier_events, premier_note.

    *completeness omitted entirely* is treated as INCOMPLETE (fail-closed) -- it is never silently
    upgraded to complete=true just because the caller didn't check. A caller that has no way to
    verify completeness (e.g. a stats-only recompute) must build one first, e.g. via
    challenge_mtgo_source.verify_registry_against_history(), and pass it in.

    When incomplete and *allow_partial* is False (default), the xlsx is NOT written at all -- only
    the JSON sidecar (challenge_statistics.status.json) and log output. When incomplete and
    *allow_partial* is True, the xlsx IS written but stamped PARTIAL in both the STATUS sheet and
    the output filename (challenge_statistics_PARTIAL.xlsx). ChallengeStatisticsResult.complete
    always reflects the real completeness regardless of allow_partial.
    """
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

    local_metagame_df = _load_run_local_metagame_df(output_dir)
    if local_metagame_df is not None:
        metagame_df = local_metagame_df

    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["ChallengeSize"] = hist["ChallengeSize"].astype(str).str.strip()
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
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
            f"{week_start.isoformat()}..{week_end.isoformat()}"
        )
    else:
        hist_window = _trim(hist, last_n_events * 2)

    missing_id = hist_window[hist_window["EventID"] == ""]
    if not missing_id.empty:
        bad_slugs = sorted(missing_id["EventSlug"].astype(str).unique())
        emit(
            "[challenge-stats] ABORT: rows without an EventID in the selected window (legacy/unmigrated "
            f"data), xlsx NOT written. Re-run the mtgo.com sync for these dates. Affected EventSlug(s): {bad_slugs}"
        )
        return ChallengeStatisticsResult(output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [])

    # Rows still stuck at NEEDS_MANUAL_REVIEW (classifier could not confidently place them, and no
    # human resolution has been applied yet) are NOT dropped from the stats tables and NOT silently
    # merged into whatever real deck/archetype the classifier guessed second-best -- they get their
    # own explicit "Unknown" row, so sum(Top32EntryCount) == 32 * event_count always holds and the
    # gap is visible in the data instead of absorbed. This relabel is local to the stats tables
    # only -- the underlying history_csv keeps the literal NEEDS_MANUAL_REVIEW marker untouched, so
    # the review/rescan workflow (challenge_mtgo_source.py) still finds these rows correctly.
    unresolved_mask = hist_window["Deck"] == "NEEDS_MANUAL_REVIEW"
    n_unresolved = int(unresolved_mask.sum())
    if n_unresolved:
        hist_window.loc[unresolved_mask, "Deck"] = "Unknown"
        hist_window.loc[unresolved_mask, "Archetype"] = "Unknown"
        emit(
            f"[challenge-stats] {n_unresolved} still-unresolved deck(s) (NEEDS_MANUAL_REVIEW) bucketed "
            "under an explicit 'Unknown' row in the stats tables, not dropped."
        )

    tiers = sorted(hist_window["ChallengeSize"].unique(), key=lambda s: int(s) if str(s).isdigit() else -1)

    n_tier: Dict[str, int] = {}
    tier_deck_tables: Dict[str, pd.DataFrame] = {}
    tier_arch_tables: Dict[str, pd.DataFrame] = {}
    for tier in tiers:
        tier_df = hist_window[hist_window["ChallengeSize"] == tier].copy()
        n = int(tier_df["EventID"].nunique())
        n_tier[tier] = n

        deck_tbl = _presence_table(tier_df, "Deck", n)
        arch_tbl = _presence_table(tier_df, "Archetype", n)
        if not deck_tbl.empty:
            deck_tbl["ChallengeSize"] = tier
        if not arch_tbl.empty:
            arch_tbl["ChallengeSize"] = tier
        tier_deck_tables[tier] = deck_tbl
        tier_arch_tables[tier] = arch_tbl

    n_all = sum(n_tier.values())
    all_df = hist_window.copy()

    # ALL_* is the literal column-wise sum of the per-tier tables -- never a fresh aggregation.
    deck_all = _sum_tier_tables(tier_deck_tables, "Deck", n_all)
    arch_all = _sum_tier_tables(tier_arch_tables, "Archetype", n_all)
    if not deck_all.empty:
        deck_all["ChallengeSize"] = "ALL"
    if not arch_all.empty:
        arch_all["ChallengeSize"] = "ALL"

    pilot_df = _pilot_table(all_df)

    deck_trend = _trend_table(all_df, "Deck")
    arch_trend = _trend_table(all_df, "Archetype")

    if not deck_all.empty:
        deck_all = deck_all.merge(deck_trend, on="Deck", how="left")
        deck_all = _merge_meta_share(deck_all, metagame_df, "Deck")
    if not arch_all.empty:
        arch_all = arch_all.merge(arch_trend, on="Archetype", how="left")
        arch_all = _merge_meta_share(arch_all, metagame_df, "Archetype")

    problems, invariant_warnings = _check_challenge_invariants(
        hist_window=hist_window,
        tiers=tiers,
        n_tier=n_tier,
        n_all=n_all,
        tier_deck_tables=tier_deck_tables,
        tier_arch_tables=tier_arch_tables,
        deck_all=deck_all,
        arch_all=arch_all,
        pilot_df=pilot_df,
        premier_event_ids=premier_event_ids,
    )
    for w in invariant_warnings:
        emit(f"[challenge-stats] WARNING: {w}")
    if problems:
        emit(f"[challenge-stats] ABORT: {len(problems)} invariant check(s) failed, xlsx NOT written:")
        for p in problems:
            emit(f"  - {p}")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "challenge_statistics.status.json").write_text(
            json.dumps({
                "complete": False,
                "registry_count": None,
                "fetched_count": None,
                "missing": [],
                "window_start": week_start.isoformat() if week_start else None,
                "window_end": week_end.isoformat() if week_end else None,
                "league_window_start": None,
                "league_window_end": None,
                "window_consistent": None,
                "tier_counts_registry": {},
                "tier_counts_fetched": {},
                "premier_checked": False,
                "premier_count": None,
                "premier_events": [],
                "premier_note": "Premier scan did not complete",
                "invariant_problems": problems,
            }, indent=2),
            encoding="utf-8",
        )
        return ChallengeStatisticsResult(output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [])

    # --- Completeness / window-consistency gate -----------------------------------------------
    # completeness omitted entirely is fail-closed INCOMPLETE, never silently upgraded to true.
    is_complete = bool(getattr(completeness, "complete", False)) if completeness is not None else False
    registry_count = getattr(completeness, "registry_count", None) if completeness is not None else None
    fetched_count = getattr(completeness, "fetched_count", None) if completeness is not None else None
    if is_complete and not registry_count:
        raise AssertionError(
            "refusing to write complete=true with a null/zero registry_count -- this is exactly "
            "the fail-closed invariant this gate exists to enforce"
        )
    if completeness is None:
        emit(
            "[challenge-stats] WARNING: no completeness signal provided by caller -- treating window "
            "as INCOMPLETE (fail-closed), not defaulting to complete=true"
        )

    missing_summary: List[dict] = []
    for m in (getattr(completeness, "missing", []) or []) if completeness is not None else []:
        missing_summary.append({
            "EventID": getattr(m, "event_id", ""),
            "EventDate": getattr(m, "date", ""),
            "Status": getattr(m, "status", ""),
            "Reason": getattr(m, "reason", ""),
        })

    tier_counts_registry = {
        str(k): v for k, v in (getattr(completeness, "tier_counts_registry", {}) or {}).items()
    } if completeness is not None else {}
    tier_counts_fetched = {
        str(k): v for k, v in (getattr(completeness, "tier_counts_fetched", {}) or {}).items()
    } if completeness is not None else {}

    premier_checked = bool(getattr(completeness, "premier_checked", False)) if completeness is not None else False
    premier_count = getattr(completeness, "premier_count", None) if completeness is not None else None
    if premier_checked and premier_count is None:
        raise AssertionError("refusing to write premier_checked=true with a null premier_count")
    premier_events_raw = (getattr(completeness, "premier_events", []) or []) if completeness is not None else []
    premier_events_summary = [
        {"EventID": eid, "EventType": etype, "EventDate": edate} for eid, etype, edate in premier_events_raw
    ]
    premier_note = (
        getattr(completeness, "premier_note", "Premier scan did not complete")
        if completeness is not None else "Premier scan did not complete"
    )

    # League/meta window consistency: the metagame_df merged into deck_all/arch_all for Meta Share
    # deltas must come from the SAME window as the Challenge window, or every delta value silently
    # corrupts. Only checked when a run-local metagame_input.csv was actually loaded and merged
    # (local_metagame_df is not None) -- otherwise there's no league window to be inconsistent with.
    league_window_start: Optional[date] = None
    league_window_end: Optional[date] = None
    window_consistent: Optional[bool] = None
    if local_metagame_df is not None:
        parsed_window = _parse_run_dir_window(output_dir.parent.parent)
        if parsed_window is not None:
            league_window_start, league_window_end = parsed_window
            if week_start is not None and week_end is not None:
                window_consistent = (league_window_start, league_window_end) == (week_start, week_end)
                if not window_consistent:
                    is_complete = False
                    emit(
                        f"[challenge-stats] ERROR: window mismatch -- challenge window "
                        f"{week_start.isoformat()}..{week_end.isoformat()} != league/meta window "
                        f"{league_window_start.isoformat()}..{league_window_end.isoformat()}; "
                        "Meta Share deltas would be corrupted"
                    )

    status_payload = {
        "complete": is_complete,
        "registry_count": registry_count,
        "fetched_count": fetched_count,
        "missing": missing_summary,
        "window_start": week_start.isoformat() if week_start else None,
        "window_end": week_end.isoformat() if week_end else None,
        "league_window_start": league_window_start.isoformat() if league_window_start else None,
        "league_window_end": league_window_end.isoformat() if league_window_end else None,
        "window_consistent": window_consistent,
        "tier_counts_registry": tier_counts_registry,
        "tier_counts_fetched": tier_counts_fetched,
        "premier_checked": premier_checked,
        "premier_count": premier_count,
        "premier_events": premier_events_summary,
        "premier_note": premier_note,
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    partial = False
    if not is_complete:
        if not allow_partial:
            status_payload["partial"] = False
            (output_dir / "challenge_statistics.status.json").write_text(
                json.dumps(status_payload, indent=2), encoding="utf-8"
            )
            emit(
                f"[challenge-stats] ABORT: window INCOMPLETE, xlsx NOT written (pass allow_partial=True "
                f"to override): registry_count={registry_count} fetched_count={fetched_count} "
                f"missing={missing_summary} window_consistent={window_consistent}"
            )
            return ChallengeStatisticsResult(
                output_dir, history_csv, output_dir / "challenge_statistics.xlsx", 0, 0, [], complete=False
            )
        partial = True
        emit(
            "[challenge-stats] allow_partial=True: writing PARTIAL xlsx despite incomplete window "
            f"(registry_count={registry_count} fetched_count={fetched_count})"
        )

    status_payload["partial"] = partial
    (output_dir / "challenge_statistics.status.json").write_text(
        json.dumps(status_payload, indent=2), encoding="utf-8"
    )

    excel_path = output_dir / ("challenge_statistics_PARTIAL.xlsx" if partial else "challenge_statistics.xlsx")

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame([{
            "Complete": is_complete,
            "Partial": partial,
            "RegistryCount": registry_count,
            "FetchedCount": fetched_count,
            "MissingEventIDs": "; ".join(str(m["EventID"]) for m in missing_summary),
            "MissingStatuses": "; ".join(f"{m['EventID']}:{m['Status']}" for m in missing_summary),
            "PremierChecked": premier_checked,
            "PremierNote": premier_note,
            "WindowConsistent": window_consistent,
            "LeagueWindowStart": status_payload["league_window_start"],
            "LeagueWindowEnd": status_payload["league_window_end"],
        }]).to_excel(writer, sheet_name="STATUS", index=False)

        wrote_any_sheet = False
        for tier in tiers:
            deck_tbl = tier_deck_tables[tier]
            if not deck_tbl.empty:
                deck_tbl.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                    writer, sheet_name=f"C{tier}_Decks", index=False
                )
                wrote_any_sheet = True
            arch_tbl = tier_arch_tables[tier]
            if not arch_tbl.empty:
                arch_tbl.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                    writer, sheet_name=f"C{tier}_Archetypes", index=False
                )
                wrote_any_sheet = True
        if not deck_all.empty:
            deck_all.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="ALL_Decks", index=False
            )
            wrote_any_sheet = True
        if not arch_all.empty:
            arch_all.sort_values(["Top32EventFreqPct", "Top32EventCount"], ascending=[False, False]).to_excel(
                writer, sheet_name="ALL_Archetypes", index=False
            )
            wrote_any_sheet = True
        if not pilot_df.empty:
            pilot_df.to_excel(writer, sheet_name="BestPilots", index=False)
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

    event_count = n_all
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
        complete=is_complete,
    )
