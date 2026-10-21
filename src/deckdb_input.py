"""DeckDB assignment export as the only input of the metagame analysis (decision K33 in DeckDB).

Input mode "deckdb" (``--input-mode deckdb``) replaces both the MTGGoldfish ``Deck`` column and the
Videre API: deck, archetype and variant come only from the export's ``deck``/``archetype``/
``variant`` columns, the Swiss record and bye only from ``swiss_wins``/``swiss_losses``/``had_bye``
(never re-derived from MTGO ``winloss``). None of the pipeline's own label machinery runs in this
mode -- no user/challenge mapping, no deck alias, no archetype rule, no card rule (Broodscale,
Moonshadow). The legacy mode is untouched.

The export file is never guessed: its path is read from ``docs/deckdb_config.csv``
(``key,value``, key ``export_path``). The file format is documented in DeckDB ``docs/export.md``
("Format eksportu dla Analizy metagame"). The loader refuses to work (``DeckdbInputError``) when
the file has more than one ``rules_version``, or when a requested window ends after the last
event date in the file (or starts before the first one).

Metrics (window closed on both sides by ``event_date``), always at deck level; an archetype is the
aggregate of its decks' lists. Rogue and unassigned lists are one "bez talii" row in materials
(``NO_DECK_LABEL``) and are kept apart only in the working file:

  share of Top 32 Challenge     = deck lists / all lists of the window
  event presence                = distinct events with >= 1 list of the deck
  Top 8                         = lists with final_rank <= 8; conversion = Top 8 / lists, Wilson
                                  interval on lists; baseline = all Top 8 lists / all lists of the
                                  window (measured, not assumed 25%)
  trophies                      = lists with final_rank == 1; trophies / lists
  Top 8 / winner event freq.    = events with >= 1 list of the deck at final_rank <= 8 (== 1)
                                  / events of the window
  Swiss winrate                 = sum(swiss_wins) / sum(swiss_wins + swiss_losses), Wilson interval
                                  on matches (too narrow: one player brings many lists)
  players / top player share    = distinct ``player`` / max lists of one ``player`` / deck lists
  coverage                      = window lists / sum(player_count) over the window's events

``loginid`` is read only for the MTGO-cache join check and never reaches a material.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

DEFAULT_CONFIG_CSV = Path("docs") / "deckdb_config.csv"
NO_DECK_LABEL = "bez talii"
STATUS_ASSIGNED = "assigned"
STATUS_ROGUE = "rogue"
STATUS_UNASSIGNED = "unassigned"
WILSON_Z = 1.959963984540054
WILSON_NOTE = (
    "Przedział Wilsona (95%) liczony na meczach szwajcarskich jest zaniżony (za wąski): "
    "jeden gracz wnosi wiele list, więc mecze nie są niezależne."
)
CONVERSION_WILSON_NOTE = (
    "Przedział Wilsona (95%) konwersji liczony na listach jest zaniżony (za wąski): "
    "jeden gracz wnosi wiele list, więc listy nie są niezależne."
)
WINRATE_NOTE = "Winrate szwajcarski list, które awansowały do Top 32 (bez drabinki Top 8, bez bye)."
NO_DECK_WINRATE_NOTE = "Wiersz „bez talii” (Rogue + nieprzypisane) jest niejednorodny; jego winrate tylko poglądowo."

# Variant reporting threshold D2 (DeckDB docs/decisions.md, entry 35): a variant is reported IN A
# MONTH when, in that month, it has >= 5% of the deck's lists, >= 3 lists and >= 2 distinct players;
# only months with >= 20 lists of the deck count. Evaluated per calendar month, on the window's rows.
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
    "variant",
    "player",
    "final_rank",
    "swiss_wins",
    "swiss_losses",
    "had_bye",
    "swiss_rounds",
    "player_count",
)
_INT_COLUMNS = ("final_rank", "swiss_wins", "swiss_losses", "had_bye", "swiss_rounds", "player_count")


class DeckdbInputError(RuntimeError):
    """The export cannot be used as-is; nothing is computed or written."""


@dataclass
class DeckdbExport:
    path: Path
    sha256: str
    rules_version: str
    first_event_date: str
    last_event_date: str
    rows: pd.DataFrame


def _norm_id(value: object) -> str:
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def load_deckdb_config(config_csv: Path = DEFAULT_CONFIG_CSV) -> Path:
    """Returns the export path written in *config_csv* (key ``export_path``); never a default."""
    if not config_csv.exists():
        raise DeckdbInputError(f"Brak pliku konfiguracji DeckDB: {config_csv}")
    cfg = pd.read_csv(config_csv, dtype=str, keep_default_na=False)
    values = dict(zip(cfg["key"].str.strip(), cfg["value"].str.strip()))
    export_path = values.get("export_path", "")
    if not export_path:
        raise DeckdbInputError(f"{config_csv}: brak wartości export_path")
    return Path(export_path)


def load_export(path: Path) -> DeckdbExport:
    if not path.exists():
        raise DeckdbInputError(f"Plik eksportu DeckDB nie istnieje: {path}")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
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

    return DeckdbExport(
        path=path,
        sha256=sha,
        rules_version=versions[0],
        first_event_date=str(df["event_date"].min()),
        last_event_date=str(df["event_date"].max()),
        rows=df,
    )


def window_rows(export: DeckdbExport, start: date, end: date) -> pd.DataFrame:
    """Rows with start <= event_date <= end. Refuses a window the file does not fully cover."""
    if end.isoformat() > export.last_event_date:
        raise DeckdbInputError(
            f"Okno {start}..{end} kończy się po ostatniej dacie eventu w eksporcie "
            f"({export.last_event_date}, {export.path.name})"
        )
    if start.isoformat() < export.first_event_date:
        raise DeckdbInputError(
            f"Okno {start}..{end} zaczyna się przed pierwszą datą eventu w eksporcie "
            f"({export.first_event_date}, {export.path.name})"
        )
    df = export.rows
    mask = (df["event_date"] >= start.isoformat()) & (df["event_date"] <= end.isoformat())
    return df[mask].copy()


def _material_deck(rows: pd.DataFrame) -> pd.Series:
    """Deck label for materials: Rogue and unassigned become one NO_DECK_LABEL row."""
    return rows["deck"].where(rows["status"] == STATUS_ASSIGNED, NO_DECK_LABEL)


def _material_archetype(rows: pd.DataFrame) -> pd.Series:
    return rows["archetype"].where(rows["status"] == STATUS_ASSIGNED, NO_DECK_LABEL)


def wilson_interval(wins: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    if n <= 0:
        return (math.nan, math.nan)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def top8_baseline(rows: pd.DataFrame) -> float:
    """All Top 8 lists / all lists of the window, in percent (the conversion baseline)."""
    return float((rows["final_rank"] <= 8).sum()) / len(rows) * 100.0 if len(rows) else math.nan


def group_metrics(rows: pd.DataFrame, key: pd.Series, key_name: str, total_lists: int, total_events: int) -> pd.DataFrame:
    """One row per value of *key* over *rows*; every count comes straight from the export rows."""
    out: List[dict] = []
    for label, grp in rows.groupby(key, sort=False):
        lists = len(grp)
        top8 = int((grp["final_rank"] <= 8).sum())
        trophies = int((grp["final_rank"] == 1).sum())
        top8_events = int(grp.loc[grp["final_rank"] <= 8, "event_id"].nunique())
        win_events = int(grp.loc[grp["final_rank"] == 1, "event_id"].nunique())
        clo, chi = wilson_interval(top8, lists)
        wins = int(grp["swiss_wins"].sum())
        losses = int(grp["swiss_losses"].sum())
        matches = wins + losses
        lo, hi = wilson_interval(wins, matches)
        per_player = grp.groupby("player").size()
        out.append({
            key_name: label,
            "Listy": lists,
            "Udzial_Top32_pct": lists / total_lists * 100.0,
            "Eventy_z_lista": int(grp["event_id"].nunique()),
            "Top8": top8,
            "Konwersja_Top32_Top8_pct": top8 / lists * 100.0,
            "Konwersja_Wilson95_dol_pct": clo * 100.0,
            "Konwersja_Wilson95_gora_pct": chi * 100.0,
            "Eventy_z_Top8": top8_events,
            "Top8_eventy_pct": top8_events / total_events * 100.0,
            "Trofea": trophies,
            "Trofea_na_liste_pct": trophies / lists * 100.0,
            "Eventy_ze_zwyciestwem": win_events,
            "Zwyciestwa_eventy_pct": win_events / total_events * 100.0,
            "Wygrane_szw": wins,
            "Przegrane_szw": losses,
            "Mecze_szw": matches,
            "Winrate_szw_pct": (wins / matches * 100.0) if matches else math.nan,
            "Wilson95_dol_pct": lo * 100.0,
            "Wilson95_gora_pct": hi * 100.0,
            "Listy_z_bye": int(grp["had_bye"].sum()),
            "Gracze": int(per_player.size),
            "Najczestszy_gracz_udzial_pct": int(per_player.max()) / lists * 100.0,
        })
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(["Listy", key_name], ascending=[False, True]).reset_index(drop=True)


def deck_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Deck level; Rogue + unassigned as one NO_DECK_LABEL row."""
    tbl = group_metrics(rows, _material_deck(rows), "Talia", len(rows), rows["event_id"].nunique())
    arch = (
        rows.assign(_d=_material_deck(rows), _a=_material_archetype(rows))
        .drop_duplicates("_d").set_index("_d")["_a"]
    )
    tbl.insert(1, "Archetyp", tbl["Talia"].map(arch))
    return tbl


