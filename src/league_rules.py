"""Per-season ranking rulesets for the pilot league.

Each season is ranked under exactly one ruleset, looked up by season name in SEASON_RULESETS:

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
SEASON_RULESETS that starts on or after FIRST_CONFIG_REQUIRED_START raises
UnconfiguredSeasonError instead of silently picking a rule (K is a per-season value).
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

# Season names as league_engine.season_for_date produces them (Winter spans the year boundary and
# is labelled "Winter 2026/27"). Autumn 2026 is pinned to legacy explicitly: nothing about the
# season in progress may change.
SEASON_RULESETS: Dict[str, Ruleset] = {
    "Summer 2026": LEGACY_RULESET,
    "Autumn 2026": LEGACY_RULESET,
    "Winter 2026/27": Ruleset(K10, k=10),
    "Spring 2027": Ruleset(K10, k=10),
}

# Seasons starting before this date that are not listed above predate the k10 rule and are
# legacy. Seasons starting on/after it must be listed explicitly.
FIRST_CONFIG_REQUIRED_START = date(2026, 12, 1)


class UnconfiguredSeasonError(LookupError):
    """A season on/after FIRST_CONFIG_REQUIRED_START has no entry in SEASON_RULESETS."""


def ruleset_for_season(season: str, season_start: date) -> Ruleset:
    rs = SEASON_RULESETS.get(season)
    if rs is not None:
        if rs.name == K10 and (not isinstance(rs.k, int) or rs.k < 1):
            raise ValueError(f"ruleset for {season!r}: k10 needs an integer K >= 1, got {rs.k!r}")
        return rs
    if season_start < FIRST_CONFIG_REQUIRED_START:
        return LEGACY_RULESET
    raise UnconfiguredSeasonError(
        f"no ranking ruleset configured for season {season!r} (starts {season_start.isoformat()}); "
        f"add it to league_rules.SEASON_RULESETS with an explicit K"
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
    """[(N, start, end), ...]: 7-day windows counted from the season start, week N =
    [start + 7(N-1), start + 7(N-1) + 6], the last one clipped at the season's end date."""
    out = []
    n, s = 1, season_start
    while s <= season_end:
        out.append((n, s, min(s + timedelta(days=6), season_end)))
        n, s = n + 1, s + timedelta(days=7)
    return out


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
