"""DeckDB assignment export as the only input of the metagame analysis (DeckDB decisions K33, K36).

Input mode "deckdb" (``--input-mode deckdb``) replaces both the MTGGoldfish ``Deck`` column and the
Videre API: deck, archetype and variant come only from the export's ``deck``/``archetype``/
``variant`` columns. None of the pipeline's own label machinery runs in this mode -- no
user/challenge mapping, no deck alias, no archetype rule, no card rule (Broodscale, Moonshadow).
The legacy mode is untouched. Specification: ``docs/deckdb_metodologia.md`` (accepted 2026-10-21).

Paths are never guessed: ``docs/deckdb_config.csv`` (``key,value``) holds ``export_path`` (the
assignment export, DeckDB ``docs/export.md``), ``format_events_path`` (DeckDB
``rules/format_events.yaml``, read only) and optionally ``cards_path`` (``deckdb export-cards``).
The loader refuses to work (``DeckdbInputError``) when the file has no ``data_version`` column or
more than one (or an empty) ``data_version``, more than one ``rules_version``, or when a requested period ends after the last event date in the file (or
starts before the first one).

Three time levels (every period closed on both sides by ``event_date``):

  week (ISO, disjoint)   K41: share of the top 10 decks + "other decks" + "Rogue & unclassified" and of every
                         archetype, change vs the previous week (event bootstrap), presence, pilots,
                         Top 8 per event; no result measure of decks
  calendar month         share, presence, Top 8 (lists, % of events), trophies (count), pilot
                         measures, variants by D2, results concentration
  format period          mean place percentile (main, K41) and conversion (auxiliary) with a
                         pilot-bootstrap interval, trophies, pilot measures; from the MTGO effective
                         date of the last confirmed major format event, cumulative up to the as-of date

  share                  = deck lists / all lists of the period
  presence               = distinct events with >= 1 list of the deck (also as % of events)
  Top 8                  = lists with final_rank <= 8; Top 8 % of events = events with >= 1 Top 8
                           list of the deck / events of the period
  place percentile      = per list 100 x (1 - (final_rank - 1) / (L - 1)), L = published lists of
                           the event; per deck the mean; baseline = the mean over all lists (50 when
                           every event has ranks 1..L)
  conversion             = Top 8 lists / lists; common baseline = all Top 8 lists / all lists of
                           the period; interval (both measures, same draws) = bootstrap over pilots,
                           only with >= 10 pilots
  change vs prev. week   = share - previous week's share (pp); interval = events drawn with
                           replacement in each week independently, 2000 draws, seed 20261026
  pilot measures (decks) = pilots in Top 32 / Top 8; the most frequent pilot (most Top 32 lists of
                           the deck, ties: more Top 8 lists, then the player id) -- share of Top 32
                           lists and of Top 8 entries, own conversion and conversion without them
  coverage               = lists / sum(player_count) over the period's events

Rogue and unassigned lists are one "Rogue & unclassified" row in materials (``NO_DECK_LABEL``), apart only in
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
NO_DECK_LABEL = "Rogue & unclassified"
STATUS_ASSIGNED = "assigned"
STATUS_ROGUE = "rogue"
STATUS_UNASSIGNED = "unassigned"

BOOT_MIN_PILOTS = 10
# pilot labels (order of 2026-10-21 e): "low pilot count" = < BOOT_MIN_PILOTS pilots, no interval;
# "concentration" = the most frequent pilot has >= 1/3 of the deck's lists or >= 1/2 of its Top 8 entries
LABEL_FEW_PILOTS = "low pilot count"
LABEL_CONCENTRATION = "concentration"
CONCENTRATION_LISTS = 1 / 3
CONCENTRATION_TOP8 = 1 / 2
BOOT_DRAWS = 2000
BOOT_SEED = 20261021

# weekly materials (DeckDB K41, order of 2026-10-26 a): the top WEEK_TOP_N decks by share, the rest pooled
# into OTHER_DECKS_LABEL; the change against the previous week is emphasised only when the 95% interval
# of the difference (events drawn with replacement in both weeks independently) excludes zero
OTHER_DECKS_LABEL = "other decks"
WEEK_TOP_N = 10
CHANGE_DRAWS = 2000
CHANGE_SEED = 20261026
# results concentration: share of the best pilots (at least the Top 8 count of the pilot at place
# ceil(TOP_PILOTS_FRAC x pilots), every tied pilot included) in lists, Top 8 entries and trophies
TOP_PILOTS_FRAC = 0.10

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
    "data_version",  # required always (DeckDB c4958cb, 2026-10-21 d): one DB state per file
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
    data_version: str
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
    data_versions = sorted(set(df["data_version"].str.strip()))
    if len(data_versions) != 1 or not data_versions[0]:
        raise DeckdbInputError(f"{path}: data_version musi mieć jedną niepustą wartość, jest {data_versions}")

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
    df["place_percentile"] = place_percentile(df)

    return DeckdbExport(
        path=path,
        sha256=sha256_of(path),
        rules_version=versions[0],
        name_match_version=",".join(sorted(set(df["name_match_version"]))),
        data_version=data_versions[0],
        first_event_date=str(df["event_date"].min()),
        last_event_date=str(df["event_date"].max()),
        rows=df,
    )


TRIAGE_STATUSES = ("complete", "pending_notice", "pending_blocking", "pending")  # "pending": files before K43 supplement


@dataclass
class TriageState:
    path: Path
    status: str
    pending_groups: int
    share_of_week: float = 0.0  # waiting groups' lists / lists of the week (DeckDB weekly_triage.py)

    @property
    def blocking(self) -> bool:
        return self.status in ("pending_blocking", "pending")

    @property
    def note(self) -> str:
        """Marker on every material (chart footers, STATUS) -- only when a blocking triage was forced with
        --allow-pending-triage; empty otherwise."""
        return f"triage pending: {self.pending_groups} list group(s) awaiting decision" if self.blocking else ""

    @property
    def notice(self) -> str:
        """pending_notice (DeckDB K43 supplement): status.md and speaker notes only, never on a chart."""
        if self.status != "pending_notice":
            return ""
        return (f"{self.pending_groups} list group(s) awaiting review, {self.share_of_week * 100:.1f}% of this week's "
                f"lists, shown as {NO_DECK_LABEL}")


def load_triage(export: DeckdbExport, allow_pending: bool = False) -> TriageState:
    """DeckDB K43 (with the supplement of the order of 2026-10-26 k): triage_<data_version>.json next to the export.
    complete and pending_notice run (pending_notice: TriageState.notice in status.md / speaker notes only);
    pending_blocking (and the older "pending") is refused unless *allow_pending* (--allow-pending-triage), and every
    material then carries TriageState.note."""
    path = export.path.parent / f"triage_{export.data_version}.json"
    if not path.exists():
        raise DeckdbInputError(f"Brak pliku triage'u DeckDB dla data_version eksportu: {path} (scripts/weekly_triage.py, K43)")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("data_version") != export.data_version:
        raise DeckdbInputError(f"{path}: data_version {data.get('data_version')!r} różny od eksportu {export.data_version!r}")
    state = TriageState(path, str(data.get("status")), int(data.get("pending_groups") or 0),
                        float(data.get("waiting_share_of_week") or 0.0))
    if state.status not in TRIAGE_STATUSES:
        raise DeckdbInputError(f"{path}: nieznany status triage'u {state.status!r}")
    if state.blocking and not allow_pending:
        groups = ", ".join(f"{g.get('item_key')} ({g.get('lists_in_week')} list w tygodniu, czeka {g.get('days_waiting')} dni)"
                           for g in data.get("waiting") or []) or "brak listy grup w pliku"
        raise DeckdbInputError(f"Triage DeckDB ma status {state.status} ({state.pending_groups} grup czeka na decyzję: "
                               f"{groups}; {path}); materiały powstają po decyzjach (K43) albo z --allow-pending-triage")
    return state


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

def place_percentile(df: pd.DataFrame) -> pd.Series:
    """Per list p = 1 - (final_rank - 1) / (L - 1) in percent, L = published lists of its event (DeckDB
    K41). Undefined (NaN) for every list of an event whose ranks are not exactly 1..L or with L = 1."""
    lists = df.groupby("event_id")["final_rank"].transform("size")
    ranks = df.groupby("event_id")["final_rank"]
    contiguous = (ranks.transform("min") == 1) & (ranks.transform("max") == lists) & (
        ranks.transform("nunique") == lists)
    p = (1.0 - (df["final_rank"] - 1) / (lists - 1).where(lists > 1)) * 100.0
    return p.where(contiguous)


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
            "Lists": len(grp),
            "Share_Top32_pct": len(grp) / n_lists * 100.0,
            "Events_with_list": events,
            "Event_presence_pct": events / n_events * 100.0,
            "Top8_lists": int(grp["top8"].sum()),
            "Events_with_Top8": top8_events,
            "Top8_event_pct": top8_events / n_events * 100.0,
        }
        if trophies:
            rec["Trophies"] = int(grp["trophy"].sum())
        out.append(rec)
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(["Lists", key_name], ascending=[False, True]).reset_index(drop=True)


def _ratio_interval(num: np.ndarray, den: np.ndarray, idx: np.ndarray) -> tuple[float, float]:
    """Percentile 95% interval of sum(num) / sum(den) (%) over the pilot draws *idx* (draws x pilots)."""
    with np.errstate(invalid="ignore", divide="ignore"):  # NaN only where no list of a draw has a value
        vals = num[idx].sum(axis=1) / den[idx].sum(axis=1) * 100.0
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def pilot_bootstrap(per_pilot: pd.DataFrame, draws: int, rng: np.random.Generator) -> tuple[float, float]:
    """Percentile 95% interval of conversion (%) drawing pilots with replacement, all lists of each."""
    n_l = per_pilot["lists"].to_numpy()
    idx = rng.integers(0, len(n_l), size=(draws, len(n_l)))
    return _ratio_interval(per_pilot["top8"].to_numpy(), n_l, idx)


def percentile_baseline(rows: pd.DataFrame) -> float:
    """Mean place percentile of all lists of the period (the common percentile baseline)."""
    return float(rows["place_percentile"].mean())


def _lead_pilot(grp: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Per-pilot lists / Top 8 / percentile sums of *grp*, sorted so that row 0 is the most frequent
    pilot (most Top 32 lists, ties: more Top 8 lists, then the player id)."""
    per = grp.groupby("player").agg(lists=("top8", "size"), top8=("top8", "sum"),
                                    p_sum=("place_percentile", "sum"),
                                    p_lists=("place_percentile", "count")).reset_index()
    per = per.sort_values(["lists", "top8", "player"], ascending=[False, False, True])
    return per, per.iloc[0]


