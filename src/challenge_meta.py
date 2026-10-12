"""Meta share from Challenge Top 32 entries (after the card rule) instead of the Videre API.

Decision 2026-10-12: Videre's per-name Meta was, for W41, practically identical to each deck's
share of Challenge Top 32 entries (r = 0.998, mean |diff| 0.10 pp, max 0.46 pp) -- but Videre only
knows submitted names, so it can never see the card-based Broodscale rule (broodscale_rule.py).
The metagame statistics (deck_analysis*, encounter, trend) therefore take Meta from our own
Challenge history, which is card-based after the rule:

  Meta         = deck entries / all entries in Challenge Top 32 rows of the window * 100
  MetaSource   = "challenge_top32_cardrule"
  Meta_Videre  = the old Videre value, control only, never used in a computation

Winrate still comes from Videre match data, which is per submitted name and cannot be split by
cards: for Broodscale Combo and Eldrazi Ramp in any window where the rule moved >= 1 Challenge
entry, Winrate / Winrate Game Count are NaN with WR_Flag = "label_mixed_by_card_rule". A deck with
Challenge entries but no Videre row gets NaN with WR_Flag = "no_videre_winrate".
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    from broodscale_rule import BROODSCALE_DECK, _load_ledger, _norm_id
except ModuleNotFoundError:
    from .broodscale_rule import BROODSCALE_DECK, _load_ledger, _norm_id

META_SOURCE_CHALLENGE = "challenge_top32_cardrule"
WR_FLAG_MIXED = "label_mixed_by_card_rule"
WR_FLAG_NO_VIDERE = "no_videre_winrate"
TREND_CAPTION = "share of Challenge Top 32 entries, card-based Broodscale rule"
MIXED_LABEL_DECKS = (BROODSCALE_DECK, "Eldrazi Ramp")
CHALLENGE_META_XLSX = "metagame_input_challenge_top32.xlsx"
CHALLENGE_META_CSV = "metagame_input_challenge_top32.csv"
TOP_N_PLACES = 32

OUTPUT_COLS: List[str] = [
    "Deck",
    "Source Deck Names",
    "Meta",
    "MetaSource",
    "Meta_Videre",
    "Top32Entries",
    "Winrate",
    "Winrate Game Count",
    "WR_Flag",
    "Archetype",
    "Meta Tier",
]


class ChallengeMetaError(RuntimeError):
    """The window's Challenge rows can't yield a trustworthy share (unresolved/legacy rows)."""


def _window_challenge_rows(history_csv: Path, start: date, end: date, format_name: str) -> pd.DataFrame:
    hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    fmt = format_name.strip().lower()
    hist = hist[hist["Format"].astype(str).str.strip().str.lower() == fmt]
    dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    hist = hist[((dates >= start) & (dates <= end)).fillna(False)].copy()
    hist["EventID"] = hist["EventID"].map(_norm_id)
    hist["_place"] = pd.to_numeric(hist["Place"], errors="coerce")
    return hist


def challenge_top32_share(history_csv: Path, start: date, end: date, format_name: str) -> pd.DataFrame:
    """Deck, Archetype, Top32Entries, Meta (percent of all Top 32 entries) for the window.

    Fails closed (ChallengeMetaError) on rows without EventID or still NEEDS_MANUAL_REVIEW -- the
    same rows challenge_statistics refuses to aggregate; a share computed over them would bucket a
    data gap as if it were a deck.
    """
    rows = _window_challenge_rows(history_csv, start, end, format_name)
    rows = rows[rows["_place"] <= TOP_N_PLACES]
    if rows.empty:
        raise ChallengeMetaError(f"no Challenge Top {TOP_N_PLACES} rows in {start}..{end}")
    if (rows["EventID"] == "").any():
        raise ChallengeMetaError(f"{int((rows['EventID'] == '').sum())} legacy row(s) without EventID in {start}..{end}")
    unresolved = rows["Deck"] == "NEEDS_MANUAL_REVIEW"
    if unresolved.any():
        raise ChallengeMetaError(f"{int(unresolved.sum())} NEEDS_MANUAL_REVIEW row(s) in {start}..{end}")

    total = len(rows)
    out = (
        rows.groupby("Deck")
        .agg(
            Top32Entries=("Deck", "size"),
            Archetype=("Archetype", lambda s: s.mode().iloc[0] if not s.mode().empty else "Unknown"),
        )
        .reset_index()
    )
    out["Meta"] = (out["Top32Entries"] / total * 100.0).round(2)
    return out.sort_values(["Top32Entries", "Deck"], ascending=[False, True]).reset_index(drop=True)


def rule_moved_entries(ledger_csv: Path, start: date, end: date) -> int:
    """Challenge entries in the window whose pre-rule label was not already the rule deck."""
    ledger = _load_ledger(ledger_csv)
    if ledger.empty:
        return 0
    dates = pd.to_datetime(ledger["EventDate"], errors="coerce").dt.date
    in_window = ((dates >= start) & (dates <= end)).fillna(False)
    moved = ledger[in_window & (ledger["Kind"] == "challenge") & (ledger["SubmittedDeck"] != BROODSCALE_DECK)]
    return int(len(moved))


def _videre_by_deck(run_dir: Path) -> pd.DataFrame:
    try:
        from metagame_input_generator import aggregate_by_deck
    except ModuleNotFoundError:
        from .metagame_input_generator import aggregate_by_deck

    path = run_dir / "metagame_input.csv"
    if not path.exists():
        return pd.DataFrame(columns=["Deck", "Source Deck Names", "Meta", "Winrate", "Winrate Game Count"])
    videre = aggregate_by_deck(pd.read_csv(path))
    videre["Deck"] = videre["Deck"].astype(str).str.strip()
    return videre


def build_challenge_meta_input(
    run_dir: Path,
    history_csv: Path,
    ledger_csv: Path,
    start: date,
    end: date,
    format_name: str,
    rogue_threshold: float,
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write <run_dir>/metagame_input_challenge_top32.xlsx (+ .csv twin, which challenge statistics
    read for Meta Share / Universe A) and return the xlsx path -- the drop-in replacement for
    metagame_input_grouped.xlsx as run_statistics' input."""
    try:
        from metagame_input_generator import apply_rogue_threshold_to_grouped
    except ModuleNotFoundError:
        from .metagame_input_generator import apply_rogue_threshold_to_grouped

    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    share = challenge_top32_share(history_csv, start, end, format_name)
    videre = _videre_by_deck(run_dir)
    vid: Dict[str, pd.Series] = {str(r["Deck"]): r for _, r in videre.iterrows()}

    moved = rule_moved_entries(ledger_csv, start, end)
    mixed = set(MIXED_LABEL_DECKS) if moved >= 1 else set()

    rows = []
    for r in share.itertuples(index=False):
        v = vid.get(r.Deck)
        row = {
            "Deck": r.Deck,
            "Source Deck Names": str(v["Source Deck Names"]) if v is not None else r.Deck,
            "Meta": float(r.Meta),
            "MetaSource": META_SOURCE_CHALLENGE,
            "Meta_Videre": float(v["Meta"]) if v is not None else float("nan"),
            "Top32Entries": int(r.Top32Entries),
            "Winrate": float(v["Winrate"]) if v is not None else float("nan"),
            "Winrate Game Count": float(v["Winrate Game Count"]) if v is not None else float("nan"),
            "WR_Flag": "" if v is not None else WR_FLAG_NO_VIDERE,
            "Archetype": r.Archetype,
        }
        if r.Deck in mixed:
            row.update({"Winrate": float("nan"), "Winrate Game Count": float("nan"), "WR_Flag": WR_FLAG_MIXED})
        rows.append(row)

    out = apply_rogue_threshold_to_grouped(pd.DataFrame(rows), rogue_threshold)
    out = out[OUTPUT_COLS].sort_values("Meta", ascending=False).reset_index(drop=True)

    xlsx = run_dir / CHALLENGE_META_XLSX
    out.to_excel(xlsx, index=False)
    out.to_csv(run_dir / CHALLENGE_META_CSV, index=False, encoding="utf-8-sig")

    videre_only = sorted(set(vid) - set(share["Deck"]))
    emit(
        f"[challenge-meta] {start}..{end}: {len(out)} deck(s) from {int(share['Top32Entries'].sum())} Challenge "
        f"Top {TOP_N_PLACES} entries -> {xlsx}; rule moved {moved} entry(ies)"
        + (f", winrate blanked for {sorted(mixed)}" if mixed else "")
    )
    no_wr = out.loc[out["WR_Flag"] == WR_FLAG_NO_VIDERE, "Deck"].tolist()
    if no_wr:
        emit(f"[challenge-meta] no Videre winrate for: {no_wr}")
    if videre_only:
        emit(f"[challenge-meta] Videre-only deck(s), no Challenge Top {TOP_N_PLACES} entry (not in the table): {videre_only}")
    return xlsx


