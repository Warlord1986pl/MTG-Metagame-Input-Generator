"""DeckDB assignment export as the only input of the metagame analysis (DeckDB decisions K33, K36).

Input mode "deckdb" (``--input-mode deckdb``) replaces both the MTGGoldfish ``Deck`` column and the
Videre API: deck, archetype and variant come only from the export's ``deck``/``archetype``/
``variant`` columns. None of the pipeline's own label machinery runs in this mode -- no
user/challenge mapping, no deck alias, no archetype rule, no card rule (Broodscale, Moonshadow).
The legacy mode is untouched. Specification: ``docs/deckdb_metodologia.md`` (accepted 2026-10-21).

Paths are never guessed: ``docs/deckdb_config.csv`` (``key,value``) holds ``export_path`` (the
assignment export, DeckDB ``docs/export.md``), ``format_events_path`` (DeckDB
``rules/format_events.yaml``, read only) and optionally ``cards_path`` (``deckdb export-cards``).
The loader refuses to work (``DeckdbInputError``) when the file has more than one
``rules_version``, or when a requested period ends after the last event date in the file (or
starts before the first one).

Three time levels (every period closed on both sides by ``event_date``):

  week (ISO, disjoint)   share, presence, Top 8 (lists, % of events)
  calendar month         the same + trophies (count) + variants by D2
  format period          conversion with a pilot-bootstrap interval, trophies, pilot measures;
                         from the MTGO effective date of the last confirmed major format event,
                         cumulative up to the as-of date

  share                  = deck lists / all lists of the period
  presence               = distinct events with >= 1 list of the deck (also as % of events)
  Top 8                  = lists with final_rank <= 8; Top 8 % of events = events with >= 1 Top 8
                           list of the deck / events of the period
  conversion             = Top 8 lists / lists; common baseline = all Top 8 lists / all lists of
                           the period; interval = bootstrap over pilots, only with >= 10 pilots
  pilot measures (decks) = pilots in Top 32 / Top 8; the most frequent pilot (most Top 32 lists of
                           the deck, ties: more Top 8 lists, then the player id) -- share of Top 32
                           lists and of Top 8 entries, own conversion and conversion without them
  coverage               = lists / sum(player_count) over the period's events

Rogue and unassigned lists are one "bez talii" row in materials (``NO_DECK_LABEL``), apart only in
the working split. Winrate is not a measure (K36); the Swiss columns are not read. Pilots are the
export's ``player`` only; ``loginid`` is read only for the MTGO-cache join check and never reaches
a material.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

DEFAULT_CONFIG_CSV = Path("docs") / "deckdb_config.csv"
NO_DECK_LABEL = "bez talii"
STATUS_ASSIGNED = "assigned"
STATUS_ROGUE = "rogue"
STATUS_UNASSIGNED = "unassigned"

BOOT_MIN_PILOTS = 10
# pilot labels (order of 2026-10-21 e): "mało pilotów" = < BOOT_MIN_PILOTS pilots, no interval;
# "koncentracja" = the most frequent pilot has >= 1/3 of the deck's lists or >= 1/2 of its Top 8 entries
LABEL_FEW_PILOTS = "mało pilotów"
LABEL_CONCENTRATION = "koncentracja"
CONCENTRATION_LISTS = 1 / 3
CONCENTRATION_TOP8 = 1 / 2
BOOT_DRAWS = 2000
BOOT_SEED = 20261021

# Variant reporting threshold D2 (DeckDB docs/decisions.md, entry 35): a variant is reported IN A
# MONTH when, in that month, it has >= 5% of the deck's lists, >= 3 lists and >= 2 distinct players;
# only months with >= 20 lists of the deck count. Evaluated per calendar month, on the period's rows.
# D2 counts players by loginid; `player` is a 1:1 pseudonym of it, so the distinct count is the same.
VARIANT_MIN_SHARE = 0.05
VARIANT_MIN_LISTS = 3
VARIANT_MIN_PLAYERS = 2
VARIANT_MIN_DECK_LISTS_PER_MONTH = 20

REQUIRED_COLUMNS = (
    "event_id",
    "decktournamentid",
    "loginid",
    "event_date",
    "status",
    "deck",
    "archetype",
    "rules_version",
    "name_match_version",
    "variant",
    "player",
    "final_rank",
    "player_count",
)
_INT_COLUMNS = ("final_rank", "player_count")


class DeckdbInputError(RuntimeError):
    """The input cannot be used as-is; nothing is computed or written."""


@dataclass
class DeckdbConfig:
    export_path: Path
    format_events_path: Optional[Path]
    cards_path: Optional[Path]


@dataclass
class DeckdbExport:
    path: Path
    sha256: str
    rules_version: str
    name_match_version: str
    first_event_date: str
    last_event_date: str
    rows: pd.DataFrame


def _norm_id(value: object) -> str:
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_deckdb_config(config_csv: Path = DEFAULT_CONFIG_CSV) -> DeckdbConfig:
    """Paths written in *config_csv*; ``export_path`` is required, nothing has a default."""
    if not config_csv.exists():
        raise DeckdbInputError(f"Brak pliku konfiguracji DeckDB: {config_csv}")
    cfg = pd.read_csv(config_csv, dtype=str, keep_default_na=False)
    values = dict(zip(cfg["key"].str.strip(), cfg["value"].str.strip()))
    if not values.get("export_path"):
        raise DeckdbInputError(f"{config_csv}: brak wartości export_path")

    def _opt(key: str) -> Optional[Path]:
        return Path(values[key]) if values.get(key) else None

    return DeckdbConfig(Path(values["export_path"]), _opt("format_events_path"), _opt("cards_path"))


def load_export(path: Path) -> DeckdbExport:
    if not path.exists():
        raise DeckdbInputError(f"Plik eksportu DeckDB nie istnieje: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DeckdbInputError(f"{path}: brak kolumn {missing}")
    if df.empty:
        raise DeckdbInputError(f"{path}: plik bez wierszy")

    versions = sorted(set(df["rules_version"].str.strip()))
    if len(versions) != 1:
        raise DeckdbInputError(f"{path}: więcej niż jedna wartość rules_version: {versions}")

    df["event_id"] = df["event_id"].map(_norm_id)
    df["decktournamentid"] = df["decktournamentid"].map(_norm_id)
    df["event_date"] = df["event_date"].str.strip()
    for col in _INT_COLUMNS:
        num = pd.to_numeric(df[col], errors="coerce")
        if num.isna().any():
            raise DeckdbInputError(f"{path}: kolumna {col} ma {int(num.isna().sum())} pustych/nieliczbowych wartości")
        df[col] = num.astype(int)
    bad_status = sorted(set(df["status"]) - {STATUS_ASSIGNED, STATUS_ROGUE, STATUS_UNASSIGNED})
    if bad_status:
        raise DeckdbInputError(f"{path}: nieznany status {bad_status}")
    dup = df.duplicated(["event_id", "decktournamentid"]).sum()
    if dup:
        raise DeckdbInputError(f"{path}: {dup} powtórzonych kluczy (event_id, decktournamentid)")
    df["material_deck"] = df["deck"].where(df["status"] == STATUS_ASSIGNED, NO_DECK_LABEL)
    df["material_archetype"] = df["archetype"].where(df["status"] == STATUS_ASSIGNED, NO_DECK_LABEL)
    df["top8"] = (df["final_rank"] <= 8).astype(int)
    df["trophy"] = (df["final_rank"] == 1).astype(int)

    return DeckdbExport(
        path=path,
        sha256=sha256_of(path),
        rules_version=versions[0],
        name_match_version=",".join(sorted(set(df["name_match_version"]))),
        first_event_date=str(df["event_date"].min()),
        last_event_date=str(df["event_date"].max()),
        rows=df,
    )


def window_rows(export: DeckdbExport, start: date, end: date) -> pd.DataFrame:
    """Rows with start <= event_date <= end. Refuses a period the file does not fully cover."""
    if end.isoformat() > export.last_event_date:
        raise DeckdbInputError(
            f"Okres {start}..{end} kończy się po ostatniej dacie eventu w eksporcie "
            f"({export.last_event_date}, {export.path.name})"
        )
    if start.isoformat() < export.first_event_date:
        raise DeckdbInputError(
            f"Okres {start}..{end} zaczyna się przed pierwszą datą eventu w eksporcie "
            f"({export.first_event_date}, {export.path.name})"
        )
    df = export.rows
    mask = (df["event_date"] >= start.isoformat()) & (df["event_date"] <= end.isoformat())
    return df[mask].copy()


# --- time levels -------------------------------------------------------------------------------

def iso_week_label(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def complete_iso_weeks(as_of: date, n: int) -> List[tuple[date, date]]:
    """The last *n* ISO weeks (Monday..Sunday) ending on or before *as_of*, oldest first."""
    last_sunday = as_of - timedelta(days=(as_of.weekday() + 1) % 7)
    return [(last_sunday - timedelta(days=7 * i + 6), last_sunday - timedelta(days=7 * i)) for i in reversed(range(n))]


def month_period(as_of: date) -> tuple[date, date, bool]:
    """(first day of as_of's month, min(last day, as_of), complete?)."""
    first = as_of.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first, min(last, as_of), as_of >= last


def previous_month(first_day: date) -> tuple[date, date]:
    last = first_day - timedelta(days=1)
    return last.replace(day=1), last


def format_period_start(format_events_path: Optional[Path], as_of: date) -> tuple[date, str]:
    """``effective_mtgo`` of the latest confirmed ``major`` event on or before *as_of*, and its key."""
    if format_events_path is None or not format_events_path.exists():
        raise DeckdbInputError(f"Brak pliku zdarzeń formatu (format_events_path): {format_events_path}")
    import yaml

    data = yaml.safe_load(format_events_path.read_text(encoding="utf-8")) or {}
    best: Optional[tuple[date, str]] = None
    for ev in data.get("events") or []:
        if not (ev.get("major") and ev.get("status") == "confirmed" and ev.get("effective_mtgo")):
            continue
        day = date.fromisoformat(str(ev["effective_mtgo"]))
        if day <= as_of and (best is None or day > best[0]):
            best = (day, str(ev.get("key", "")))
    if best is None:
        raise DeckdbInputError(f"{format_events_path}: brak potwierdzonego dużego zdarzenia formatu przed {as_of}")
    return best


# --- measures ----------------------------------------------------------------------------------

def coverage(rows: pd.DataFrame) -> dict:
    events = rows.groupby("event_id")["player_count"].first()
    participants = int(events.sum())
    return {
        "events": int(events.size),
        "lists": int(len(rows)),
        "participants": participants,
        "coverage_pct": len(rows) / participants * 100.0 if participants else math.nan,
    }


def top8_baseline(rows: pd.DataFrame) -> float:
    """All Top 8 lists / all lists of the period, in percent (the common conversion baseline)."""
    return float(rows["top8"].sum()) / len(rows) * 100.0 if len(rows) else math.nan


def share_table(rows: pd.DataFrame, key: str, key_name: str, trophies: bool) -> pd.DataFrame:
    """Week/month level: share, presence, Top 8 (lists, % of events); trophies for the month."""
    n_lists, n_events = len(rows), rows["event_id"].nunique()
    out: List[dict] = []
    for label, grp in rows.groupby(key, sort=False):
        events = int(grp["event_id"].nunique())
        top8_events = int(grp.loc[grp["top8"] == 1, "event_id"].nunique())
        rec = {
            key_name: label,
            "Listy": len(grp),
            "Udzial_Top32_pct": len(grp) / n_lists * 100.0,
            "Eventy_z_lista": events,
            "Obecnosc_pct_eventow": events / n_events * 100.0,
            "Top8": int(grp["top8"].sum()),
            "Eventy_z_Top8": top8_events,
            "Top8_pct_eventow": top8_events / n_events * 100.0,
        }
        if trophies:
            rec["Trofea"] = int(grp["trophy"].sum())
        out.append(rec)
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(["Listy", key_name], ascending=[False, True]).reset_index(drop=True)


def pilot_bootstrap(per_pilot: pd.DataFrame, draws: int, rng: np.random.Generator) -> tuple[float, float]:
    """Percentile 95% interval of conversion (%) drawing pilots with replacement, all lists of each."""
    n_l = per_pilot["lists"].to_numpy()
    n_t = per_pilot["top8"].to_numpy()
    idx = rng.integers(0, len(n_l), size=(draws, len(n_l)))
    conv = n_t[idx].sum(axis=1) / n_l[idx].sum(axis=1) * 100.0
    lo, hi = np.percentile(conv, [2.5, 97.5])
    return float(lo), float(hi)


def conversion_table(rows: pd.DataFrame, key: str, key_name: str, pilots: bool,
                     draws: int = BOOT_DRAWS, seed: int = BOOT_SEED) -> pd.DataFrame:
    """Format-period level. *pilots*: add pilot measures (decks only, never for archetypes or the
    'bez talii' row). The bootstrap interval is given only with >= BOOT_MIN_PILOTS pilots."""
    rng = np.random.default_rng(seed)
    out: List[dict] = []
    for label, grp in sorted(rows.groupby(key), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        per = grp.groupby("player").agg(lists=("top8", "size"), top8=("top8", "sum")).reset_index()
        n_pilots = len(per)
        per = per.sort_values(["lists", "top8", "player"], ascending=[False, False, True])
        lead = per.iloc[0]
        lists, top8 = len(grp), int(grp["top8"].sum())
        rec = {
            key_name: label,
            "Listy": lists,
            "Top8": top8,
            "Konwersja_pct": top8 / lists * 100.0,
            "Przedzial_dol_pct": math.nan,
            "Przedzial_gora_pct": math.nan,
            "Przedzial_info": "",
            "Trofea": int(grp["trophy"].sum()),
            "Piloci_Top32": n_pilots,
        }
        labels = []
        if n_pilots >= BOOT_MIN_PILOTS:
            rec["Przedzial_dol_pct"], rec["Przedzial_gora_pct"] = pilot_bootstrap(per, draws, rng)
            rec["Przedzial_info"] = f"bootstrap po pilotach, {n_pilots} pilotów"
        else:
            rec["Przedzial_info"] = f"bez przedziału: {n_pilots} pilotów (< {BOOT_MIN_PILOTS})"
            labels.append(LABEL_FEW_PILOTS)
        if pilots and label != NO_DECK_LABEL and (
                lead["lists"] / lists >= CONCENTRATION_LISTS or (top8 and lead["top8"] / top8 >= CONCENTRATION_TOP8)):
            labels.append(LABEL_CONCENTRATION)
        rec["Etykieta_pilotow"] = ", ".join(labels)
        if pilots and label != NO_DECK_LABEL:
            rest = grp[grp["player"] != lead["player"]]
            rec.update({
                "Piloci_Top8": int(grp.loc[grp["top8"] == 1, "player"].nunique()),
                "Najcz_pilot_listy": int(lead["lists"]),
                "Najcz_pilot_udzial_Top32_pct": lead["lists"] / lists * 100.0,
                "Najcz_pilot_Top8": int(lead["top8"]),
                "Najcz_pilot_udzial_Top8_pct": (lead["top8"] / top8 * 100.0) if top8 else math.nan,
                "Najcz_pilot_konwersja_pct": lead["top8"] / lead["lists"] * 100.0,
                "Bez_najcz_pilota_listy": len(rest),
                "Bez_najcz_pilota_konwersja_pct": rest["top8"].mean() * 100.0 if len(rest) else math.nan,
                "Remis_najcz_pilota": int((per["lists"] == lead["lists"]).sum() > 1),
            })
        out.append(rec)
    return pd.DataFrame(out)


def archetype_decks(rows: pd.DataFrame) -> pd.Series:
    assigned = rows[rows["status"] == STATUS_ASSIGNED]
    per_deck = assigned.groupby("deck")["archetype"].nunique()
    if (per_deck > 1).any():
        raise DeckdbInputError(f"Talie z więcej niż jednym archetypem: {sorted(per_deck[per_deck > 1].index)}")
    return rows.groupby("material_archetype")["material_deck"].nunique()


def deck_archetype_map(rows: pd.DataFrame) -> pd.Series:
    return rows.drop_duplicates("material_deck").set_index("material_deck")["material_archetype"]


def no_deck_split(rows: pd.DataFrame, trophies: bool) -> pd.DataFrame:
    """Working split: Rogue and unassigned as separate rows (shares of all lists of the period)."""
    sub = rows[rows["status"] != STATUS_ASSIGNED]
    if sub.empty:
        return pd.DataFrame()
    tbl = share_table(rows.assign(_s=rows["status"].where(rows["status"] != STATUS_ASSIGNED, "")), "_s", "Status",
                      trophies)
    return tbl[tbl["Status"] != ""].reset_index(drop=True)


def variant_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Variants reported under D2, one row per (month, deck, variant). Deck metrics are not affected."""
    assigned = rows[rows["status"] == STATUS_ASSIGNED]
    out: List[dict] = []
    for (month, deck), grp in assigned.groupby([assigned["event_date"].str[:7], "deck"]):
        deck_lists = len(grp)
        if deck_lists < VARIANT_MIN_DECK_LISTS_PER_MONTH:
            continue
        for variant, vgrp in grp[grp["variant"] != ""].groupby("variant"):
            n = len(vgrp)
            players = int(vgrp["player"].nunique())
            if n / deck_lists >= VARIANT_MIN_SHARE and n >= VARIANT_MIN_LISTS and players >= VARIANT_MIN_PLAYERS:
                out.append({
                    "Miesiac": month,
                    "Talia": deck,
                    "Wariant": variant,
                    "Listy_wariantu": n,
                    "Listy_talii_w_miesiacu": deck_lists,
                    "Udzial_w_talii_pct": n / deck_lists * 100.0,
                    "Gracze_wariantu": players,
                })
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(
        ["Miesiac", "Listy_talii_w_miesiacu", "Talia", "Listy_wariantu"], ascending=[True, False, True, False]
    ).reset_index(drop=True)


def event_table(rows: pd.DataFrame) -> pd.DataFrame:
    grp = rows.groupby("event_id")
    tbl = pd.DataFrame({
        "EventID": grp["event_id"].first(),
        "Data": grp["event_date"].first(),
        "Gracze_eventu": grp["player_count"].first(),
        "Listy": grp.size(),
        "Bez_talii": grp["status"].apply(lambda s: int((s != STATUS_ASSIGNED).sum())),
    }).reset_index(drop=True)
    tbl["Tydzien_ISO"] = tbl["Data"].map(lambda d: iso_week_label(date.fromisoformat(d)))
    tbl["Miesiac"] = tbl["Data"].str[:7]
    tbl["Pokrycie_pct"] = tbl["Listy"] / tbl["Gracze_eventu"] * 100.0
    return tbl.sort_values(["Data", "EventID"]).reset_index(drop=True)


def finalists_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Place 1 and 2 per event, deck labels only (no player identifiers)."""
    out = []
    for eid, grp in rows.groupby("event_id"):
        first = grp[grp["final_rank"] == 1]
        second = grp[grp["final_rank"] == 2]
        if first.empty or second.empty:
            continue
        out.append({
            "EventID": eid,
            "Data": first.iloc[0]["event_date"],
            "Talia_1": first.iloc[0]["material_deck"],
            "Talia_2": second.iloc[0]["material_deck"],
        })
    return pd.DataFrame(out, columns=["EventID", "Data", "Talia_1", "Talia_2"]).sort_values(
        ["Data", "EventID"]).reset_index(drop=True)


def weekly_trend(export: DeckdbExport, weeks: List[tuple[date, date]], key: str, key_name: str) -> pd.DataFrame:
    """Share of Top 32 Challenge per disjoint ISO week."""
    out = []
    for ws, we in weeks:
        rows = window_rows(export, ws, we)
        for label, n in rows[key].value_counts().items():
            out.append({
                "Tydzien_ISO": iso_week_label(ws),
                "Od": ws.isoformat(),
                "Do": we.isoformat(),
                key_name: label,
                "Listy": int(n),
                "Listy_tygodnia": int(len(rows)),
                "Eventy_tygodnia": int(rows["event_id"].nunique()),
                "Udzial_Top32_pct": n / len(rows) * 100.0,
            })
    return pd.DataFrame(out)


# --- MTGO cache join check ---------------------------------------------------------------------

def match_mtgo_cache(rows: pd.DataFrame, cache_dir: Path, start: date, end: date) -> dict:
    """Join period lists with the MTGO JSON cache by (event_id, decktournamentid), both ways.

    Cache side: every Modern Challenge (description starting "Modern Challenge", so no Showcase /
    RC events, which the export never has) dated in the period, plus any export event found in the
    cache regardless of its description.
    """
    export_keys = set(zip(rows["event_id"], rows["decktournamentid"]))
    export_events = set(rows["event_id"])
    cache_keys: set = set()
    cache_events: set = set()
    if cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            eid = _norm_id(data.get("event_id", f.stem))
            day = str(data.get("starttime", ""))[:10]
            is_challenge = str(data.get("description", "")).startswith("Modern Challenge")
            if not (eid in export_events or (is_challenge and start.isoformat() <= day <= end.isoformat())):
                continue
            cache_events.add(eid)
            for dl in data.get("decklists", []) or []:
                cache_keys.add((eid, _norm_id(dl.get("decktournamentid", ""))))
    return {
        "export_lists": len(export_keys),
        "cache_lists": len(cache_keys),
        "matched": len(export_keys & cache_keys),
        "export_not_in_cache": len(export_keys - cache_keys),
        "cache_not_in_export": len(cache_keys - export_keys),
        "export_events_not_in_cache": len(export_events - cache_events),
        "cache_events_not_in_export": len(cache_events - export_events),
    }