def _pilot_labels(n_pilots: int, lead: pd.Series, lists: int, top8: int, concentration: bool) -> List[str]:
    labels = [LABEL_FEW_PILOTS] if n_pilots < BOOT_MIN_PILOTS else []
    if concentration and (lead["lists"] / lists >= CONCENTRATION_LISTS
                          or (top8 and lead["top8"] / top8 >= CONCENTRATION_TOP8)):
        labels.append(LABEL_CONCENTRATION)
    return labels


def pilot_summary(rows: pd.DataFrame, key: str, key_name: str) -> pd.DataFrame:
    """Month level, decks: pilots, the most frequent pilot's share of Top 32 lists and Top 8 entries, pilot
    labels (no result measure). The 'Rogue & unclassified' row has no pilot measures."""
    out: List[dict] = []
    for label, grp in rows[rows[key] != NO_DECK_LABEL].groupby(key):
        per, lead = _lead_pilot(grp)
        lists, top8 = len(grp), int(grp["top8"].sum())
        out.append({
            key_name: label,
            "Pilots": len(per),
            "Top_pilot_share_Top32_pct": lead["lists"] / lists * 100.0,
            "Top_pilot_share_Top8_pct": (lead["top8"] / top8 * 100.0) if top8 else math.nan,
            "Pilot_labels": ", ".join(_pilot_labels(len(per), lead, lists, top8, True)),
        })
    return pd.DataFrame(out, columns=[key_name, "Pilots", "Top_pilot_share_Top32_pct", "Top_pilot_share_Top8_pct",
                                      "Pilot_labels"])