def challenge_history_path_for(outputs_base: Path, format_slug: str) -> Path:
    """Weekly history of the Challenge-based Meta -- kept apart from metagame_history_<fmt>.csv
    (Videre per-name Meta) so a trend line never mixes the two sources."""
    return outputs_base / f"metagame_history_challenge_top32_{format_slug}.csv"


def _index_for_range(history_csv: Path, start: date, end: date) -> Optional[int]:
    if not history_csv.exists():
        return None
    hist = pd.read_csv(history_csv, usecols=lambda c: c in {"WeekIndex", "WeekStart", "WeekEnd"})
    if not {"WeekIndex", "WeekStart", "WeekEnd"}.issubset(hist.columns):
        return None
    hit = hist[(hist["WeekStart"].astype(str) == start.isoformat()) & (hist["WeekEnd"].astype(str) == end.isoformat())]
    idx = pd.to_numeric(hit["WeekIndex"], errors="coerce").dropna()
    return int(idx.max()) if not idx.empty else None


def week_index_for(challenge_history_csv: Path, videre_history_csv: Path, start: date, end: date) -> int:
    """Same WeekIndex numbering as the Videre history (W41 stays 41 on the x axis): the window's
    existing index in either history, else one past the highest index in either."""
    for path in (challenge_history_csv, videre_history_csv):
        idx = _index_for_range(path, start, end)
        if idx is not None:
            return idx
    highest = 0
    for path in (challenge_history_csv, videre_history_csv):
        if path.exists():
            col = pd.read_csv(path, usecols=lambda c: c == "WeekIndex")
            if "WeekIndex" in col.columns:
                highest = max(highest, int(pd.to_numeric(col["WeekIndex"], errors="coerce").max() or 0))
    return highest + 1
