"""TEMPORARY card-based Deck override for Rakdos Moonshadow, the second rule after broodscale_rule.py.

Rule (2026-10-19): an entry whose MAINDECK has >= 1 Moonshadow AND >= 1 Dark Matter Manipulator,
NO Death's Shadow and NO blue card is Deck "Rakdos Midrange", Archetype "Midrange" -- regardless of
the submitted name (Rakdos Midrange, Rakdos Aggro, BR, ...). Sideboard copies never count.
  * Death's Shadow lists (Rakdos Death's Shadow) and blue lists (W42: a Dimir list with Seasoned
    Cryomancer) keep their current name.
  * "Blue" = a maindeck card whose mtgo.com `colors` include COLOR_BLUE. mtgo.com reports every land
    as COLORLESS, so a blue dual land alone (Watery Grave) is NOT detected -- only blue spells.
  * Broodscale goes first: a list with Basking Broodscale is never Rakdos Midrange.
  * A list with only one of the two cards is not touched (W42: DragonFodder, Dark Matter
    Manipulator without Moonshadow, stays "Rakdos Aggro").

Same three places as the Broodscale rule: challenge_mtgo_source (fresh rows, via card_rule_deck),
apply_moonshadow_rule_to_history (offline pass over the persisted history, run right after the
Broodscale pass) and write_rakdos_override_log (per-window audit file). The pre-rule label of every
matched entry is frozen in outputs/rakdos_override_ledger_<format>.csv on first sighting. Only
Deck/Archetype are ever touched -- never Pilot/LoginID.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

import pandas as pd

try:
    from broodscale_rule import BROODSCALE_CARD, _norm_id, _window_rows, main_counts_from_decklist, rule_deck_for_counts
except ModuleNotFoundError:
    from .broodscale_rule import BROODSCALE_CARD, _norm_id, _window_rows, main_counts_from_decklist, rule_deck_for_counts

MOONSHADOW_CARD = "moonshadow"
DMM_CARD = "dark matter manipulator"
DEATHS_SHADOW_CARD = "death's shadow"
RAKDOS_DECK = "Rakdos Midrange"
RAKDOS_ARCHETYPE = "Midrange"
# Decks whose Videre winrate (per submitted name) is mixed by this rule in a window where it moved
# >= 1 entry -- see challenge_meta.MIXED_LABEL_DECKS.
RAKDOS_MIXED_LABEL_DECKS = (RAKDOS_DECK, "Rakdos Aggro")

LEDGER_COLS: List[str] = [
    "Kind",
    "EventID",
    "EventDate",
    "Tier",
    "Pilot",
    "LoginID",
    "Place",
    "SubmittedDeck",
    "RuleDeck",
    "MoonshadowMain",
    "DarkMatterManipulatorMain",
    "RecordedOn",
]

LOG_COLS: List[str] = [
    "Section",
    "Scope",
    "EventID",
    "EventDate",
    "Tier",
    "Pilot",
    "Place",
    "SubmittedDeck",
    "RuleDeck",
    "MoonshadowMain",
    "DarkMatterManipulatorMain",
    "ExcludedBy",
    "Entries",
    "Trophies",
]


def exclusion_reason(main_counts: Mapping[str, int], blue_main: bool) -> Optional[str]:
    """Why a list WITH both Moonshadow and Dark Matter Manipulator still isn't the rule deck, or None."""
    if int(main_counts.get(BROODSCALE_CARD, 0) or 0) >= 1:
        return "basking_broodscale"
    if int(main_counts.get(DEATHS_SHADOW_CARD, 0) or 0) >= 1:
        return "deaths_shadow"
    if blue_main:
        return "blue_card"
    return None


def _has_pair(main_counts: Mapping[str, int]) -> bool:
    return int(main_counts.get(MOONSHADOW_CARD, 0) or 0) >= 1 and int(main_counts.get(DMM_CARD, 0) or 0) >= 1