def conversion_table(rows: pd.DataFrame, key: str, key_name: str, pilots: bool,
                     draws: int = BOOT_DRAWS, seed: int = BOOT_SEED) -> pd.DataFrame:
    """Format-period level: mean place percentile (main, K41) and Top 8 conversion (auxiliary, K36), both
    with a pilot-bootstrap interval on the same draws, given only with >= BOOT_MIN_PILOTS pilots; the
    *_CI_excludes_baseline flags compare each interval with the period's common baseline. *pilots*: add
    pilot measures (decks only, never for archetypes or the 'Rogue & unclassified' row)."""
    rng = np.random.default_rng(seed)
    conv_base, pct_base = top8_baseline(rows), percentile_baseline(rows)
    out: List[dict] = []
    for label, grp in sorted(rows.groupby(key), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        per, lead = _lead_pilot(grp)
        n_pilots = len(per)
        lists, top8 = len(grp), int(grp["top8"].sum())
        rec = {
            key_name: label,
            "Lists": lists,
            "Mean_place_percentile": float(grp["place_percentile"].mean()),
            "Percentile_CI_low": math.nan,
            "Percentile_CI_high": math.nan,
            "Percentile_CI_excludes_baseline": 0,
            "Top8_lists": top8,
            "Top8_conversion_pct": top8 / lists * 100.0,
            "CI_low_pct": math.nan,
            "CI_high_pct": math.nan,
            "Conversion_CI_excludes_baseline": 0,
            "CI_info": "",
            "Trophies": int(grp["trophy"].sum()),
            "Pilots_Top32": n_pilots,
        }
        if n_pilots >= BOOT_MIN_PILOTS:
            # the same draw as before the percentile was added, so the conversion interval is unchanged
            idx = rng.integers(0, n_pilots, size=(draws, n_pilots))
            rec["CI_low_pct"], rec["CI_high_pct"] = _ratio_interval(per["top8"].to_numpy(), per["lists"].to_numpy(),
                                                                    idx)
            lo, hi = _ratio_interval(per["p_sum"].to_numpy() / 100.0, per["p_lists"].to_numpy(), idx)
            rec["Percentile_CI_low"], rec["Percentile_CI_high"] = lo, hi
            rec["Percentile_CI_excludes_baseline"] = int(lo > pct_base or hi < pct_base)
            rec["Conversion_CI_excludes_baseline"] = int(rec["CI_low_pct"] > conv_base or rec["CI_high_pct"] < conv_base)
            rec["CI_info"] = f"pilot bootstrap, {n_pilots} pilots"
        else:
            rec["CI_info"] = f"no interval: {n_pilots} pilots (< {BOOT_MIN_PILOTS})"
        labels = _pilot_labels(n_pilots, lead, lists, top8, pilots and label != NO_DECK_LABEL)
        rec["Pilot_labels"] = ", ".join(labels)
        if pilots and label != NO_DECK_LABEL:
            rest = grp[grp["player"] != lead["player"]]
            rec.update({
                "Pilots_Top8": int(grp.loc[grp["top8"] == 1, "player"].nunique()),
                "Top_pilot_lists": int(lead["lists"]),
                "Top_pilot_share_Top32_pct": lead["lists"] / lists * 100.0,
                "Top_pilot_Top8_lists": int(lead["top8"]),
                "Top_pilot_share_Top8_pct": (lead["top8"] / top8 * 100.0) if top8 else math.nan,
                "Top_pilot_conversion_pct": lead["top8"] / lead["lists"] * 100.0,
                "Without_top_pilot_lists": len(rest),
                "Without_top_pilot_conversion_pct": rest["top8"].mean() * 100.0 if len(rest) else math.nan,
                "Top_pilot_tie": int((per["lists"] == lead["lists"]).sum() > 1),
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
                    "Month": month,
                    "Deck": deck,
                    "Variant": variant,
                    "Variant_lists": n,
                    "Deck_lists_in_month": deck_lists,
                    "Share_of_deck_pct": n / deck_lists * 100.0,
                    "Variant_pilots": players,
                })
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(
        ["Month", "Deck_lists_in_month", "Deck", "Variant_lists"], ascending=[True, False, True, False]
    ).reset_index(drop=True)


def event_table(rows: pd.DataFrame) -> pd.DataFrame:
    grp = rows.groupby("event_id")
    tbl = pd.DataFrame({
        "EventID": grp["event_id"].first(),
        "Date": grp["event_date"].first(),
        "Event_players": grp["player_count"].first(),
        "Lists": grp.size(),
        "Rogue_unclassified_lists": grp["status"].apply(lambda s: int((s != STATUS_ASSIGNED).sum())),
    }).reset_index(drop=True)
    tbl["ISO_week"] = tbl["Date"].map(lambda d: iso_week_label(date.fromisoformat(d)))
    tbl["Month"] = tbl["Date"].str[:7]
    tbl["Coverage_pct"] = tbl["Lists"] / tbl["Event_players"] * 100.0
    return tbl.sort_values(["Date", "EventID"]).reset_index(drop=True)


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
            "Date": first.iloc[0]["event_date"],
            "Deck_1st": first.iloc[0]["material_deck"],
            "Deck_2nd": second.iloc[0]["material_deck"],
        })
    return pd.DataFrame(out, columns=["EventID", "Date", "Deck_1st", "Deck_2nd"]).sort_values(
        ["Date", "EventID"]).reset_index(drop=True)


