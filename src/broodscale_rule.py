"""TEMPORARY card-based Deck override for Broodscale, until the general card classifier exists.

Rule (2026-10-12): an entry whose MAINDECK has >= 1 Basking Broodscale is Deck "Broodscale Combo",
Archetype "Combo" -- regardless of the submitted name (Eldrazi Ramp, Eldrazi, Mono-Green Eldrazi,
MTGO color codes, ...). Sideboard copies never count. The rule wins over every name-based step:
user/challenge deck mappings, deck_aliases.csv, the nearest-neighbor classifier, an earlier Review
Queue decision and the sticky "already resolved" value in the history files.

Three places apply it:
  * challenge_mtgo_source.build_challenge_dataset -- fresh rows (and the classifier's reference
    library) already carry the rule label;
  * apply_broodscale_rule_to_history -- an offline, idempotent pass over challenge/premier history
    using only the cached mtgo.com JSON. run_generation calls it right after every sync, because
    sync_challenge_history_window keeps an already-resolved Deck sticky and would otherwise put the
    old name-based label back;
  * write_broodscale_override_log -- the per-window audit file next to challenge_statistics.xlsx.

The pre-rule label of every overridden entry is frozen in a persistent ledger
(outputs/broodscale_override_ledger_<format>.csv) the first time the entry is seen, because once
the history row says "Broodscale Combo" nothing else remembers what it was called before.
Only Deck/Archetype are ever touched -- never Pilot/LoginID (see the Pilot-freezing note in
CLAUDE.md).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

import pandas as pd

BROODSCALE_CARD = "basking broodscale"
BLADE_CARD = "blade of the bloodchief"
BROODSCALE_DECK = "Broodscale Combo"
BROODSCALE_ARCHETYPE = "Combo"

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
    "BaskingBroodscaleMain",
    "BladeOfTheBloodchiefMain",
    "RecordedOn",
]

REVERSE_LEDGER_COLS: List[str] = [
    "Kind",
    "EventID",
    "EventDate",
    "Tier",
    "Pilot",
    "LoginID",
    "Place",
    "PreviousDeck",
    "NewDeck",
    "Method",
    "SubmittedName",
    "Similarity",
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
    "BaskingBroodscaleMain",
    "BladeOfTheBloodchiefMain",
    "Entries",
    "Trophies",
]


def _norm_card(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip()).lower()


def _norm_id(value: object) -> str:
    return re.sub(r"\.0$", "", str(value if value is not None else "").strip())


def rule_deck_for_counts(main_counts: Mapping[str, int]) -> Optional[Tuple[str, str]]:
    """(Deck, Archetype) forced by the rule for a maindeck card multiset, or None.

    *main_counts* is keyed by normalized (lowercase, collapsed-whitespace) card name -- the same
    shape as challenge_mtgo_source.MtgoEventDeck.signature, which is maindeck-only.
    """
    if int(main_counts.get(BROODSCALE_CARD, 0) or 0) >= 1:
        return BROODSCALE_DECK, BROODSCALE_ARCHETYPE
    return None


def main_counts_from_decklist(decklist: dict) -> Dict[str, int]:
    """Maindeck card counts (basics included, sideboard excluded) of one mtgo.com JSON decklist."""
    counts: Dict[str, int] = {}
    for c in decklist.get("main_deck", []) or []:
        name = _norm_card((c.get("card_attributes") or {}).get("card_name", ""))
        if not name:
            continue
        counts[name] = counts.get(name, 0) + int(c.get("qty", 1) or 1)
    return counts


def load_event_main_counts(cache_dir: Path, event_id: str) -> Optional[Dict[str, Dict[str, int]]]:
    """loginid -> maindeck counts for one cached event, or None when the event JSON isn't cached."""
    path = cache_dir / f"{event_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {_norm_id(d.get("loginid")): main_counts_from_decklist(d) for d in data.get("decklists", []) or []}