def moonshadow_rule_deck(main_counts: Mapping[str, int], blue_main: bool) -> Optional[Tuple[str, str]]:
    """(Deck, Archetype) forced by the Moonshadow rule for a maindeck, or None."""
    if _has_pair(main_counts) and exclusion_reason(main_counts, blue_main) is None:
        return RAKDOS_DECK, RAKDOS_ARCHETYPE
    return None


def card_rule_deck(main_counts: Mapping[str, int], blue_main: bool) -> Optional[Tuple[str, str]]:
    """All card rules in order: Broodscale first, then Moonshadow."""
    return rule_deck_for_counts(main_counts) or moonshadow_rule_deck(main_counts, blue_main)


def blue_main_from_decklist(decklist: dict) -> bool:
    for c in decklist.get("main_deck", []) or []:
        attrs = c.get("card_attributes") or {}
        colors = attrs.get("colors") or []
        if "COLOR_BLUE" in colors or str(attrs.get("color", "")).upper() == "BLUE":
            return True
    return False


def load_event_profiles(cache_dir: Path, event_id: str) -> Optional[Dict[str, Tuple[Dict[str, int], bool]]]:
    """loginid -> (maindeck counts, has a blue maindeck card) for one cached event, or None."""
    path = cache_dir / f"{event_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        _norm_id(d.get("loginid")): (main_counts_from_decklist(d), blue_main_from_decklist(d))
        for d in data.get("decklists", []) or []
    }