def weekly_trend(export: DeckdbExport, weeks: List[tuple[date, date]], key: str, key_name: str) -> pd.DataFrame:
    """Share of Top 32 Challenge per disjoint ISO week."""
    out = []
    for ws, we in weeks:
        rows = window_rows(export, ws, we)
        for label, n in rows[key].value_counts().items():
            out.append({
                "ISO_week": iso_week_label(ws),
                "From": ws.isoformat(),
                "To": we.isoformat(),
                key_name: label,
                "Lists": int(n),
                "Week_lists": int(len(rows)),
                "Week_events": int(rows["event_id"].nunique()),
                "Share_Top32_pct": n / len(rows) * 100.0,
            })
    return pd.DataFrame(out)


def _event_share_draws(rows: pd.DataFrame, labels: List[str], draws: int, rng: np.random.Generator) -> np.ndarray:
    """draws x labels shares (%) of the week's lists, drawing its events with replacement."""
    events = sorted(rows["event_id"].unique())
    counts = pd.crosstab(rows["event_id"], rows["_group"]).reindex(index=events, columns=labels, fill_value=0)
    m = counts.to_numpy(dtype=float)
    idx = rng.integers(0, len(events), size=(draws, len(events)))
    return m[idx].sum(axis=1) / m.sum(axis=1)[idx].sum(axis=1)[:, None] * 100.0