def _load_ledger(ledger_csv: Path) -> pd.DataFrame:
    if not ledger_csv.exists():
        return pd.DataFrame(columns=LEDGER_COLS)
    df = pd.read_csv(ledger_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    for col in LEDGER_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[LEDGER_COLS]


def apply_broodscale_rule_to_history(
    history_csv: Path,
    cache_dir: Path,
    ledger_csv: Path,
    kind: str,
    pre_rule_labels: Optional[Mapping[Tuple[str, str], str]] = None,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Force Deck/Archetype on every *history_csv* row whose cached maindeck has Basking Broodscale.

    Rows are matched to their decklist by (EventID, LoginID). Rows without an EventID, without a
    cached event JSON or without a matching loginid are left untouched and counted in the log.
    Every matched entry gets a ledger row (first sighting wins) recording its pre-rule label:
    *pre_rule_labels* -- (event_id, loginid) -> label the name-based pipeline produced, passed by
    run_generation for freshly fetched rows -- takes priority over the current history value.

    Idempotent. Returns the ledger rows added by this call. With *dry_run* nothing is written.
    """
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
    ledger_keys = {
        (r.Kind, _norm_id(r.EventID), _norm_id(r.LoginID)) for r in ledger.itertuples(index=False)
    }

    counts_cache: Dict[str, Optional[Dict[str, Dict[str, int]]]] = {}
    new_ledger: List[dict] = []
    changed = no_json = no_login = 0
    today = date.today().isoformat()
    for idx, row in hist.iterrows():
        event_id = _norm_id(row.get("EventID"))
        if not event_id:
            continue
        if event_id not in counts_cache:
            counts_cache[event_id] = load_event_main_counts(cache_dir, event_id)
        event_counts = counts_cache[event_id]
        if event_counts is None:
            no_json += 1
            continue
        loginid = _norm_id(row.get("LoginID"))
        main = event_counts.get(loginid)
        if main is None:
            no_login += 1
            continue
        forced = rule_deck_for_counts(main)
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
                "BaskingBroodscaleMain": str(main.get(BROODSCALE_CARD, 0)),
                "BladeOfTheBloodchiefMain": str(main.get(BLADE_CARD, 0)),
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
        f"[broodscale-rule] {history_csv.name}: {changed} row(s) relabeled to {BROODSCALE_DECK!r}, "
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


def _window_rows(history_csv: Path, start: date, end: date) -> pd.DataFrame:
    if not history_csv.exists():
        return pd.DataFrame()
    hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if hist.empty:
        return hist
    dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    hist = hist[((dates >= start) & (dates <= end)).fillna(False)].copy()
    hist["EventID"] = hist["EventID"].map(_norm_id)
    hist["LoginID"] = hist["LoginID"].map(_norm_id)
    return hist[hist["EventID"] != ""]


def _load_reverse_ledger(reverse_ledger_csv: Optional[Path]) -> pd.DataFrame:
    if reverse_ledger_csv is None or not reverse_ledger_csv.exists():
        return pd.DataFrame(columns=REVERSE_LEDGER_COLS)
    df = pd.read_csv(reverse_ledger_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    for col in REVERSE_LEDGER_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[REVERSE_LEDGER_COLS]


def append_reverse_ledger(reverse_ledger_csv: Path, rows: List[dict]) -> None:
    """Record entries taken OUT of the rule deck because the name alias that put them there was
    removed (2026-10-12: "Eldrazi" -> Broodscale Combo). First sighting per (Kind, EventID, LoginID)
    wins, same as the override ledger."""
    if not rows:
        return
    ledger = _load_reverse_ledger(reverse_ledger_csv)
    seen = {(r.Kind, _norm_id(r.EventID), _norm_id(r.LoginID)) for r in ledger.itertuples(index=False)}
    fresh = [r for r in rows if (r["Kind"], _norm_id(r["EventID"]), _norm_id(r["LoginID"])) not in seen]
    if not fresh:
        return
    reverse_ledger_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([ledger, pd.DataFrame(fresh, columns=REVERSE_LEDGER_COLS)], ignore_index=True).to_csv(
        reverse_ledger_csv, index=False, encoding="utf-8-sig"
    )


def write_broodscale_override_log(
    out_path: Path,
    challenge_history_csv: Path,
    premier_history_csv: Optional[Path],
    cache_dir: Path,
    ledger_csv: Path,
    start: date,
    end: date,
    log: Optional[Callable[[str], None]] = None,
    reverse_ledger_csv: Optional[Path] = None,
) -> Path:
    """Write broodscale_override_<window>.csv. Every row carries Scope (Challenge / Premier) so the
    Challenge numbers reconcile 1:1 with challenge_statistics.xlsx (which never contains Premier).
    Sections, in order:
      OVERRIDE   -- one row per window entry with Basking Broodscale in the main;
      SUMMARY    -- per Scope x pre-rule name: entries + trophies that went to the rule deck, plus a
                    TOTAL per Scope;
      TRANSITION -- per Scope, pre -> post label counts for every deck the rule or the reverse touched
                    (pre = override/reverse ledger label, else the current label), so the pre-rule
                    sheet's per-deck Appearances close exactly against the post-rule sheet;
      REVERSE_REMOVED_NAME_ALIAS -- entries taken out of the rule deck when the "Eldrazi" name alias
                    was removed (SubmittedDeck = previous label, RuleDeck = new label);
      CONTROL_BLADE_WITHOUT_BROODSCALE / CONTROL_RULEDECK_WITHOUT_BROODSCALE -- report-only.
    Everything is also echoed to *log*.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    ledger = _load_ledger(ledger_csv)
    submitted = {
        (r.Kind, _norm_id(r.EventID), _norm_id(r.LoginID)): r.SubmittedDeck for r in ledger.itertuples(index=False)
    }
    reverse = _load_reverse_ledger(reverse_ledger_csv)
    reversed_from = {
        (r.Kind, _norm_id(r.EventID), _norm_id(r.LoginID)): r.PreviousDeck for r in reverse.itertuples(index=False)
    }

    overrides: List[dict] = []
    reverse_rows: List[dict] = []
    control_blade: List[dict] = []
    control_rule: List[dict] = []
    transition_src: List[dict] = []
    sources = [("challenge", challenge_history_csv)]
    if premier_history_csv is not None:
        sources.append(("premier", premier_history_csv))
    for kind, path in sources:
        scope = "Premier" if kind == "premier" else "Challenge"
        rows = _window_rows(path, start, end)
        counts_cache: Dict[str, Optional[Dict[str, Dict[str, int]]]] = {}
        for r in rows.itertuples(index=False):
            key = (kind, r.EventID, r.LoginID)
            pre = submitted.get(key, reversed_from.get(key, r.Deck))
            transition_src.append({"Scope": scope, "pre": pre, "post": r.Deck, "trophy": str(r.Place).strip() == "1"})
            if r.EventID not in counts_cache:
                counts_cache[r.EventID] = load_event_main_counts(cache_dir, r.EventID)
            main = (counts_cache[r.EventID] or {}).get(r.LoginID)
            if main is None:
                continue
            bb = int(main.get(BROODSCALE_CARD, 0))
            blade = int(main.get(BLADE_CARD, 0))
            base = {
                "Scope": scope,
                "EventID": r.EventID,
                "EventDate": r.EventDate,
                "Tier": "Premier" if kind == "premier" else r.Tier,
                "Pilot": r.Pilot,
                "Place": r.Place,
                "BaskingBroodscaleMain": bb,
                "BladeOfTheBloodchiefMain": blade,
            }
            if bb >= 1:
                overrides.append({**base, "Section": "OVERRIDE", "SubmittedDeck": pre, "RuleDeck": r.Deck})
            elif blade >= 1:
                control_blade.append({**base, "Section": "CONTROL_BLADE_WITHOUT_BROODSCALE", "SubmittedDeck": r.Deck})
            if bb == 0 and r.Deck == BROODSCALE_DECK:
                control_rule.append({**base, "Section": "CONTROL_RULEDECK_WITHOUT_BROODSCALE", "SubmittedDeck": r.Deck})
            if key in reversed_from:
                reverse_rows.append({
                    **base, "Section": "REVERSE_REMOVED_NAME_ALIAS",
                    "SubmittedDeck": reversed_from[key], "RuleDeck": r.Deck,
                })

    order = {"Challenge": 0, "Premier": 1}

    def _sort_key(d: dict):
        return (order[d["Scope"]], d["EventDate"], d["EventID"], int(d["Place"] or 0))

    overrides.sort(key=_sort_key)
    reverse_rows.sort(key=_sort_key)

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
                summary.append({
                    "Section": "SUMMARY", "Scope": scope, "SubmittedDeck": name, "RuleDeck": BROODSCALE_DECK,
                    "Entries": int(g.Entries), "Trophies": int(g.Trophies),
                })
            summary.append({
                "Section": "SUMMARY", "Scope": scope, "SubmittedDeck": "TOTAL", "RuleDeck": BROODSCALE_DECK,
                "Entries": int(len(part)), "Trophies": int(part["_trophy"].sum()),
            })
        bad = ov[ov["RuleDeck"] != BROODSCALE_DECK]
        if not bad.empty:
            emit(f"[broodscale-rule] ERROR: {len(bad)} entry(ies) with Basking Broodscale still not "
                 f"{BROODSCALE_DECK!r} in history -- run apply_broodscale_rule_to_history first")

    transitions: List[dict] = []
    if transition_src:
        tr = pd.DataFrame(transition_src)
        changed = tr[tr["pre"] != tr["post"]]
        touched = {BROODSCALE_DECK} | set(changed["pre"]) | set(changed["post"])
        tr = tr[tr["pre"].isin(touched) | tr["post"].isin(touched)]
        grouped = tr.groupby(["Scope", "pre", "post"]).agg(Entries=("pre", "size"), Trophies=("trophy", "sum")).reset_index()
        grouped["_o"] = grouped["Scope"].map(order)
        for g in grouped.sort_values(["_o", "pre", "post"]).itertuples(index=False):
            transitions.append({
                "Section": "TRANSITION", "Scope": g.Scope, "SubmittedDeck": g.pre, "RuleDeck": g.post,
                "Entries": int(g.Entries), "Trophies": int(g.Trophies),
            })

    out = pd.DataFrame(
        overrides + summary + transitions + reverse_rows + control_blade + control_rule, columns=LOG_COLS
    )
    for col in ("BaskingBroodscaleMain", "BladeOfTheBloodchiefMain", "Entries", "Trophies"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")

    for scope in ("Challenge", "Premier"):
        part = [d for d in overrides if d["Scope"] == scope]
        moved = sum(1 for d in part if d["SubmittedDeck"] != BROODSCALE_DECK)
        emit(f"[broodscale-rule] {scope}: {len(part)} entry(ies) with Basking Broodscale in "
             f"{start.isoformat()}..{end.isoformat()} ({moved} from another name)")
    for t in transitions:
        emit(f"  {t['Scope']}: {t['SubmittedDeck']} -> {t['RuleDeck']}: {t['Entries']} entries, {t['Trophies']} trophies")
    emit(f"[broodscale-rule] reverse (removed name alias): {len(reverse_rows)} entry(ies)")
    for d in reverse_rows:
        emit(f"  - {d['EventDate']} {d['EventID']} T{d['Tier']} #{d['Place']} {d['Pilot']}: {d['SubmittedDeck']} -> {d['RuleDeck']}")
    emit(f"[broodscale-rule] control: {len(control_blade)} entry(ies) with Blade of the Bloodchief but no Basking Broodscale")
    for d in control_blade:
        emit(f"  - {d['EventDate']} {d['EventID']} T{d['Tier']} #{d['Place']} {d['Pilot']}: {d['SubmittedDeck']} (Blade x{d['BladeOfTheBloodchiefMain']})")
    emit(f"[broodscale-rule] control: {len(control_rule)} {BROODSCALE_DECK!r} entry(ies) without Basking Broodscale in the main")
    for d in control_rule:
        emit(f"  - {d['EventDate']} {d['EventID']} T{d['Tier']} #{d['Place']} {d['Pilot']} (Blade x{d['BladeOfTheBloodchiefMain']})")
    emit(f"[broodscale-rule] log -> {out_path}")
    return out_path


def ledger_path_for(outputs_base: Path, format_slug: str) -> Path:
    return outputs_base / f"broodscale_override_ledger_{format_slug}.csv"


def reverse_ledger_path_for(outputs_base: Path, format_slug: str) -> Path:
    return outputs_base / f"broodscale_reverse_alias_ledger_{format_slug}.csv"
