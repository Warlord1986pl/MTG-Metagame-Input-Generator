"""Season calendar and per-season ranking rulesets for the pilot league.

SEASONS is the season calendar from Summer 2026 on: each season's name, its date range (both ends
inclusive) and its ruleset. An event belongs to the season whose range contains its EventDate
(season_for_date). Dates before the first configured season fall back to meteorological quarters
(the convention the league started with); a date after the last configured season raises
UnconfiguredSeasonError, so the rebuild stops until the next season is configured.

From Winter 2026/27 every season is a whole number of full 7-day weeks (13 weeks = 91 days; a 52-week
year drifts about one day per year against the calendar, so every 5-6 years one season will get a
14th week to realign -- not automated, it is a config change with an explicit K), and each season
starts the day after the previous one ends. validate_seasons() enforces this at import time.
Season names stay quarter-based; season dates come from this config, not from calendar months.

Each season is ranked under exactly one ruleset:

  legacy -- the rule Summer 2026 and Autumn 2026 were published under, implemented by
            league_engine._rank_table / _apply_tie_break_sort and left untouched: Points desc,
            Points/Starts desc, Wins, Top2, Top4, Top8, Top16 desc, Starts asc, LoginID asc.
            Ranks are always 1..N with no shared rank.
  k10    -- from Winter 2026/27 (2026-12-01). Implemented here, see compare():
            1. SeasonScore desc (sum of the pilot's K best per-event points; all of them if fewer
               than K results)
            2. Wins, Top2, Top4, Top8, Top16 desc (whole-season counts)
            3. points per start desc (all results), compared as exact fractions
            4. Starts desc
            5. placement vector asc (each pilot's placements sorted ascending, lexicographic)
            6. otherwise the pilots share the rank (standard competition ranking: 1, 2, 2, 4)
            No LoginID key and no date key. Inside a shared-rank group the FILE row order is
            canonical LoginID ascending; that order never affects Rank.

The ruleset is configuration, not something derived from the data: a season missing from
SEASONS that starts on or after FIRST_CONFIG_REQUIRED_START raises UnconfiguredSeasonError instead
of silently picking a rule (K is a per-season value).
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from typing import Dict, Hashable, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

LEGACY = "legacy"
K10 = "k10"


@dataclass(frozen=True)
class Ruleset:
    name: str
    k: Optional[int] = None  # number of counted results; k10 only


LEGACY_RULESET = Ruleset(LEGACY)

@dataclass(frozen=True)
class Season:
    name: str
    start: date
    end: date  # inclusive
    ruleset: Ruleset


# Summer 2026 and Autumn 2026 keep the dates they were published with (meteorological quarters)
# and the legacy rule: nothing about them may change. Names are what the site and file slugs use
# (Winter spans the year boundary and is labelled "Winter 2026/27").
SEASONS: Tuple[Season, ...] = (
    Season("Summer 2026", date(2026, 6, 1), date(2026, 8, 31), LEGACY_RULESET),
    Season("Autumn 2026", date(2026, 9, 1), date(2026, 11, 30), LEGACY_RULESET),
    Season("Winter 2026/27", date(2026, 12, 1), date(2027, 3, 1), Ruleset(K10, k=10)),
    Season("Spring 2027", date(2027, 3, 2), date(2027, 5, 31), Ruleset(K10, k=10)),
)

# Seasons starting before this date that are not configured predate the k10 rule and are legacy.
# Seasons starting on/after it must be configured explicitly.
FIRST_CONFIG_REQUIRED_START = date(2026, 12, 1)


class UnconfiguredSeasonError(LookupError):
    """No configured season covers a date / season on or after FIRST_CONFIG_REQUIRED_START."""


class SeasonConfigError(ValueError):
    """SEASONS (or a season_config.csv row) is inconsistent; see validate_seasons()."""


def validate_seasons(seasons: Sequence[Season]) -> None:
    """Raises SeasonConfigError unless *seasons* is a valid calendar: unique names, start <= end,
    each season starting exactly the day after the previous one ends (no overlap, no gap), and
    every k10 season a whole number of 7-day weeks with an integer K >= 1."""
    names = [x.name for x in seasons]
    if len(set(names)) != len(names):
        raise SeasonConfigError(f"duplicate season names in {names}")
    for x in seasons:
        if x.end < x.start:
            raise SeasonConfigError(f"{x.name}: ends {x.end} before it starts {x.start}")
        if x.ruleset.name == K10:
            days = (x.end - x.start).days + 1
            if days % 7:
                raise SeasonConfigError(
                    f"{x.name}: a k10 season must be whole weeks, got {days} days ({x.start}..{x.end})"
                )
            if not isinstance(x.ruleset.k, int) or isinstance(x.ruleset.k, bool) or x.ruleset.k < 1:
                raise SeasonConfigError(f"{x.name}: k10 needs an integer K >= 1, got {x.ruleset.k!r}")
    ordered = sorted(seasons, key=lambda x: x.start)
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start <= prev.end:
            raise SeasonConfigError(f"{prev.name} ({prev.start}..{prev.end}) overlaps {nxt.name} ({nxt.start}..{nxt.end})")
        if nxt.start != prev.end + timedelta(days=1):
            raise SeasonConfigError(
                f"gap between {prev.name} (ends {prev.end}) and {nxt.name} (starts {nxt.start})"
            )


validate_seasons(SEASONS)
SEASON_RULESETS: Dict[str, Ruleset] = {x.name: x.ruleset for x in SEASONS}
_SEASONS_BY_NAME: Dict[str, Season] = {x.name: x for x in SEASONS}


def _meteorological_season(d: date) -> Tuple[str, date, date]:
    """The original season convention (calendar quarters), used only for dates before the first
    configured season."""
    m, y = d.month, d.year
    if m in (3, 4, 5):
        return f"Spring {y}", date(y, 3, 1), date(y, 5, 31)
    if m in (6, 7, 8):
        return f"Summer {y}", date(y, 6, 1), date(y, 8, 31)
    if m in (9, 10, 11):
        return f"Autumn {y}", date(y, 9, 1), date(y, 11, 30)
    y0 = y if m == 12 else y - 1
    end = date(y0 + 1, 3, 1) - timedelta(days=1)
    return f"Winter {y0}/{str(y0 + 1)[-2:]}", date(y0, 12, 1), end


def season_for_date(d: date, seasons: Sequence[Season] = SEASONS) -> Tuple[str, date, date]:
    """(name, start, end) of the configured season whose range contains *d*. Before the first
    configured season: meteorological quarters. After the last one: UnconfiguredSeasonError."""
    for x in seasons:
        if x.start <= d <= x.end:
            return x.name, x.start, x.end
    if seasons and d < min(x.start for x in seasons):
        return _meteorological_season(d)
    raise UnconfiguredSeasonError(
        f"no season configured for {d.isoformat()}; add the next season to league_rules.SEASONS "
        f"(dates, ruleset and K)"
    )


def check_season_config_rows(rows: Iterable[Tuple[str, date, date]]) -> None:
    """Raises SeasonConfigError if a (Season, StartDate, EndDate) row of season_config.csv names a
    configured season with dates other than its configured ones (the file is derived from SEASONS
    and must agree with it)."""
    for name, start, end in rows:
        x = _SEASONS_BY_NAME.get(str(name).strip())
        if x is not None and (start, end) != (x.start, x.end):
            raise SeasonConfigError(
                f"season_config.csv: {name} is {start}..{end}, config says {x.start}..{x.end}"
            )


# --- season close -------------------------------------------------------------------------------
#
# A season's record (docs/data/pilot_league_seasons.csv, league_results_export.maybe_close_season) is
# written on the first run that has ingested a result dated after the season's end -- by the
# EventDate of an ingested event, not by the run date -- so late results for the last days of a
# season (ingestion lag is ~2-3 days) still count. Until then the season is "final results pending"
# on the site and stays the default season. Summer 2026 was closed by the old run-date rule; its
# record is already written and never touched again.
#
# A result dated inside a closed season that is ingested after the close (detected from Autumn 2026
# on, see LATE_AFTER_CLOSE_FROM) still goes into that season's standings and results, is flagged in
# the season's results manifest (LateArrivals, AfterSeasonClose) and alerted on; the record itself
# is never changed automatically.
LATE_AFTER_CLOSE_FROM = date(2026, 9, 1)


def season_is_closed(season: str, season_end: date, latest_event_date: Optional[date], registered: Iterable[str]) -> bool:
    """True once *season* has a record, or once a result dated after its end has been ingested
    (the run that sees that result writes the record)."""
    if season in set(registered):
        return True
    return latest_event_date is not None and latest_event_date > season_end


def default_season(
    seasons: Sequence[Tuple[str, date, date]], closed: Dict[str, bool], as_of: date
) -> Optional[str]:
    """The season the site opens on: the earliest started season that is not closed yet (a season
    whose end has passed stays the default, "final results pending", until its record is written),
    else the latest started season."""
    started = sorted((x for x in seasons if x[1] <= as_of), key=lambda x: x[1])
    for name, _start, _end in started:
        if not closed.get(name, False):
            return name
    if started:
        return started[-1][0]
    return sorted(seasons, key=lambda x: x[1])[-1][0] if seasons else None


def ruleset_for_season(season: str, season_start: date) -> Ruleset:
    rs = SEASON_RULESETS.get(season)
    if rs is not None:
        return rs
    if season_start < FIRST_CONFIG_REQUIRED_START:
        return LEGACY_RULESET
    raise UnconfiguredSeasonError(
        f"no ranking ruleset configured for season {season!r} (starts {season_start.isoformat()}); "
        f"add it to league_rules.SEASONS with an explicit K"
    )


# --- k10 ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PilotStats:
    """Everything the k10 comparator reads, for one pilot over one set of results (a season, or
    one weekly window). *score* is the SeasonScore (K best) for a season, or the plain sum of all
    points for a weekly window (k=None)."""
    score: int
    points: int
    starts: int
    wins: int
    top2: int
    top4: int
    top8: int
    top16: int
    placements: Tuple[int, ...]  # ascending
    # Points of the K-th counted result when starts >= K, else None. Not a ranking key.
    weakest_counted: Optional[int] = None

    @staticmethod
    def from_results(points: Sequence[int], placements: Sequence[int], k: Optional[int]) -> "PilotStats":
        pts = [int(p) for p in points]
        pl = sorted(int(p) for p in placements)
        best = sorted(pts, reverse=True)
        score = sum(best if k is None else best[:k])
        return PilotStats(
            score=score, points=sum(pts), starts=len(pl),
            wins=sum(p == 1 for p in pl), top2=sum(p <= 2 for p in pl), top4=sum(p <= 4 for p in pl),
            top8=sum(p <= 8 for p in pl), top16=sum(p <= 16 for p in pl), placements=tuple(pl),
            weakest_counted=best[k - 1] if k is not None and len(best) >= k else None,
        )


def compare(a: PilotStats, b: PilotStats) -> int:
    """-1 if *a* ranks above *b*, 1 if below, 0 if they share the rank. See module docstring."""
    for x, y in (
        (a.score, b.score), (a.wins, b.wins), (a.top2, b.top2), (a.top4, b.top4),
        (a.top8, b.top8), (a.top16, b.top16),
    ):
        if x != y:
            return -1 if x > y else 1
    # 3. points per start as exact fractions: a.points/a.starts vs b.points/b.starts.
    lhs, rhs = a.points * b.starts, b.points * a.starts
    if lhs != rhs:
        return -1 if lhs > rhs else 1
    # 4. more Top 32 finishes ranks higher.
    if a.starts != b.starts:
        return -1 if a.starts > b.starts else 1
    # 5. placement vectors (equal length here, since Starts are equal), best placement first.
    if a.placements != b.placements:
        return -1 if a.placements < b.placements else 1
    return 0


def row_order_key(login_id: str, pilot: str) -> tuple:
    """File row order inside a shared-rank group: canonical LoginID ascending (numeric); a
    name-keyed identity without LoginID goes after every id-keyed one, by name. Never a ranking
    key."""
    lid = str(login_id).strip()
    if lid.isdigit():
        return (0, int(lid), "")
    return (1, 0, str(pilot))


def competition_rank(
    stats: Dict[Hashable, PilotStats], order_key: Dict[Hashable, tuple]
) -> List[Tuple[Hashable, int, bool]]:
    """Sorts *stats* by compare() and returns [(key, Rank, Tied), ...] in file order.

    Standard competition ranking: Rank = 1 + number of pilots strictly better. Tied is True for
    every member of a group of two or more equal pilots. Keys are pre-sorted by *order_key* and the
    comparator sort is stable, so a tie group comes out in *order_key* order and the result does
    not depend on the input order of *stats*.
    """
    keys = sorted(stats, key=lambda k: (order_key[k], str(k)))
    keys.sort(key=functools.cmp_to_key(lambda x, y: compare(stats[x], stats[y])))
    ranks: List[int] = []
    for i, k in enumerate(keys):
        if i and compare(stats[keys[i - 1]], stats[k]) == 0:
            ranks.append(ranks[-1])
        else:
            ranks.append(i + 1)
    group_size: Dict[int, int] = {}
    for r in ranks:
        group_size[r] = group_size.get(r, 0) + 1
    return [(k, r, group_size[r] > 1) for k, r in zip(keys, ranks)]


def _event_id_sort(v: object) -> tuple:
    s = str(v).strip()
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def counted_mask(rows: pd.DataFrame, key_col: str, k: int) -> pd.Series:
    """Boolean Series (aligned to rows.index): True for the results that make up each pilot's
    SeasonScore. Per pilot (*key_col*), the first K rows in the order PointsAwarded desc, Placement
    asc, EventDate asc, EventID asc. Columns read: LeaguePoints, Place, EventDate, EventID. The
    score itself does not depend on this choice, only which rows are flagged."""
    if rows.empty:
        return pd.Series(dtype=bool)
    pts = pd.to_numeric(rows["LeaguePoints"], errors="coerce").fillna(0).astype(int)
    place = pd.to_numeric(rows["Place"], errors="coerce")
    order = sorted(
        rows.index,
        key=lambda i: (
            str(rows.at[i, key_col]), -int(pts.at[i]),
            float("inf") if pd.isna(place.at[i]) else float(place.at[i]),
            str(rows.at[i, "EventDate"]).strip(), _event_id_sort(rows.at[i, "EventID"]),
        ),
    )
    out = pd.Series(False, index=rows.index)
    taken: Dict[str, int] = {}
    for i in order:
        key = str(rows.at[i, key_col])
        if taken.get(key, 0) < k:
            out.at[i] = True
            taken[key] = taken.get(key, 0) + 1
    return out


def stats_by_key(rows: pd.DataFrame, key_col: str, k: Optional[int]) -> Dict[str, PilotStats]:
    """PilotStats per *key_col* value. Columns read: LeaguePoints, Place."""
    out: Dict[str, PilotStats] = {}
    if rows.empty:
        return out
    pts = pd.to_numeric(rows["LeaguePoints"], errors="coerce").fillna(0).astype(int)
    place = pd.to_numeric(rows["Place"], errors="coerce")
    if place.isna().any():
        bad = rows.loc[place.isna(), ["EventID", key_col]].head(5).to_dict("records")
        raise AssertionError(f"k10: result row(s) without a numeric Place: {bad}")
    for key, idx in rows.groupby(key_col, sort=True).groups.items():
        out[str(key)] = PilotStats.from_results(pts.loc[idx].tolist(), place.loc[idx].astype(int).tolist(), k)
    return out


# A weekly window is final WEEKLY_GRACE_DAYS days after its last day (late-arriving mtgo.com results
# can still change its winner until then): with window end E, its win is provisional while
# E < as_of <= E + grace, and final once as_of > E + grace. Only final wins count in WeeklyWins.
WEEKLY_GRACE_DAYS = 3


@dataclass(frozen=True)
class WeeklyOutcome:
    week: int
    start: date
    end: date
    winners: Tuple[str, ...]  # keys at rank 1 of the window, file order; empty if no results
    provisional: bool


def weekly_windows(season_start: date, season_end: date) -> List[Tuple[int, date, date]]:
    """[(N, start, end), ...]: full 7-day windows counted from the season start, week N =
    [start + 7(N-1), start + 7(N-1) + 6]. A k10 season is whole weeks (validate_seasons), so there
    is no partial week; a range that is not raises SeasonConfigError."""
    days = (season_end - season_start).days + 1
    if days < 7 or days % 7:
        raise SeasonConfigError(
            f"weekly windows need a whole number of weeks, got {days} days ({season_start}..{season_end})"
        )
    return [
        (n, season_start + timedelta(days=7 * (n - 1)), season_start + timedelta(days=7 * (n - 1) + 6))
        for n in range(1, days // 7 + 1)
    ]


def weekly_outcomes(
    rows: pd.DataFrame,
    key_col: str,
    order_key: Dict[str, tuple],
    season_start: date,
    season_end: date,
    as_of: date,
) -> List[WeeklyOutcome]:
    """One WeeklyOutcome per window whose last day is before *as_of* (final or provisional, see
    WEEKLY_GRACE_DAYS); windows still running are left out. Each window is ranked with compare(),
    its score being the sum of all the window's points (no K limit); every pilot at rank 1 of the
    window (including a shared rank 1) is a winner."""
    out: List[WeeklyOutcome] = []
    dates = pd.to_datetime(rows["EventDate"], errors="coerce").dt.date if not rows.empty else None
    for n, ws, we in weekly_windows(season_start, season_end):
        if not as_of > we:
            continue
        provisional = as_of <= we + timedelta(days=WEEKLY_GRACE_DAYS)
        winners: List[str] = []
        if dates is not None:
            window = rows[((dates >= ws) & (dates <= we)).fillna(False)]
            if not window.empty:
                st = stats_by_key(window, key_col, None)
                for key, rank, _tied in competition_rank(st, {k: order_key[k] for k in st}):
                    if rank != 1:
                        break
                    winners.append(key)
        out.append(WeeklyOutcome(n, ws, we, tuple(winners), provisional))
    return out


def count_wins(outcomes: Iterable[WeeklyOutcome], provisional: bool) -> Dict[str, int]:
    """Wins per key over the final (provisional=False) or the provisional (True) outcomes."""
    wins: Dict[str, int] = {}
    for o in outcomes:
        if o.provisional == provisional:
            for key in o.winners:
                wins[key] = wins.get(key, 0) + 1
    return wins


def weekly_wins(
    rows: pd.DataFrame,
    key_col: str,
    order_key: Dict[str, tuple],
    season_start: date,
    season_end: date,
    as_of: date,
) -> Dict[str, int]:
    """Final weekly wins per pilot (the WeeklyWins column); provisional windows are not counted."""
    return count_wins(
        weekly_outcomes(rows, key_col, order_key, season_start, season_end, as_of), provisional=False
    )


def sort_key(s: PilotStats) -> tuple:
    """An independent, key-based formulation of compare() (smaller tuple = better), used by
    league_engine.validate_league to check the production comparator rather than reuse it."""
    return (
        -s.score, -s.wins, -s.top2, -s.top4, -s.top8, -s.top16,
        -Fraction(s.points, s.starts) if s.starts else Fraction(0),
        -s.starts, s.placements,
    )