def week_share_table(rows: pd.DataFrame, prev_rows: pd.DataFrame, key: str, key_name: str,
                     top_n: Optional[int] = None, draws: int = CHANGE_DRAWS, seed: int = CHANGE_SEED) -> pd.DataFrame:
    """Week level (K41). Rows: the *top_n* labels by lists of the week (ties by name; all labels when None),
    one pooled OTHER_DECKS_LABEL row for the rest (only with *top_n*) and the NO_DECK_LABEL row last. Share,
    presence (events with >= 1 list / events of the week), distinct pilots, and the change of share against
    *prev_rows* (the previous week, pooled by the same rows) with a 95% interval: events drawn with
    replacement in each week independently, the same draws for every row."""
    named = rows.loc[rows[key] != NO_DECK_LABEL, key].value_counts()
    named = named.sort_index().sort_values(ascending=False, kind="stable")
    keep = list(named.index if top_n is None else named.index[:top_n])

    def _grouped(r: pd.DataFrame) -> pd.DataFrame:
        pooled = r[key].isin(keep) | (r[key] == NO_DECK_LABEL)
        return r.assign(_group=r[key].where(pooled, OTHER_DECKS_LABEL))

    cur, prev = _grouped(rows), _grouped(prev_rows)
    present = set(cur["_group"]) | set(prev["_group"])
    labels = keep + [lab for lab in (OTHER_DECKS_LABEL, NO_DECK_LABEL) if lab in present]
    rng = np.random.default_rng(seed)
    diff = _event_share_draws(cur, labels, draws, rng) - _event_share_draws(prev, labels, draws, rng)
    lo, hi = np.percentile(diff, [2.5, 97.5], axis=0)
    n_lists, n_events, p_lists = len(cur), cur["event_id"].nunique(), len(prev)
    out: List[dict] = []
    for j, lab in enumerate(labels):
        g, p = cur[cur["_group"] == lab], prev[prev["_group"] == lab]
        share, prev_share = len(g) / n_lists * 100.0, len(p) / p_lists * 100.0
        out.append({
            key_name: lab,
            "Labels_in_row": int(g[key].nunique()),
            "Lists": len(g),
            "Share_Top32_pct": share,
            "Events_with_list": int(g["event_id"].nunique()),
            "Week_events": int(n_events),
            "Event_presence_pct": g["event_id"].nunique() / n_events * 100.0,
            "Pilots": int(g["player"].nunique()),
            "Prev_week_lists": len(p),
            "Prev_week_share_pct": prev_share,
            "Change_pp": share - prev_share,
            "Change_CI_low_pp": float(lo[j]),
            "Change_CI_high_pp": float(hi[j]),
            "Change_CI_excludes_0": int(lo[j] > 0 or hi[j] < 0),
        })
    return pd.DataFrame(out)


