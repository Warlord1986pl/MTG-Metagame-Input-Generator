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
from typing import Dict, List, Optional

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


def _ratio_draws(num: np.ndarray, den: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """sum(num) / sum(den) (%) of every pilot draw of *idx* (draws x pilots)."""
    with np.errstate(invalid="ignore", divide="ignore"):  # NaN only where no list of a draw has a value
        return num[idx].sum(axis=1) / den[idx].sum(axis=1) * 100.0


def _ratio_interval(num: np.ndarray, den: np.ndarray, idx: np.ndarray) -> tuple[float, float]:
    """Percentile 95% interval of sum(num) / sum(den) (%) over the pilot draws *idx* (draws x pilots)."""
    lo, hi = np.percentile(_ratio_draws(num, den, idx), [2.5, 97.5])
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
    boot: Dict[str, Dict[str, np.ndarray]] = {}  # label -> measure -> the deck's bootstrap draws (BH, below)
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
            if label != NO_DECK_LABEL:
                boot[label] = {"Conversion": _ratio_draws(per["top8"].to_numpy(), per["lists"].to_numpy(), idx),
                               "Percentile": _ratio_draws(per["p_sum"].to_numpy() / 100.0,
                                                          per["p_lists"].to_numpy(), idx)}
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
        rest = rows[rows[key] != label]
        rec["Percentile_baseline_rest"] = float(rest["place_percentile"].mean()) if len(rest) else math.nan
        rec["Conversion_baseline_rest"] = float(rest["top8"].sum()) / len(rest) * 100.0 if len(rest) else math.nan
        out.append(rec)
    return results_bh(pd.DataFrame(out), key_name, boot)


RESULT_MEASURES = ("Percentile", "Conversion")
RESULT_BH_COLUMNS = ("baseline_rest", "p", "q_BH", "BH_rejects", "BH_threshold_T", "band_low", "band_high")


def results_bh(tbl: pd.DataFrame, key_name: str, boot: Dict[str, Dict[str, np.ndarray]],
               q: Optional[float] = None) -> pd.DataFrame:
    """DeckDB K41 (change of 2026-10-28): per measure, for every label with an interval (>= BOOT_MIN_PILOTS pilots,
    not 'Rogue & unclassified') the two-sided p of the deck against the field without it (baseline_rest), from the same
    pilot draws as the 95% interval: p = 2 x min(P(est* <= b), P(est* >= b)), at least 1 / draws; Benjamini-Hochberg
    q = *q* separately in the family of the percentile and of the conversion; BH_threshold_T = q k / m. The band
    (band_low, band_high) is the interval at level 1 - T read from the same draws: the order statistics x_(j+1) and
    x_(N-j), j = floor(N T / 2), so that baseline_rest lies strictly outside it exactly when p <= T, i.e. when BH
    rejects; a disagreement stops the run (DeckdbInputError), as does a draw without a value for a deck whose measure
    is defined. Rows without a test (no interval, the measure undefined for every list of the deck, or no other list
    in the period): p, q, T and the band empty, BH_rejects 0."""
    q = BH_Q if q is None else q  # defined below with the weekly change test
    out = tbl.copy()
    for meas in RESULT_MEASURES:
        for col in RESULT_BH_COLUMNS[1:]:
            out[f"{meas}_{col}"] = 0 if col == "BH_rejects" else math.nan
        # no test when the measure is undefined for the deck (every draw NaN) or there is no rest of the field
        fam = [lab for i, lab in enumerate(out[key_name]) if lab in boot and not np.isnan(boot[lab][meas]).all()
               and not math.isnan(out.at[i, f"{meas}_baseline_rest"])]
        if not fam:
            continue
        rows_of = {lab: i for i, lab in enumerate(out[key_name])}
        base = np.array([out.at[rows_of[lab], f"{meas}_baseline_rest"] for lab in fam])
        vals = [boot[lab][meas] for lab in fam]
        if any(np.isnan(v).any() for v in vals):
            raise DeckdbInputError(f"{meas}: losowanie bootstrapu bez wartości (NaN) dla "
                                   f"{[lab for lab, v in zip(fam, vals) if np.isnan(v).any()]}")
        n = len(vals[0])
        p = np.array([max(2.0 * min(float((v <= b).mean()), float((v >= b).mean())), 1.0 / n)
                      for v, b in zip(vals, base)])
        p = np.minimum(p, 1.0)
        qv, rej, t = bh(p, q)
        if t < 1.0 / n:
            raise DeckdbInputError(f"{meas}: próg BH T = {t} mniejszy niż 1/{n}; pas na poziomie 1 - T nieokreślony "
                                   "przy tej liczbie losowań")
        j = int(math.floor(n * t / 2.0 + 1e-9))
        for i, (lab, v, b) in enumerate(zip(fam, vals, base)):
            srt = np.sort(v)
            lo, hi = float(srt[j]), float(srt[n - 1 - j])
            r = rows_of[lab]
            out.loc[r, [f"{meas}_p", f"{meas}_q_BH", f"{meas}_BH_threshold_T", f"{meas}_band_low",
                        f"{meas}_band_high"]] = [p[i], qv[i], t, lo, hi]
            out.loc[r, f"{meas}_BH_rejects"] = int(rej[i])
            if bool(b < lo or b > hi) != bool(rej[i]):
                raise DeckdbInputError(f"{meas} {lab}: pas 1 - T ({lo:.4f}..{hi:.4f}) a baseline_rest {b:.4f} "
                                       f"niezgodne z odrzuceniem BH ({bool(rej[i])}, p {p[i]}, T {t})")
    return out


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


TOP_TIE_RULE = ("the ten decks with the most Top 32 lists of the week; a tie on lists is broken by the deck name in "
                "alphabetical order (A before Z), not by presence or results")


def ranked_labels(rows: pd.DataFrame, key: str) -> List[str]:
    """Labels of *key* (without NO_DECK_LABEL) by lists of *rows* descending, a tie by the label in alphabetical order
    (TOP_TIE_RULE; the glossary term "top 10 (week)" states the same rule)."""
    counts = rows.loc[rows[key] != NO_DECK_LABEL, key].value_counts()
    return sorted(counts.index, key=lambda lab: (-int(counts[lab]), str(lab)))


def week_share_table(rows: pd.DataFrame, prev_rows: pd.DataFrame, key: str, key_name: str,
                     top_n: Optional[int] = None, draws: int = CHANGE_DRAWS, seed: int = CHANGE_SEED) -> pd.DataFrame:
    """Week level (K41). Rows: the *top_n* labels by lists of the week (ties by name; all labels when None),
    one pooled OTHER_DECKS_LABEL row for the rest (only with *top_n*) and the NO_DECK_LABEL row last. Share,
    presence (events with >= 1 list / events of the week), distinct pilots, and the change of share against
    *prev_rows* (the previous week, pooled by the same rows) with a 95% interval: events drawn with
    replacement in each week independently, the same draws for every row."""
    keep = ranked_labels(rows, key)
    keep = keep if top_n is None else keep[:top_n]

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


# --- full weekly / monthly metrics, tracked decks and the BH-corrected change test (order before 2026-W44, part 2) --

BH_Q = 0.10
BH_EPS = 1e-12  # float tolerance of the BH comparisons (p lie on the 1 / draws grid)
TRACK_MIN_SHARE_PCT = 1.0  # largest_in_archetype: share >= 1% in the week or the previous week
TRACK_TOP10_WEEK, TRACK_TOP10_PREV, TRACK_LARGEST = "top10_week", "top10_prev", "largest_in_archetype"
TRACK_ARCHETYPE = "archetype"  # every archetype (and the Rogue & unclassified row) with a list in w or w-1
LEVEL_DECK, LEVEL_ARCHETYPE = "deck", "archetype"


def bootstrap_p(diff: np.ndarray) -> np.ndarray:
    """Two-sided p of every column of *diff* (draws x labels) against 0: 2 x min(P(d <= 0), P(d >= 0)) over the
    bootstrap distribution, at least 1 / draws, at most 1."""
    n = diff.shape[0]
    p = 2.0 * np.minimum((diff <= 0).mean(axis=0), (diff >= 0).mean(axis=0))
    return np.clip(p, 1.0 / n, 1.0)


def bh(p: np.ndarray, q: float = BH_Q) -> tuple[np.ndarray, np.ndarray, float]:
    """Benjamini-Hochberg over one family: (adjusted q-values, rejections, threshold T = q x k / m). k = the largest
    rank with p_(k) <= k q / m; a p is rejected exactly when p <= T. With no rejection T = q / m (the level the
    smallest p would have to reach)."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    if m == 0:
        return p, p.astype(bool), math.nan
    order = np.argsort(p, kind="mergesort")
    ranked = p[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    qv = np.empty(m)
    qv[order] = np.minimum(adj, 1.0)
    rejects = qv <= q + BH_EPS
    k = int(rejects.sum())
    t = q * max(k, 1) / m
    if not (np.all(p[rejects] <= t + BH_EPS) and np.all(p[~rejects] > t - BH_EPS)):
        raise DeckdbInputError(f"BH: odrzucenia (k = {k}) niezgodne z progiem T = {t}")
    return qv, rejects, t


def week_periods(period_start: date, last_week_end: date) -> List[tuple[date, date]]:
    """Every ISO week of the format period up to the Sunday *last_week_end*, the first one clipped to *period_start*."""
    if last_week_end.weekday() != 6:
        raise DeckdbInputError(f"koniec ostatniego tygodnia {last_week_end} nie jest niedzielą")
    out, ws = [], period_start - timedelta(days=period_start.weekday())
    while ws <= last_week_end:
        out.append((max(ws, period_start), ws + timedelta(days=6)))
        ws += timedelta(days=7)
    return out


def tracked_decks(cur: pd.DataFrame, prev: pd.DataFrame, arch: Dict[str, str]) -> Dict[str, str]:
    """Tracked decks of week w -> reason: the top 10 of w, the top 10 of w-1, and for every archetype without a deck in
    those two top 10s its largest deck by lists in w (ties alphabetical) when that deck has a share >= 1% in w or w-1.
    One reason per deck, in this order."""
    top_w = ranked_labels(cur, "material_deck")[:WEEK_TOP_N]
    top_p = ranked_labels(prev, "material_deck")[:WEEK_TOP_N]
    out = {d: TRACK_TOP10_WEEK for d in top_w}
    out.update({d: TRACK_TOP10_PREV for d in top_p if d not in out})
    covered = {arch[d] for d in out}
    n_cur, n_prev = cur["material_deck"].value_counts(), prev["material_deck"].value_counts()
    for a in sorted({x for x in arch.values() if x != NO_DECK_LABEL} - covered):
        decks = sorted((d for d, x in arch.items() if x == a), key=lambda d: (-int(n_cur.get(d, 0)), d))
        d = decks[0]
        if (n_cur.get(d, 0) / len(cur) * 100.0 >= TRACK_MIN_SHARE_PCT
                or n_prev.get(d, 0) / len(prev) * 100.0 >= TRACK_MIN_SHARE_PCT):
            out[d] = TRACK_LARGEST
    return out


WEEKLY_METRIC_COLUMNS = ["iso_week", "week_from", "week_to", "level", "name", "archetype", "lists", "share_pct",
                         "events_with_list", "week_events", "top8", "top8_events", "trophies", "pilots",
                         "top_pilot_share_pct", "concentrated", "prev_week_lists", "prev_share_pct", "change_pp",
                         "tracked", "tracked_reason", "change_ci_low_pp", "change_ci_high_pp", "change_p",
                         "change_q_bh", "change_beyond"]


def _label_counts(rows: pd.DataFrame, key: str, labels: List[str], top_pilot: bool) -> pd.DataFrame:
    g = rows.groupby(key)
    t = pd.DataFrame({
        "lists": g.size(), "events_with_list": g["event_id"].nunique(), "top8": g["top8"].sum(),
        "top8_events": rows[rows["top8"] == 1].groupby(key)["event_id"].nunique(), "trophies": g["trophy"].sum(),
        "pilots": g["player"].nunique(),
    }).reindex(labels).fillna(0).astype(int)
    if top_pilot:
        lead = g["player"].agg(lambda s: int(s.value_counts().iloc[0])).reindex(labels)
        t["top_pilot_share_pct"] = (lead / t["lists"].where(t["lists"] > 0) * 100.0)
        t.loc[t.index == NO_DECK_LABEL, "top_pilot_share_pct"] = math.nan  # not a deck
        t["concentrated"] = (t["top_pilot_share_pct"] >= CONCENTRATION_LISTS * 100.0).astype("Int64").where(
            t["top_pilot_share_pct"].notna())
    else:
        t["top_pilot_share_pct"], t["concentrated"] = math.nan, pd.NA
    return t


def weekly_metrics(export: DeckdbExport, period_start: date, last_week_end: date,
                   draws: int = CHANGE_DRAWS, seed: int = CHANGE_SEED) -> pd.DataFrame:
    """Long table, recomputed from the export on every call: every ISO week of the format period x every deck (with
    'Rogue & unclassified') and, apart, every archetype (with 'Rogue & unclassified') seen in the period; a label
    absent in a week has 0 lists and share 0. The change test runs only for the tracked decks (tracked_decks) and for
    the archetypes with a list in w or w-1: event bootstrap as week_share_table (the same seed per week, the current
    week drawn first), 95% interval, two-sided p (bootstrap_p), BH q = 0.10 separately in the week's family of tracked
    decks and of archetypes; change_beyond = BH rejection. The first week of the period has no previous week in it:
    no change, nothing tracked."""
    weeks = week_periods(period_start, last_week_end)
    span = window_rows(export, weeks[0][0], weeks[-1][1])
    arch = deck_archetype_map(span).to_dict()
    universe = {LEVEL_DECK: ("material_deck", sorted(span["material_deck"].unique())),
                LEVEL_ARCHETYPE: ("material_archetype", sorted(span["material_archetype"].unique()))}
    out: List[pd.DataFrame] = []
    prev_rows: Optional[pd.DataFrame] = None
    for ws, we in weeks:
        rows = window_rows(export, ws, we)
        if rows.empty:
            raise DeckdbInputError(f"tydzień {iso_week_label(ws)} ({ws}..{we}) bez list: udział nieokreślony")
        for level, (key, labels) in universe.items():
            t = _label_counts(rows, key, labels, top_pilot=level == LEVEL_DECK)
            t["share_pct"] = t["lists"] / len(rows) * 100.0
            t["week_events"] = int(rows["event_id"].nunique())
            t["archetype"] = [arch.get(x, x) for x in labels] if level == LEVEL_DECK else labels
            for col in ("prev_week_lists", "prev_share_pct", "change_pp", "change_ci_low_pp", "change_ci_high_pp",
                        "change_p", "change_q_bh"):
                t[col] = math.nan
            t["tracked"], t["tracked_reason"], t["change_beyond"] = 0, "", pd.NA
            if prev_rows is not None:
                n_prev = prev_rows[key].value_counts().reindex(labels).fillna(0).astype(int)
                t["prev_week_lists"] = n_prev
                t["prev_share_pct"] = n_prev / len(prev_rows) * 100.0
                t["change_pp"] = t["share_pct"] - t["prev_share_pct"]
                if level == LEVEL_DECK:
                    reasons = tracked_decks(rows, prev_rows, arch)
                else:
                    reasons = {x: TRACK_ARCHETYPE for x in labels if t.at[x, "lists"] or n_prev[x]}
                fam = [x for x in labels if x in reasons]
                rng = np.random.default_rng(seed)
                diff = (_event_share_draws(rows.assign(_group=rows[key]), labels, draws, rng)
                        - _event_share_draws(prev_rows.assign(_group=prev_rows[key]), labels, draws, rng))
                lo, hi = np.percentile(diff, [2.5, 97.5], axis=0)
                p = bootstrap_p(diff)
                j = [labels.index(x) for x in fam]
                qv, rej, _t = bh(p[j]) if fam else (np.array([]), np.array([], dtype=bool), math.nan)
                t.loc[fam, "tracked"] = 1
                t.loc[fam, "tracked_reason"] = [reasons[x] for x in fam]
                t.loc[fam, "change_ci_low_pp"], t.loc[fam, "change_ci_high_pp"] = lo[j], hi[j]
                t.loc[fam, "change_p"], t.loc[fam, "change_q_bh"] = p[j], qv
                t.loc[fam, "change_beyond"] = rej.astype(int)
            t = t.rename_axis("name").reset_index()
            t.insert(0, "level", level)
            t.insert(0, "week_to", we.isoformat())
            t.insert(0, "week_from", ws.isoformat())
            t.insert(0, "iso_week", iso_week_label(ws))
            out.append(t)
        prev_rows = rows
    wm = pd.concat(out, ignore_index=True)[WEEKLY_METRIC_COLUMNS]
    wm["change_beyond"] = wm["change_beyond"].astype("Int64")
    wm["concentrated"] = wm["concentrated"].astype("Int64")
    check_weekly_metrics(export, wm)
    return wm


def check_weekly_metrics(export: DeckdbExport, wm: pd.DataFrame) -> None:
    """Per week and level: the lists sum to the week's lists; per row trophies <= top8 <= lists and events_with_list
    <= week_events. DeckdbInputError otherwise (no fallback)."""
    for (week, level), g in wm.groupby(["iso_week", "level"]):
        n = len(window_rows(export, date.fromisoformat(g["week_from"].iloc[0]), date.fromisoformat(g["week_to"].iloc[0])))
        if int(g["lists"].sum()) != n:
            raise DeckdbInputError(f"weekly_metrics {week} {level}: suma list {int(g['lists'].sum())} != {n}")
    bad = wm[(wm["trophies"] > wm["top8"]) | (wm["top8"] > wm["lists"]) | (wm["events_with_list"] > wm["week_events"])]
    if not bad.empty:
        raise DeckdbInputError(f"weekly_metrics: monotoniczność naruszona w {len(bad)} wierszach, np. "
                               f"{bad.iloc[0][['iso_week', 'level', 'name']].tolist()}")


MONTHLY_METRIC_COLUMNS = ["month", "month_from", "month_to", "complete_month", "level", "name", "archetype", "lists",
                          "share_pct", "events_with_list", "month_events", "top8", "trophies", "pilots",
                          "top_pilot_share_pct"]


def monthly_metrics(export: DeckdbExport, period_start: date, end: date) -> pd.DataFrame:
    """Calendar month x every deck / archetype of the format period (both with 'Rogue & unclassified'), recomputed
    from the export: lists, share, events, Top 8, trophies, pilots, top pilot share (decks only). The first month is
    clipped to *period_start*, the last one to *end* (complete_month = 0 when it ends before the month does)."""
    span = window_rows(export, period_start, end)
    arch = deck_archetype_map(span).to_dict()
    out: List[pd.DataFrame] = []
    ms = period_start.replace(day=1)
    while ms <= end:
        full_end = (ms + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        a, b = max(ms, period_start), min(full_end, end)
        rows = window_rows(export, a, b)
        for level, key in ((LEVEL_DECK, "material_deck"), (LEVEL_ARCHETYPE, "material_archetype")):
            labels = sorted(span[key].unique())
            t = _label_counts(rows, key, labels, top_pilot=level == LEVEL_DECK).drop(columns=["top8_events",
                                                                                              "concentrated"])
            t["share_pct"] = t["lists"] / len(rows) * 100.0
            t["month_events"] = int(rows["event_id"].nunique())
            t["archetype"] = [arch.get(x, x) for x in labels] if level == LEVEL_DECK else labels
            t = t.rename_axis("name").reset_index()
            t.insert(0, "level", level)
            t.insert(0, "complete_month", int(b == full_end))
            t.insert(0, "month_to", b.isoformat())
            t.insert(0, "month_from", a.isoformat())
            t.insert(0, "month", f"{ms:%Y-%m}")
            if int(t["lists"].sum()) != len(rows):
                raise DeckdbInputError(f"monthly_metrics {ms:%Y-%m} {level}: suma list != {len(rows)}")
            out.append(t)
        ms = full_end + timedelta(days=1)
    return pd.concat(out, ignore_index=True)[MONTHLY_METRIC_COLUMNS]


def attach_change_beyond(tbl: pd.DataFrame, wm: pd.DataFrame, week: str, level: str, key_name: str) -> pd.DataFrame:
    """The sheet *tbl* (week_share_table) with the column change_beyond (BH, weekly_metrics) right after
    Change_CI_excludes_0. Every named row must equal its weekly_metrics row in lists, share, events, pilots, change
    and interval, otherwise DeckdbInputError. The 'Rogue & unclassified' row takes the test of the archetype family
    (the same quantity; decks are tested only when tracked); the pooled 'other decks' row has no test."""
    w = wm[wm["iso_week"] == week]
    own = w[w["level"] == level].set_index("name")
    rogue = w[w["level"] == LEVEL_ARCHETYPE].set_index("name")
    pairs = [("Lists", "lists"), ("Share_Top32_pct", "share_pct"), ("Events_with_list", "events_with_list"),
             ("Pilots", "pilots"), ("Change_pp", "change_pp")]
    beyond = []
    for _, r in tbl.iterrows():
        name = r[key_name]
        if name == OTHER_DECKS_LABEL:
            beyond.append(pd.NA)
            continue
        m = rogue.loc[name] if name == NO_DECK_LABEL else own.loc[name]
        diffs = [c for c, mc in pairs if not math.isclose(float(r[c]), float(m[mc]), rel_tol=0, abs_tol=1e-9)]
        if m["tracked"] == 1:
            diffs += [c for c, mc in (("Change_CI_low_pp", "change_ci_low_pp"), ("Change_CI_high_pp", "change_ci_high_pp"))
                      if not math.isclose(float(r[c]), float(m[mc]), rel_tol=0, abs_tol=1e-9)]
        if diffs:
            raise DeckdbInputError(f"{week} {name}: arkusz tygodnia różny od weekly_metrics w {diffs}")
        beyond.append(m["change_beyond"])
    out = tbl.copy()
    out.insert(out.columns.get_loc("Change_CI_excludes_0") + 1, "change_beyond", pd.array(beyond, dtype="Int64"))
    return out


NO_VARIANT_LABEL = "(no variant)"


def variant_week_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Week level: per variant of every deck with variants (an export variant on any of its lists of the window):
    Top 32 lists, Top 8 lists, trophies; a list of such a deck without a variant counts as NO_VARIANT_LABEL. The
    variant sums must equal the deck's own counts, otherwise DeckdbInputError (no fallback)."""
    a = rows[rows["status"] == STATUS_ASSIGNED]
    with_v = sorted(set(a.loc[a["variant"] != "", "deck"]))
    sub = a[a["deck"].isin(with_v)].assign(_v=lambda d: d["variant"].where(d["variant"] != "", NO_VARIANT_LABEL))
    out = sub.groupby(["deck", "_v"]).agg(Lists_Top32=("top8", "size"), Lists_Top8=("top8", "sum"),
                                          Trophies=("trophy", "sum")).reset_index()
    out = out.rename(columns={"deck": "Deck", "_v": "Variant"})
    for col in ("Lists_Top32", "Lists_Top8", "Trophies"):
        out[col] = out[col].astype(int)
    deck = a[a["deck"].isin(with_v)].groupby("deck").agg(Lists_Top32=("top8", "size"), Lists_Top8=("top8", "sum"),
                                                         Trophies=("trophy", "sum"))
    summed = out.groupby("Deck")[["Lists_Top32", "Lists_Top8", "Trophies"]].sum()
    bad = [d for d in with_v if tuple(summed.loc[d]) != tuple(int(x) for x in deck.loc[d])]
    if bad:
        raise DeckdbInputError(f"warianty tygodnia: suma wariantów różna od talii dla {bad}")
    return out.sort_values(["Deck", "Lists_Top32", "Variant"], ascending=[True, False, True]).reset_index(drop=True)


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