def archetype_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Archetype = pooled lists of its decks (a deck has exactly one archetype in the export)."""
    assigned = rows[rows["status"] == STATUS_ASSIGNED]
    per_deck = assigned.groupby("deck")["archetype"].nunique()
    if (per_deck > 1).any():
        raise DeckdbInputError(f"Talie z więcej niż jednym archetypem: {sorted(per_deck[per_deck > 1].index)}")
    tbl = group_metrics(rows, _material_archetype(rows), "Archetyp", len(rows), rows["event_id"].nunique())
    decks = rows.assign(_a=_material_archetype(rows), _d=_material_deck(rows)).groupby("_a")["_d"].nunique()
    tbl.insert(1, "Talie", tbl["Archetyp"].map(decks).astype(int))
    return tbl


def no_deck_split(rows: pd.DataFrame) -> pd.DataFrame:
    """Working file only: Rogue and unassigned as separate rows (shares of all window lists)."""
    sub = rows[rows["status"] != STATUS_ASSIGNED]
    if sub.empty:
        return pd.DataFrame()
    return group_metrics(sub, sub["status"], "Status", len(rows), rows["event_id"].nunique())


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
    tbl["Pokrycie_pct"] = tbl["Listy"] / tbl["Gracze_eventu"] * 100.0
    return tbl.sort_values(["Data", "EventID"]).reset_index(drop=True)


def coverage(rows: pd.DataFrame) -> dict:
    events = rows.groupby("event_id")["player_count"].first()
    participants = int(events.sum())
    return {
        "events": int(events.size),
        "lists": int(len(rows)),
        "participants": participants,
        "coverage_pct": len(rows) / participants * 100.0 if participants else math.nan,
    }


def finalists_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Place 1 and 2 per event, deck labels only (no player identifiers)."""
    deck = _material_deck(rows)
    out = []
    for eid, grp in rows.assign(_d=deck).groupby("event_id"):
        first = grp[grp["final_rank"] == 1]
        second = grp[grp["final_rank"] == 2]
        if first.empty or second.empty:
            continue
        out.append({
            "EventID": eid,
            "Data": first.iloc[0]["event_date"],
            "Talia_1": first.iloc[0]["_d"],
            "Talia_2": second.iloc[0]["_d"],
        })
    return pd.DataFrame(out, columns=["EventID", "Data", "Talia_1", "Talia_2"]).sort_values(["Data", "EventID"]).reset_index(drop=True)