def top8_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Week level: the eight Top 8 lists of every event with deck and variant, the winner flagged; no player
    identifiers, no rates."""
    t = rows[rows["top8"] == 1].sort_values(["event_date", "event_id", "final_rank"])
    return pd.DataFrame({
        "EventID": t["event_id"],
        "Date": t["event_date"],
        "Event_players": t["player_count"],
        "Place": t["final_rank"],
        "Deck": t["material_deck"],
        "Variant": t["variant"].where(t["status"] == STATUS_ASSIGNED, ""),
        "Archetype": t["material_archetype"],
        "Winner": (t["final_rank"] == 1).astype(int),
    }).reset_index(drop=True)


def results_concentration(rows: pd.DataFrame, frac: float = TOP_PILOTS_FRAC) -> dict:
    """Share of the best pilots of the period in its lists, Top 8 entries and trophies. The threshold is the Top 8 count
    of the pilot at place ceil(frac x pilots) when pilots are ranked by Top 8 entries; the group is every pilot with at
    least that many Top 8 entries (all pilots tied at the threshold are in, no tie-break by id or at random), so its
    actual share of pilots may exceed *frac* and is reported (supplement to the orders of 2026-10-26, point 2). A
    threshold of 0 is raised to 1 (a pilot without a Top 8 entry is never in the group)."""
    per = rows.groupby("player").agg(lists=("top8", "size"), top8=("top8", "sum"), trophies=("trophy", "sum"))
    per = per.reset_index().sort_values("top8", ascending=False)
    k = max(1, math.ceil(frac * len(per)))
    cut = max(1, int(per["top8"].iloc[k - 1]))
    top = per[per["top8"] >= cut]
    totals = {c: int(per[c].sum()) for c in ("lists", "top8", "trophies")}
    share = len(top) / len(per) * 100.0 if len(per) else math.nan
    return {
        "Pilots": len(per),
        "Target_fraction_pct": frac * 100.0,
        "Top_pilots": len(top),
        "Top_pilots_pct": share,
        "Min_Top8_in_group": cut,
        "Group": f"top {share:.0f}% of pilots (all with at least {cut} Top 8 entr{'y' if cut == 1 else 'ies'})",
        "Lists": totals["lists"],
        "Top8_entries": totals["top8"],
        "Trophies": totals["trophies"],
        "Top_pilots_share_lists_pct": top["lists"].sum() / totals["lists"] * 100.0,
        "Top_pilots_share_Top8_pct": (top["top8"].sum() / totals["top8"] * 100.0) if totals["top8"] else math.nan,
        "Top_pilots_share_trophies_pct": (top["trophies"].sum() / totals["trophies"] * 100.0)
        if totals["trophies"] else math.nan,
        "Pilots_at_threshold": int((per["top8"] == cut).sum()),
    }


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