def _load_ledger(ledger_csv: Path) -> pd.DataFrame:
    if not ledger_csv.exists():
        return pd.DataFrame(columns=LEDGER_COLS)
    df = pd.read_csv(ledger_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    for col in LEDGER_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[LEDGER_COLS]


def apply_moonshadow_rule_to_history(
    history_csv: Path,
    cache_dir: Path,
    ledger_csv: Path,
    kind: str,
    pre_rule_labels: Optional[Mapping[Tuple[str, str], str]] = None,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Force Deck/Archetype on every *history_csv* row whose cached maindeck matches the rule.
    Same contract as broodscale_rule.apply_broodscale_rule_to_history (run it AFTER that one)."""
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    if not history_csv.exists():
        return pd.DataFrame(columns=LEDGER_COLS)
    hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if hist.empty or "EventID" not in hist.columns:
        return pd.DataFrame(columns=LEDGER_COLS)

    pre_rule_labels = pre_rule_labels or {}
    ledger = _load_ledger(ledger_csv)
    ledger_keys = {(r.Kind, _norm_id(r.EventID), _norm_id(r.LoginID)) for r in ledger.itertuples(index=False)}

    profiles: Dict[str, Optional[Dict[str, Tuple[Dict[str, int], bool]]]] = {}
    new_ledger: List[dict] = []
    changed = no_json = no_login = 0
    today = date.today().isoformat()
    for idx, row in hist.iterrows():
        event_id = _norm_id(row.get("EventID"))
        if not event_id:
            continue
        if event_id not in profiles:
            profiles[event_id] = load_event_profiles(cache_dir, event_id)
        if profiles[event_id] is None:
            no_json += 1
            continue
        loginid = _norm_id(row.get("LoginID"))
        prof = profiles[event_id].get(loginid)
        if prof is None:
            no_login += 1
            continue
        main, blue = prof
        forced = moonshadow_rule_deck(main, blue)
        if forced is None:
            continue
        deck, archetype = forced
        current_deck = str(row.get("Deck", ""))
        if (kind, event_id, loginid) not in ledger_keys:
            new_ledger.append({
                "Kind": kind,
                "EventID": event_id,
                "EventDate": row.get("EventDate", ""),
                "Tier": row.get("Tier", ""),
                "Pilot": row.get("Pilot", ""),
                "LoginID": loginid,
                "Place": row.get("Place", ""),
                "SubmittedDeck": pre_rule_labels.get((event_id, loginid), current_deck),
                "RuleDeck": deck,
                "MoonshadowMain": str(main.get(MOONSHADOW_CARD, 0)),
                "DarkMatterManipulatorMain": str(main.get(DMM_CARD, 0)),
                "RecordedOn": today,
            })
            ledger_keys.add((kind, event_id, loginid))
        if (current_deck, str(row.get("Archetype", ""))) != (deck, archetype):
            hist.at[idx, "Deck"] = deck
            hist.at[idx, "Archetype"] = archetype
            if "DeckGuess" in hist.columns:
                hist.at[idx, "DeckGuess"] = ""
            changed += 1

    emit(
        f"[moonshadow-rule] {history_csv.name}: {changed} row(s) relabeled to {RAKDOS_DECK!r}, "
        f"{len(new_ledger)} new ledger row(s); skipped {no_json} row(s) without cached JSON, "
        f"{no_login} row(s) without a matching loginid" + (" [DRY-RUN, nothing written]" if dry_run else "")
    )
    added = pd.DataFrame(new_ledger, columns=LEDGER_COLS)
    if dry_run:
        return added

    if changed:
        try:
            from challenge_history_engine import _clean_history_df_before_write, backup_history_file
        except ModuleNotFoundError:
            from .challenge_history_engine import _clean_history_df_before_write, backup_history_file
        backup_history_file(history_csv, log=log)
        _clean_history_df_before_write(hist).to_csv(history_csv, index=False, encoding="utf-8-sig")
    if new_ledger:
        ledger_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.concat([ledger, added], ignore_index=True).to_csv(ledger_csv, index=False, encoding="utf-8-sig")
    return added


def write_rakdos_override_log(
    out_path: Path,
    challenge_history_csv: Path,
    premier_history_csv: Optional[Path],
    cache_dir: Path,
    ledger_csv: Path,
    start: date,
    end: date,
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write rakdos_override_<window>.csv, same layout as broodscale_override_<window>.csv:
      OVERRIDE -- one row per window entry the rule applies to (SubmittedDeck = pre-rule label);
      SUMMARY  -- per Scope x pre-rule name: entries + trophies moved to the rule deck, plus TOTAL;
      EXCLUDED -- report-only: lists with both cards that the rule leaves alone (ExcludedBy =
                  deaths_shadow / blue_card / basking_broodscale), under their current name;
      CONTROL_DMM_WITHOUT_MOONSHADOW -- report-only, current name kept.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    ledger = _load_ledger(ledger_csv)
    submitted = {(r.Kind, _norm_id(r.EventID), _norm_id(r.LoginID)): r.SubmittedDeck for r in ledger.itertuples(index=False)}

    overrides: List[dict] = []
    excluded: List[dict] = []
    control_dmm: List[dict] = []
    sources = [("challenge", challenge_history_csv)]
    if premier_history_csv is not None:
        sources.append(("premier", premier_history_csv))
    for kind, path in sources:
        scope = "Premier" if kind == "premier" else "Challenge"
        profiles: Dict[str, Optional[Dict[str, Tuple[Dict[str, int], bool]]]] = {}
        for r in _window_rows(path, start, end).itertuples(index=False):
            if r.EventID not in profiles:
                profiles[r.EventID] = load_event_profiles(cache_dir, r.EventID)
            prof = (profiles[r.EventID] or {}).get(r.LoginID)
            if prof is None:
                continue
            main, blue = prof
            moon, dmm = int(main.get(MOONSHADOW_CARD, 0)), int(main.get(DMM_CARD, 0))
            base = {
                "Scope": scope, "EventID": r.EventID, "EventDate": r.EventDate,
                "Tier": "Premier" if kind == "premier" else r.Tier, "Pilot": r.Pilot, "Place": r.Place,
                "MoonshadowMain": moon, "DarkMatterManipulatorMain": dmm,
            }
            if moonshadow_rule_deck(main, blue) is not None:
                pre = submitted.get((kind, r.EventID, r.LoginID), r.Deck)
                overrides.append({**base, "Section": "OVERRIDE", "SubmittedDeck": pre, "RuleDeck": r.Deck})
            elif _has_pair(main):
                excluded.append({**base, "Section": "EXCLUDED", "SubmittedDeck": r.Deck,
                                 "ExcludedBy": exclusion_reason(main, blue)})
            elif dmm >= 1 and moon == 0:
                control_dmm.append({**base, "Section": "CONTROL_DMM_WITHOUT_MOONSHADOW", "SubmittedDeck": r.Deck})

    order = {"Challenge": 0, "Premier": 1}

    def _sort_key(d: dict):
        return (order[d["Scope"]], d["EventDate"], d["EventID"], int(d["Place"] or 0))

    for part in (overrides, excluded, control_dmm):
        part.sort(key=_sort_key)

    summary: List[dict] = []
    if overrides:
        ov = pd.DataFrame(overrides)
        ov["_trophy"] = pd.to_numeric(ov["Place"], errors="coerce").eq(1)
        for scope in ("Challenge", "Premier"):
            part = ov[ov["Scope"] == scope]
            if part.empty:
                continue
            grouped = part.groupby("SubmittedDeck").agg(Entries=("EventID", "size"), Trophies=("_trophy", "sum"))
            for name, g in grouped.sort_values(["Entries", "Trophies"], ascending=False).iterrows():
                summary.append({"Section": "SUMMARY", "Scope": scope, "SubmittedDeck": name, "RuleDeck": RAKDOS_DECK,
                                "Entries": int(g.Entries), "Trophies": int(g.Trophies)})
            summary.append({"Section": "SUMMARY", "Scope": scope, "SubmittedDeck": "TOTAL", "RuleDeck": RAKDOS_DECK,
                            "Entries": int(len(part)), "Trophies": int(part["_trophy"].sum())})
        bad = ov[ov["RuleDeck"] != RAKDOS_DECK]
        if not bad.empty:
            emit(f"[moonshadow-rule] ERROR: {len(bad)} rule entry(ies) still not {RAKDOS_DECK!r} in history "
                 "-- run apply_moonshadow_rule_to_history first")

    out = pd.DataFrame(overrides + summary + excluded + control_dmm, columns=LOG_COLS)
    for col in ("MoonshadowMain", "DarkMatterManipulatorMain", "Entries", "Trophies"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")

    for scope in ("Challenge", "Premier"):
        part = [d for d in overrides if d["Scope"] == scope]
        moved = sum(1 for d in part if d["SubmittedDeck"] != RAKDOS_DECK)
        emit(f"[moonshadow-rule] {scope}: {len(part)} rule entry(ies) in {start.isoformat()}..{end.isoformat()} "
             f"({moved} from another name)")
    for s in summary:
        emit(f"  {s['Scope']}: {s['SubmittedDeck']} -> {s['RuleDeck']}: {s['Entries']} entries, {s['Trophies']} trophies")
    emit(f"[moonshadow-rule] excluded (both cards, rule not applied): {len(excluded)} entry(ies)")
    for d in excluded:
        emit(f"  - {d['Scope']} {d['EventDate']} {d['EventID']} #{d['Place']} {d['Pilot']}: {d['SubmittedDeck']} ({d['ExcludedBy']})")
    emit(f"[moonshadow-rule] control: {len(control_dmm)} entry(ies) with Dark Matter Manipulator but no Moonshadow")
    emit(f"[moonshadow-rule] log -> {out_path}")
    return out_path


def rule_moved_entries(ledger_csv: Path, start: date, end: date) -> int:
    """Challenge entries in the window whose pre-rule label was not already the rule deck."""
    ledger = _load_ledger(ledger_csv)
    if ledger.empty:
        return 0
    dates = pd.to_datetime(ledger["EventDate"], errors="coerce").dt.date
    in_window = ((dates >= start) & (dates <= end)).fillna(False)
    return int(len(ledger[in_window & (ledger["Kind"] == "challenge") & (ledger["SubmittedDeck"] != RAKDOS_DECK)]))


def ledger_path_for(outputs_base: Path, format_slug: str) -> Path:
    return outputs_base / f"rakdos_override_ledger_{format_slug}.csv"