def trend_windows(end: date, weeks: int, days: int = 14) -> List[tuple[date, date]]:
    """Weekly-stepped closed windows of *days* days ending at *end*, oldest first (the same grid
    as the legacy weekly runs)."""
    return [(e - timedelta(days=days - 1), e) for e in (end - timedelta(days=7 * i) for i in reversed(range(weeks)))]


def trend_table(export: DeckdbExport, end: date, weeks: int, key: str) -> pd.DataFrame:
    """Share of Top 32 Challenge per window of trend_windows(); *key* is 'Talia' or 'Archetyp'."""
    out = []
    for ws, we in trend_windows(end, weeks):
        rows = window_rows(export, ws, we)
        labels = _material_deck(rows) if key == "Talia" else _material_archetype(rows)
        counts = labels.value_counts()
        for label, n in counts.items():
            out.append({
                "Okno_od": ws.isoformat(),
                "Okno_do": we.isoformat(),
                key: label,
                "Listy": int(n),
                "Listy_okna": int(len(rows)),
                "Eventy_okna": int(rows["event_id"].nunique()),
                "Udzial_Top32_pct": n / len(rows) * 100.0,
            })
    return pd.DataFrame(out)


def _cache_file(cache_dir: Path, event_id: str) -> Optional[Path]:
    for name in (f"{event_id}.json", f"{event_id}.0.json"):
        p = cache_dir / name
        if p.exists():
            return p
    return None


def match_mtgo_cache(rows: pd.DataFrame, cache_dir: Path, start: date, end: date) -> dict:
    """Join window lists with the MTGO JSON cache by (event_id, decktournamentid), both ways.

    Cache side: every Modern Challenge (description starting "Modern Challenge", so no Showcase /
    RC events, which the export never has) dated in the window, plus any export event found in the
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
