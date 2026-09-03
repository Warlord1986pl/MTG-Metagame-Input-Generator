"""Season-long pilot league, built as a second consumer of the Challenge history the pipeline
already parses (challenge_history_modern.csv) plus the premier registry (premier_history_modern.csv).
This module never fetches anything itself -- no MTGO/MTGGoldfish requests -- it only reads the same
persistent CSVs challenge_history_engine already maintains, so it can never drift out of sync with
what the weekly Challenge/Premier pipeline saw.

Keyed by EventID (not by weekly window), so overlapping/rolling windows are naturally idempotent:
re-syncing an event that's already on disk overwrites exactly that one file and nothing else.

Premier handling: premier events are excluded from every metagame metric elsewhere in the pipeline
(meta share, encounter probability, winrate, ...) because their player pool and size would distort
those numbers -- that reasoning does not apply here, where the league ranks player achievement, not
the format. Premier events therefore enter the league (at double points) and stay out of everything
else. An event is premier for league purposes if and only if its EventID appears in
premier_history_modern.csv -- membership, never name/slug matching, since new premier event types
appear over time and a text filter would silently miss them.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

try:
    from challenge_history_engine import load_challenge_history, normalize_name
except ImportError:
    from .challenge_history_engine import load_challenge_history, normalize_name

try:
    from league_matches import process_event_standings_and_bracket, detect_pilot_renames
except ImportError:
    try:
        from .league_matches import process_event_standings_and_bracket, detect_pilot_renames
    except ImportError:
        process_event_standings_and_bracket = None  # type: ignore[assignment]
        detect_pilot_renames = None  # type: ignore[assignment]

try:
    import identity as pilot_identity
except ImportError:
    from . import identity as pilot_identity


class LeagueBlockingError(Exception):
    """Raised by run_league_update when a snapshot write or validate_league fails -- the two
    failure modes that leave the league's delta chain silently broken if swallowed: a missing
    snapshot means next week's Prev*/D* columns come back blank, and a failed validate_league means
    a season table went out the door with the invariants already known to be wrong. Both are
    deliberately distinguished from every OTHER league/Challenge failure (check_league_invariants,
    check_premier_completeness, a bad fetch, ...), which stays a plain AssertionError/Exception and
    is handled the old way (logged, does not abort the run) -- see metagame_input_generator.py's
    except clauses, which catch this type specifically to mark the run's exit code non-zero without
    escalating every other kind of league hiccup the same way.
    """


LEAGUE_RESULTS_COLS: List[str] = [
    "EventID",
    "EventDate",
    # Tier is the only per-row tier label in this file. The source history CSV's raw tier number
    # (formerly a separate "ChallengeSize" column here) is fully recoverable from this one --
    # strip the "C" prefix for Challenge rows; premier rows never had a meaningful raw number
    # (source Tier was the literal string "None") -- so it was dropped as a duplicate rather than
    # given a second name to avoid colliding with this pre-existing column.
    "Tier",
    "EventClass",
    "Pilot",
    # Stable per-account id from the source history's LoginID column -- survives an mtgo.com
    # account rename, unlike Pilot. This, not Pilot, is what the season table groups pilots by.
    # Blank for rows sourced from a pre-2026-07-13 history row (no LoginID available there either).
    "LoginID",
    "Place",
    "Deck",
    "LeaguePoints",
    # Swiss standings, sourced from the already-cached mtgo.com event JSON (see league_matches.py)
    # -- data capture only, no scoring or season logic reads these. SwissRank is the Swiss-round
    # seeding; Place (above) is the final post-bracket standing -- they agree for places 9-32 and
    # usually disagree in the top 8, which is the point of keeping both.
    "SwissRank",
    "SwissPoints",
    "OMWP",
    "GWP",
    "OGWP",
]

SEASON_CONFIG_COLS: List[str] = ["Season", "StartDate", "EndDate"]

# Immutable weekly capture, keyed by LoginID -- see write_weekly_snapshot. This, not a running
# total, is what next week's Prev*/D* columns are computed from.
SNAPSHOT_COLS: List[str] = [
    "LoginID", "Rank", "Points", "PremierPoints", "Wins", "Top2", "Top4", "Top8", "Top16", "Starts",
]

# The 14 week-over-week delta columns, in the exact order/names the published site reads by name
# (see build_season_table / _apply_deltas). PrevPoints etc. come straight from the most recent
# snapshot older than the current run's ISO week; D* = current value - Prev*.
DELTA_COLS: List[str] = [
    "PrevPoints", "PrevStarts", "PrevTop16", "PrevTop8", "PrevTop4", "PrevTop2", "PrevWins",
    "DPoints", "DStarts", "DTop16", "DTop8", "DTop4", "DTop2", "DWins",
]

PILOT_TABLE_COLS: List[str] = [
    "Rank", "Pilot", "LoginID", "Points", "PremierPoints", "Wins", "Top2", "Top4", "Top8", "Top16",
    "Starts", "PrevRank", "RankChange",
    *DELTA_COLS,
    # Not part of the published 14-column delta block above (that list's order/names are fixed) --
    # appended here only because validate_league's check 16 (DPoints formula) needs the previous
    # week's PremierPoints to verify the premier-bonus term, and nothing else already carries it.
    "PrevPremierPoints",
]


def league_points_for_place(place: object) -> int:
    """Challenge ladder: 1 point for reaching the Top 16, 1 more for reaching the Top 8, 1 more for
    each elimination match won. Resolves to 5/4/3/3/2/2/2/2/1x8/0x16 for places 1..32+. Place 17..32
    (and anything unparseable) scores 0 -- Top32 rows are kept for storage completeness (see
    LEAGUE_RESULTS_COLS docstring at call sites), not because they score. Premier events use
    premier_points_for_place below -- exactly double this ladder. Total per Challenge event is
    always 31 (5+4+3+3+2*4+1*8), asserted in check_league_invariants.
    """
    try:
        p = int(float(place))
    except (TypeError, ValueError):
        return 0
    if p == 1:
        return 5
    if p == 2:
        return 4
    if p in (3, 4):
        return 3
    if 5 <= p <= 8:
        return 2
    if 9 <= p <= 16:
        return 1
    return 0


def premier_points_for_place(place: object) -> int:
    """Premier ladder: the Challenge ladder doubled. A Top 8 in an RC Super Qualifier is harder
    than a Top 8 in a Challenge 32, so it is worth more -- uniformly across every premier event
    type (RC Super Qualifier, Showcase Qualifier, Showcase Challenge, Qualifier, Champions
    Showcase, and any type not yet seen). No per-type multipliers.
    """
    return 2 * league_points_for_place(place)


def season_for_date(d: date) -> Tuple[str, date, date]:
    """Meteorological quarter for *d*. Winter spans the year boundary and is labelled e.g.
    "Winter 2026/27" for events from 2026-12-01 through 2027-02-28/29 inclusive.
    """
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


def rank_change_anchor(as_of: date, coverage_end: Optional[date] = None) -> date:
    """The single source of truth for "which date is RankChange measured against" -- called both
    by build_season_table (to pick the PrevRank baseline) and by anything downstream that needs to
    report or re-derive the same value (league_site_export, league_results_export), so the anchor
    can never drift between what was actually used and what gets published about it.

    anchor = the most recent Wednesday on or before *as_of*. An event dated ON that Wednesday
    belongs to the NEXT weekly window, not this one -- build_season_table's baseline filter is
    EventDate < anchor, so this stays a plain "most recent Wednesday <= as_of" rather than needing
    a same-day special case here.

    Within any single calendar week (Wed through the following Tue), every as_of maps to the SAME
    anchor -- this is what actually fixes the old rolling "as_of - 7 days" baseline, which gave a
    different answer on every single day. Consecutive weekly editions tile the season with no
    overlap and no gap even if a download happens late; anchoring to as_of directly does not.

    If *coverage_end* is given and the anchor as computed above would land AFTER it, the anchor is
    frozen instead at the most recent Wednesday on or before coverage_end -- and stays there
    forever, for any as_of from then on. This is what stops a closed season's RankChange from
    sliding into an ever-emptier window and decaying to 0 as days pass with no new events: once a
    season has no more events coming (as_of has moved past its last actual EventDate), the
    published RankChange is permanently the one measured over the last complete window that
    actually contained events.
    """
    days_since_wednesday = (as_of.weekday() - 2) % 7  # date.weekday(): Monday=0 ... Wednesday=2
    anchor = as_of - timedelta(days=days_since_wednesday)
    if coverage_end is not None and anchor > coverage_end:
        frozen_days_since_wednesday = (coverage_end.weekday() - 2) % 7
        anchor = coverage_end - timedelta(days=frozen_days_since_wednesday)
    return anchor


def season_filename_slug(season: str) -> str:
    """Filesystem/Windows-safe form of a season name -- "Winter 2026/27" has a "/" that is not a
    legal path character, so it becomes "Winter_2026-27".
    """
    return re.sub(r"\s+", "_", season.replace("/", "-").strip())


def _write_csv_lf(df: pd.DataFrame, path: Path) -> None:
    """Writes UTF-8 without a BOM and with LF line endings, regardless of platform. These files
    are version-controlled (unlike the rest of outputs/, which is regenerable and gitignored), so
    they need a stable, diff-friendly format for years, not the utf-8-sig/CRLF Excel-friendly
    default used elsewhere in this pipeline. Reads stay on "utf-8-sig" (see load_challenge_history
    and load_all_league_results) since that transparently accepts both BOM and non-BOM input.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        # pandas writes lineterminator as literal characters into the output regardless of the
        # file's newline mode -- it defaults to os.linesep, which is "\r\n" on Windows. Passing
        # "\n" explicitly is what actually forces LF here; newline="" above only stops Python's
        # own text-mode layer from adding a second translation on top of that.
        df.to_csv(f, index=False, lineterminator="\n")


def _clean_event_id_col(df: pd.DataFrame) -> pd.DataFrame:
    """Same defensive guard as challenge_history_engine._clean_history_df_before_write: pandas has
    been observed silently upcasting a numeric-looking EventID string column to float during an
    intermediate operation, coming back out as "12345.0" instead of "12345".
    """
    if "EventID" in df.columns:
        df = df.copy()
        df["EventID"] = df["EventID"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


def _load_premier_event_ids(premier_history_csv: Optional[Path]) -> set:
    """The sole source of truth for "is this EventID premier": membership in
    premier_history_modern.csv, never name/slug matching."""
    if premier_history_csv is None or not premier_history_csv.exists():
        return set()
    hist = load_challenge_history(premier_history_csv)
    if hist.empty:
        return set()
    return set(hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True))


def _log_premier_event_slugs(premier_history_csv: Optional[Path], log: Optional[Callable[[str], None]]) -> None:
    """Prints every distinct EventSlug on file so a brand-new premier event type is visible in the
    log the week it first appears, instead of being discovered later through a wrong total."""
    if log is None or premier_history_csv is None or not premier_history_csv.exists():
        return
    hist = load_challenge_history(premier_history_csv)
    if hist.empty:
        return
    slugs = sorted(set(hist["EventSlug"].astype(str).str.strip()))
    log(f"[league] premier EventSlug types on file: {slugs}")


def backfill_event_class(results_dir: Path, log: Optional[Callable[[str], None]] = None) -> int:
    """One-time migration: adds EventClass="Challenge" to any results/<EventID>.csv written before
    the Premier league feature existed (no EventClass column at all). Premier files always write
    their own EventClass, so there is no ambiguity to backfill there. Idempotent -- a no-op once
    every file has the column. Returns the number of files patched.
    """
    if not results_dir.exists():
        return 0
    patched = 0
    for p in sorted(results_dir.glob("*.csv")):
        try:
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if df.empty or "EventClass" in df.columns:
            continue
        df["EventClass"] = "Challenge"
        df = df[[c for c in LEAGUE_RESULTS_COLS if c in df.columns]]
        _write_csv_lf(df, p)
        patched += 1
        if log is not None:
            log(f"[league] backfilled EventClass=Challenge on {p.name}")
    return patched


SWISS_FIELDS: List[str] = ["SwissRank", "SwissPoints", "OMWP", "GWP", "OGWP"]


def _capture_standings_and_bracket(
    event_id: str,
    event_date: str,
    tier: str,
    event_class: str,
    deck_by_loginid: Dict[str, str],
    mtgo_json_cache_dir: Optional[Path],
    matches_dir: Optional[Path],
    no_bracket_csv: Optional[Path],
    log: Optional[Callable[[str], None]],
) -> Dict[str, dict]:
    """Thin guard around league_matches.process_event_standings_and_bracket: returns {} (leaving
    the five Swiss columns blank, touching nothing under matches/) whenever the feature isn't
    wired up -- module unavailable, or any of the three path params left None -- so callers that
    don't pass them behave exactly as before this capability existed.

    Also isolates one event's hard-invariant failure (AssertionError from league_matches'
    standings/bracket checks) from every other event: this is data capture across dozens of
    events per run, and a genuine data-quality problem specific to one of them (malformed JSON,
    an irregular bracket) must not blank out standings/bracket capture for the other, clean
    events. The assertion still prints its full detail (via league_matches' _fail) before being
    caught here, so it is never silent -- it just doesn't abort the run. Note this is no longer
    the account-rename case: league_matches now correlates entirely on LoginID, so a renamed
    account (history's frozen Pilot differing from the JSON's current name) doesn't fail this at
    all -- see detect_pilot_renames for that, now purely informational.
    """
    if process_event_standings_and_bracket is None:
        return {}
    if mtgo_json_cache_dir is None or matches_dir is None or no_bracket_csv is None:
        return {}
    try:
        swiss = process_event_standings_and_bracket(
            event_id=event_id,
            event_date=event_date,
            tier=tier,
            event_class=event_class,
            deck_by_loginid=deck_by_loginid,
            mtgo_json_cache_dir=mtgo_json_cache_dir,
            matches_dir=matches_dir,
            no_bracket_csv=no_bracket_csv,
            log=log,
        )
    except AssertionError as exc:
        if log is not None:
            log(f"[ERROR] [league-matches] {event_id}: standings/bracket capture FAILED, skipped for this event only: {exc}")
        return {}
    return swiss or {}


def sync_challenge_league_results(
    history_csv: Path,
    results_dir: Path,
    format_name: str,
    start_date: date,
    end_date: date,
    premier_ids: Optional[set] = None,
    mtgo_json_cache_dir: Optional[Path] = None,
    matches_dir: Optional[Path] = None,
    no_bracket_csv: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Write/overwrite outputs/league/results/<EventID>.csv (EventClass="Challenge") for every
    Challenge event whose EventDate falls in [start_date, end_date] and has a resolved EventID
    (rows still stuck on legacy/unmigrated data with a blank EventID are skipped, same as the
    Challenge stats pipeline).

    When *mtgo_json_cache_dir* is given, also captures Swiss standings (SwissRank/SwissPoints/
    OMWP/GWP/OGWP columns here) and the elimination bracket (written separately to *matches_dir*
    by league_matches.process_event_standings_and_bracket) from the already-cached mtgo.com event
    JSON -- no network request is made here or by that call. Left None, the five Swiss columns are
    blank and matches/ is untouched for these events, same as before this capability existed.

    *premier_ids* (from premier_history_modern.csv, by ID membership) are skipped here defensively:
    challenge_history_modern.csv is not known to contain premier rows, but this stays a hard
    exclusion in case a future data-quality regression leaks one in.

    Returns the list of EventIDs written. Idempotent: given the same history_csv contents, the
    same call always produces byte-identical files (rows sorted by Place, points computed purely
    from Place).
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    hist = load_challenge_history(history_csv)
    if hist.empty:
        return []

    fmt_norm = normalize_name(format_name)
    hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
    if hist.empty:
        return []

    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    hist["Tier"] = hist["Tier"].astype(str).str.strip()

    event_dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    in_range = (event_dates >= start_date) & (event_dates <= end_date)
    window = hist[in_range.fillna(False) & (hist["EventID"] != "")].copy()

    premier_ids = {str(x).strip() for x in (premier_ids or set())}

    results_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    skipped_premier: List[str] = []

    for eid, grp in window.groupby("EventID", sort=True):
        if eid in premier_ids:
            skipped_premier.append(eid)
            continue
        g = grp.copy()
        g["Place"] = pd.to_numeric(g["Place"], errors="coerce")
        g = g.dropna(subset=["Place"])
        if g.empty:
            continue
        g["Place"] = g["Place"].astype(int)
        g = g.sort_values("Place")

        event_date = str(g["EventDate"].iloc[0])
        size = str(g["Tier"].iloc[0])
        tier = f"C{size}"
        pilots = g["Pilot"].astype(str).str.strip()
        decks = g["Deck"].astype(str).str.strip()
        login_ids = g["LoginID"].astype(str).str.strip() if "LoginID" in g.columns else pd.Series([""] * len(g), index=g.index)

        # deck_by_loginid correlates this ONE event's own results rows (by history's own LoginID
        # column) against that SAME event's bracket JSON -- see league_matches.py, which does all
        # standings/bracket cross-referencing on LoginID throughout, never on display name.
        swiss = _capture_standings_and_bracket(
            event_id=eid, event_date=event_date, tier=tier, event_class="Challenge",
            deck_by_loginid=dict(zip(login_ids, decks)),
            mtgo_json_cache_dir=mtgo_json_cache_dir, matches_dir=matches_dir,
            no_bracket_csv=no_bracket_csv, log=log,
        )

        out = pd.DataFrame({
            "EventID": eid,
            "EventDate": event_date,
            "Tier": tier,
            "EventClass": "Challenge",
            "Pilot": pilots,
            "LoginID": login_ids,
            "Place": g["Place"],
            "Deck": decks,
            "LeaguePoints": g["Place"].apply(league_points_for_place),
            **{col: login_ids.map(lambda lid: swiss.get(lid, {}).get(col, "")) for col in SWISS_FIELDS},
        })[LEAGUE_RESULTS_COLS]

        out = _clean_event_id_col(out)
        path = results_dir / f"{eid}.csv"
        _write_csv_lf(out, path)
        written.append(eid)
        emit(f"[league] event {eid} ({event_date} C{size}): {len(out)} result(s) -> {path}")

    if skipped_premier:
        emit(
            f"[league] excluded {len(skipped_premier)} premier event(s) from Challenge sync "
            f"(by EventID membership in premier_history): {sorted(skipped_premier)}"
        )

    return written


def sync_premier_league_results(
    premier_history_csv: Optional[Path],
    results_dir: Path,
    format_name: str,
    start_date: date,
    end_date: date,
    mtgo_json_cache_dir: Optional[Path] = None,
    matches_dir: Optional[Path] = None,
    no_bracket_csv: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Write/overwrite outputs/league/results/<EventID>.csv (EventClass="Premier") for every
    premier event on file whose EventDate falls in [start_date, end_date]. Tier is set to the raw
    EventSlug (e.g. "modern-rc-super-qualifier") purely for readability -- it never affects
    scoring, which is uniform across premier types via premier_points_for_place. Premier source
    data covers places 1..32, the same depth as Challenges, so no special handling is needed for
    rows outside the Top 8 -- they score 0 and are kept, same as Challenge rows.

    Returns the list of EventIDs written. Same idempotency guarantee as
    sync_challenge_league_results.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    if premier_history_csv is None or not premier_history_csv.exists():
        return []

    hist = load_challenge_history(premier_history_csv)
    if hist.empty:
        return []

    fmt_norm = normalize_name(format_name)
    hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
    if hist.empty:
        return []

    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    hist["EventSlug"] = hist["EventSlug"].astype(str).str.strip()

    event_dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    in_range = (event_dates >= start_date) & (event_dates <= end_date)
    window = hist[in_range.fillna(False) & (hist["EventID"] != "")].copy()

    results_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []

    for eid, grp in window.groupby("EventID", sort=True):
        g = grp.copy()
        g["Place"] = pd.to_numeric(g["Place"], errors="coerce")
        g = g.dropna(subset=["Place"])
        if g.empty:
            continue
        g["Place"] = g["Place"].astype(int)
        g = g.sort_values("Place")

        event_date = str(g["EventDate"].iloc[0])
        tier = str(g["EventSlug"].iloc[0]).strip() or "Premier"
        pilots = g["Pilot"].astype(str).str.strip()
        decks = g["Deck"].astype(str).str.strip()
        login_ids = g["LoginID"].astype(str).str.strip() if "LoginID" in g.columns else pd.Series([""] * len(g), index=g.index)

        # deck_by_loginid correlates same-event results against the bracket JSON -- see the
        # Challenge-side comment above; identical reasoning for premier events.
        swiss = _capture_standings_and_bracket(
            event_id=eid, event_date=event_date, tier=tier, event_class="Premier",
            deck_by_loginid=dict(zip(login_ids, decks)),
            mtgo_json_cache_dir=mtgo_json_cache_dir, matches_dir=matches_dir,
            no_bracket_csv=no_bracket_csv, log=log,
        )

        out = pd.DataFrame({
            "EventID": eid,
            "EventDate": event_date,
            "Tier": tier,
            "EventClass": "Premier",
            "Pilot": pilots,
            "LoginID": login_ids,
            "Place": g["Place"],
            "Deck": decks,
            "LeaguePoints": g["Place"].apply(premier_points_for_place),
            **{col: login_ids.map(lambda lid: swiss.get(lid, {}).get(col, "")) for col in SWISS_FIELDS},
        })[LEAGUE_RESULTS_COLS]

        out = _clean_event_id_col(out)
        path = results_dir / f"{eid}.csv"
        _write_csv_lf(out, path)
        written.append(eid)
        emit(f"[league] premier event {eid} ({event_date} {tier}): {len(out)} result(s) -> {path}")

    return written


def load_all_league_results(results_dir: Path) -> pd.DataFrame:
    """Read every outputs/league/results/<EventID>.csv into one frame. This -- not a running
    total -- is the sole input to the season table, so a corrected event file propagates
    automatically on the next rebuild.
    """
    if not results_dir.exists():
        return pd.DataFrame(columns=LEAGUE_RESULTS_COLS)
    frames = []
    for p in sorted(results_dir.glob("*.csv")):
        try:
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=LEAGUE_RESULTS_COLS)
    combined = pd.concat(frames, ignore_index=True)
    for col in LEAGUE_RESULTS_COLS:
        if col not in combined.columns:
            combined[col] = "Challenge" if col == "EventClass" else ""
    return combined[LEAGUE_RESULTS_COLS]


def upsert_season_config(config_csv: Path, season: str, start: date, end: date) -> None:
    """Idempotent by season name: re-running never duplicates a row, it replaces it."""
    if config_csv.exists():
        try:
            df = pd.read_csv(config_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            df = pd.DataFrame(columns=SEASON_CONFIG_COLS)
    else:
        df = pd.DataFrame(columns=SEASON_CONFIG_COLS)
    for col in SEASON_CONFIG_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[df["Season"] != season]
    new_row = pd.DataFrame([{"Season": season, "StartDate": start.isoformat(), "EndDate": end.isoformat()}])
    df = pd.concat([df[SEASON_CONFIG_COLS], new_row], ignore_index=True)
    df = df.sort_values("StartDate", kind="mergesort").reset_index(drop=True)
    _write_csv_lf(df, config_csv)


def _identity_key(login_id: str, pilot: str) -> str:
    """The league groups pilots by LoginID (the stable per-account id from the mtgo.com JSON,
    survives an account rename) rather than by display name. When LoginID is missing -- rows from
    before 2026-07-13, where no LoginID was ever captured -- falls back to the display name.

    LoginID is first resolved through the pilot identity overlay (data/pilot_identity.csv --
    see identity.resolve()), so two loginids belonging to the same real person (recorded there as
    primary/alias) collapse to one key here, exactly like an account rename already does. A
    loginid with no entry in that overlay resolves to itself, so this is a no-op until a merge is
    recorded.

    The "id:"/"name:" prefix is not cosmetic: it keeps an id-keyed identity and a name-keyed
    identity from ever silently colliding, in the pathological case where some LoginID string
    happened to equal some other pilot's display name. Without the prefix that coincidence would
    silently merge two unrelated people; with it, they simply hash to different keys.
    """
    lid = str(login_id).strip()
    if lid:
        lid = pilot_identity.resolve(lid)
        return f"id:{lid}"
    return f"name:{str(pilot).strip()}"


def aggregate_pilot_table(results_df: pd.DataFrame) -> pd.DataFrame:
    """Pilot, LoginID, Points, PremierPoints, Wins, Top2, Top4, Top8, Top16, Starts -- unranked, one
    row per identity (see _identity_key: LoginID when available, the display name otherwise). Deck
    is deliberately absent: the league tracks pilots, and a per-pilot "best/only deck" field would
    repeat the same defect BestPilots.BestDeck has (collapsing a pilot who scored with two
    different decks into one).

    Pilot in the output is the display name attached to this identity's MOST RECENT EventDate --
    a renamed account shows its newest name, not whichever name happened to sort first or last.
    LoginID in the output is blank for a name-keyed (fallback) identity, so a reader can tell a
    real merged identity from a fallback one at a glance (see also build_season_table's coverage
    report, which counts and lists these).

    Points is the total across both event classes. PremierPoints is the premier-only subset, so a
    reader can tell apart a pilot with two Challenge trophies from one with a single premier
    finish, even when both reach a similar Points total. Wins/Top2/Top4/Top8/Top16/Starts count
    placements from both classes together -- a Top 8 is a Top 8 regardless of which ladder scored
    it.

    """
    cols = ["Pilot", "LoginID", "Points", "PremierPoints", "Wins", "Top2", "Top4", "Top8", "Top16", "Starts"]
    if results_df.empty:
        return pd.DataFrame(columns=cols)

    work = results_df.copy()
    work["Place"] = pd.to_numeric(work["Place"], errors="coerce")
    work["LeaguePoints"] = pd.to_numeric(work["LeaguePoints"], errors="coerce").fillna(0)
    if "EventClass" not in work.columns:
        work["EventClass"] = "Challenge"
    work["EventClass"] = work["EventClass"].astype(str).str.strip()
    work["Pilot"] = work["Pilot"].astype(str).str.strip()
    if "LoginID" not in work.columns:
        work["LoginID"] = ""
    work["LoginID"] = work["LoginID"].astype(str).str.strip()
    work = work[work["Pilot"] != ""]
    if work.empty:
        return pd.DataFrame(columns=cols)

    work["_Key"] = [_identity_key(lid, p) for lid, p in zip(work["LoginID"], work["Pilot"])]
    work["_EventDate_dt"] = pd.to_datetime(work.get("EventDate"), errors="coerce")

    grp = work.groupby("_Key")
    points = grp["LeaguePoints"].sum()
    premier_points = work[work["EventClass"] == "Premier"].groupby("_Key")["LeaguePoints"].sum()
    wins = grp["Place"].apply(lambda s: int((s == 1).sum()))
    top2 = grp["Place"].apply(lambda s: int((s <= 2).sum()))
    top4 = grp["Place"].apply(lambda s: int((s <= 4).sum()))
    top8 = grp["Place"].apply(lambda s: int((s <= 8).sum()))
    top16 = grp["Place"].apply(lambda s: int((s <= 16).sum()))
    starts = grp["EventID"].nunique()

    # Display name = Pilot at this identity's latest EventDate. Rows with an unparseable date are
    # excluded from the "latest" search (never let a bad date silently win); if that leaves an
    # identity with no dated row at all, fall back to whatever Pilot value appears first for it.
    # A pilot_profile.csv display_name override (see identity.display_name()) takes priority over
    # this "latest name wins" rule when one is set, for an id-keyed identity.
    dated = work.dropna(subset=["_EventDate_dt"])
    latest_idx = dated.groupby("_Key")["_EventDate_dt"].idxmax()
    display_pilot = work.loc[latest_idx.values].set_index(latest_idx.index)["Pilot"]
    first_pilot = work.groupby("_Key")["Pilot"].first()

    def _canonical_login_id(key: str) -> str:
        # The output LoginID is the resolved pilot_id encoded in the key itself, not "whichever
        # raw LoginID happened to appear first in this group" -- after a merge, two different raw
        # loginids share one key, and "first seen" could pick either one.
        return key[3:] if key.startswith("id:") else ""

    def _display_name_for(key: str) -> str:
        raw = display_pilot.get(key, first_pilot.get(key, ""))
        pilot_id = _canonical_login_id(key)
        return pilot_identity.display_name(pilot_id, fallback=raw) if pilot_id else raw

    idx = points.index
    out = pd.DataFrame({
        "_Key": idx,
        "Pilot": [_display_name_for(k) for k in idx],
        "LoginID": [_canonical_login_id(k) for k in idx],
        "Points": points.values,
        "PremierPoints": premier_points.reindex(idx).fillna(0).values,
        "Wins": wins.reindex(idx).values,
        "Top2": top2.reindex(idx).values,
        "Top4": top4.reindex(idx).values,
        "Top8": top8.reindex(idx).values,
        "Top16": top16.reindex(idx).values,
        "Starts": starts.reindex(idx).values,
    })
    out["Points"] = out["Points"].astype(int)
    out["PremierPoints"] = out["PremierPoints"].astype(int)
    return out[["_Key"] + cols]


def _apply_tie_break_sort(df: pd.DataFrame) -> pd.DataFrame:
    """The one production implementation of the tie-break rule described in _rank_table's
    docstring (that docstring is the rule's single source of truth for anything published outside
    the code) -- shared by _rank_table (building today's table) and _remigrate_snapshot_ranks
    (recomputing Rank inside an already-written snapshot with the same rule), so the two can never
    silently drift apart. Returns *df* stably re-ordered; does not add a Rank column itself.

    Every row must have Starts >= 1 (a pilot only ever enters this table via a Top32 appearance,
    so Points/Starts is always well-defined) -- raises AssertionError naming the offending LoginID
    otherwise, rather than letting a division by zero happen or silently skipping the row.
    """
    work = df.copy()
    starts_num = pd.to_numeric(work["Starts"], errors="coerce")
    bad = work[starts_num.isna() | (starts_num < 1)]
    if not bad.empty:
        offending = [
            {"LoginID": r.get("LoginID"), "Starts": r.get("Starts")}
            for r in bad.to_dict(orient="records")
        ]
        raise AssertionError(
            f"_apply_tie_break_sort: Starts must be >= 1 for every pilot (a pilot only enters "
            f"this table via a Top32 appearance) -- offending: {offending}"
        )

    work["_PtsPerStart"] = [
        Fraction(int(p), int(s)) for p, s in zip(work["Points"], work["Starts"])
    ]
    # Terminal key: a real LoginID is a unique account id, so this fully resolves every tie except
    # between two blank-LoginID name-fallback identities (pre-2026-07-13 legacy rows with no
    # LoginID at all) -- pd.to_numeric("") -> NaN, and sort_values already places NaN last
    # regardless of ascending direction, so no separate handling is needed for that residual case.
    work["_LoginIDNum"] = pd.to_numeric(work["LoginID"], errors="coerce")
    ordered = work.sort_values(
        ["Points", "_PtsPerStart", "Wins", "Top2", "Top4", "Top8", "Top16", "Starts", "_LoginIDNum"],
        ascending=[False, False, False, False, False, False, False, True, True],
        kind="mergesort",
    ).drop(columns=["_PtsPerStart", "_LoginIDNum"]).reset_index(drop=True)
    return ordered


def _rank_table(agg: pd.DataFrame) -> pd.DataFrame:
    """Tie-break chain -- this docstring is the single source of truth for the rule (it is quoted
    in published materials); the one production implementation is _apply_tie_break_sort, shared
    with _remigrate_snapshot_ranks. Applied in this exact order:

      1. Points, descending.
      2. Points / Starts, descending -- points earned per Top32 entry, as an exact
         fractions.Fraction(Points, Starts), never a float. A float comparison can split
         mathematically equal ratios apart, or merge distinct ones together, and this value
         decides the order of the published table. A single Top32 entry scores 0-5 points in a
         Challenge and 0-10 in a premier event, so this ratio is a real measure of how deep a
         pilot's runs go, not an artifact of how many events they entered -- it lightly offsets
         the raw Points column's inherent bias toward pilots who simply play often.
      3. Wins, descending.
      4. Top2, descending.
      5. Top4, descending.
      6. Top8, descending.
      7. Top16, descending.
      8. Starts, ascending -- in practice this key rarely decides anything on its own (LoginID,
         next, almost always does first), but stays in the chain at this position for continuity
         with the table's own display order.
      9. LoginID, ascending (ties before keys 1-8 are set are compared numerically; a
         name-fallback identity, which has no LoginID at all, sorts after every id-keyed one).
         A real LoginID is a unique per-account id, so this is a genuine terminal key: any two
         *distinct* id-keyed pilots are now always fully ordered by keys 1-9, with no remaining
         ambiguity for an outside reader re-deriving the table from the published results alone.

    A group still tied after all nine keys is a genuine, unresolved tie -- today this can only
    happen between two blank-LoginID name-fallback identities (pre-2026-07-13 legacy rows with no
    LoginID ever captured), an increasingly rare, purely historical edge case. Such a group's
    internal order falls out of the stable sort applied to *agg*'s existing order, which is itself
    deterministic (see aggregate_pilot_table's identity groupby), so re-running on identical data
    reproduces the same order even where the rule itself is silent.
    """
    if agg.empty:
        out = agg.copy()
        out.insert(0, "Rank", pd.Series(dtype=int))
        return out
    ranked = _apply_tie_break_sort(agg)
    ranked.insert(0, "Rank", range(1, len(ranked) + 1))
    return ranked


def _log_identity_coverage(agg: pd.DataFrame, label: str, log: Optional[Callable[[str], None]]) -> None:
    """Task 3's coverage report: how many identities in this table were keyed by the real LoginID
    versus fell back to the display name (no LoginID available -- always true for pre-2026-07-13
    data, and possible in principle if a future cache miss or normalization gap loses the id).
    Every fallback identity is named explicitly, not just counted, so a name-keyed row can never
    silently pass as equivalent to an id-keyed one.
    """
    if log is None or agg.empty:
        return
    is_fallback = agg["_Key"].astype(str).str.startswith("name:")
    n_id = int((~is_fallback).sum())
    n_name = int(is_fallback.sum())
    log(f"[league] {label} identity coverage: {n_id} keyed by LoginID, {n_name} fell back to display name")
    if n_name:
        fallback_names = sorted(agg.loc[is_fallback, "Pilot"].tolist())
        log(f"[league]   name-keyed (no LoginID) fallback pilot(s): {fallback_names}")


def build_season_table(
    results_dir: Path,
    season_start: date,
    season_end: date,
    as_of: date,
    snapshot_dir: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Rebuilt from scratch every call by reading every file in results_dir whose EventDate falls
    in [season_start, season_end] -- never an incremented running total, so a corrected event file
    propagates automatically. PrevRank/RankChange are computed live from the same raw files, with
    the baseline date picked by rank_change_anchor(as_of, coverage_end) -- see that function's own
    docstring for why this is a fixed weekly (Wednesday) boundary rather than a rolling "N days
    back" window, and why it freezes once a season has stopped receiving new events. No snapshot is
    involved in this pair; the returned table also carries the anchor actually used as
    table.attrs["rank_change_anchor"] (an ISO date string) so a caller never has to recompute or
    guess it.

    Grouped and ranked by identity (LoginID, falling back to display name -- see _identity_key),
    including for the PrevRank lookup: matching prev-vs-current by identity rather than by raw
    Pilot string is what stops a rename inside the cutoff window from showing up as one pilot
    vanishing and a different one appearing from nowhere.

    DELTA_COLS (PrevPoints/DPoints/...) are a separate, snapshot-based pair: when *snapshot_dir* is
    given, the newest weekly snapshot older than isocalendar(as_of) is loaded (see
    _load_latest_snapshot_before) as the delta base; left None (no snapshot lookup at all, e.g. a
    read-only caller like league_site_export/pilot_identity_cli that doesn't want the stderr noise
    of a missing-snapshot warning), or when no snapshot exists yet, every DELTA_COLS cell stays
    blank -- never 0, since 0 is a legitimate delta.
    """
    all_results = load_all_league_results(results_dir)
    if all_results.empty:
        return pd.DataFrame(columns=PILOT_TABLE_COLS)

    dates = pd.to_datetime(all_results["EventDate"], errors="coerce").dt.date
    season_mask = (dates >= season_start) & (dates <= season_end)
    season_mask = season_mask.fillna(False)

    current_results = all_results[season_mask]
    current_agg = aggregate_pilot_table(current_results)
    _log_identity_coverage(current_agg, "current", log)
    current_ranked = _rank_table(current_agg)
    if current_ranked.empty:
        return pd.DataFrame(columns=PILOT_TABLE_COLS)

    event_dates = pd.to_datetime(current_results["EventDate"], errors="coerce").dt.date.dropna()
    coverage_end = event_dates.max() if not event_dates.empty else None
    anchor = rank_change_anchor(as_of, coverage_end)
    prev_mask = season_mask & (dates < anchor)
    prev_results = all_results[prev_mask]
    prev_ranked = _rank_table(aggregate_pilot_table(prev_results))
    prev_rank_lookup = (
        prev_ranked.set_index("_Key")["Rank"] if not prev_ranked.empty else pd.Series(dtype="int64")
    )

    current_ranked["PrevRank"] = current_ranked["_Key"].map(prev_rank_lookup).astype("Int64")
    current_ranked["RankChange"] = (current_ranked["PrevRank"] - current_ranked["Rank"]).astype("Int64")

    base_snapshot = None
    if snapshot_dir is not None:
        season_name, _s, _e = season_for_date(season_start)
        base_snapshot, _base_week = _load_latest_snapshot_before(
            snapshot_dir, season_name, _iso_week(as_of), log=log
        )
    current_ranked = _apply_deltas(current_ranked, base_snapshot)

    # Set on the object actually being returned, not earlier -- pandas' .attrs propagation through
    # intermediate operations (_apply_deltas, the column-selection below) is not guaranteed across
    # pandas versions, so this assigns it last rather than relying on it surviving the trip.
    result = current_ranked[PILOT_TABLE_COLS]
    result.attrs["rank_change_anchor"] = anchor.isoformat()
    return result


def write_season_league_csv(league_dir: Path, season: str, table: pd.DataFrame) -> Path:
    path = league_dir / f"pilot_league_{season_filename_slug(season)}.csv"
    _write_csv_lf(table, path)
    return path


# --- Weekly snapshots (delta base for PrevPoints/DPoints/...) -----------------------------------

_SNAPSHOT_WEEK_RE = re.compile(r"_w(\d+)\.csv$")


def _iso_week(d: date) -> int:
    """ISO week number, same convention compute_run_dir uses for the WNN_ run-folder prefix in
    metagame_input_generator.py -- reusing it here keeps "week N" meaning one thing project-wide."""
    return d.isocalendar()[1]


def _snapshot_path(snapshot_dir: Path, season: str, week: int) -> Path:
    return snapshot_dir / f"pilot_league_{season_filename_slug(season)}_w{week:02d}.csv"


def write_weekly_snapshot(
    snapshot_dir: Path,
    season: str,
    as_of: date,
    table: pd.DataFrame,
    today: Optional[date] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[Path]:
    """Writes the weekly snapshot for *season* at ISO week isocalendar(as_of), keyed by LoginID.

    Overwritable only for the CURRENT ISO week: a second run later the same week (e.g. Wednesday,
    then again Friday) legitimately has more events on disk by then, and it -- not the Wednesday
    run -- should become next week's delta base, so it overwrites and logs
    "snapshot for week <N> overwritten". A snapshot for any earlier ISO week is frozen: writing to
    it raises FileExistsError. "Current" is judged against *today* (real wall-clock date,
    defaulting to date.today() when not given) rather than *as_of* itself, so a deliberately
    backdated as_of -- a historical backfill run -- is still correctly judged frozen against the
    real calendar instead of always comparing equal to its own week.

    Returns None (writes nothing) when *table* is empty or every row is name-keyed (no LoginID --
    pre-2026-07-13 fallback identity, see _identity_key), since LoginID is the only key a delta can
    reliably track across weeks.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)
        else:
            print(msg)

    if table.empty:
        return None
    week = _iso_week(as_of)
    week_today = _iso_week(today if today is not None else date.today())
    path = _snapshot_path(snapshot_dir, season, week)
    if path.exists():
        if week == week_today:
            emit(f"snapshot for week {week} overwritten")
        else:
            raise FileExistsError(
                f"[league] snapshot already exists for {season} week {week}: {path} -- this is an "
                f"earlier ISO week than today's (week {week_today}), so it is frozen; only the "
                f"current week's snapshot may be overwritten"
            )
    snap = table[table["LoginID"].astype(str).str.strip() != ""].copy()
    if snap.empty:
        return None
    out = snap[SNAPSHOT_COLS].copy()
    for col in SNAPSHOT_COLS:
        if col != "LoginID":
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    _write_csv_lf(out, path)
    return path


def _load_latest_snapshot_before(
    snapshot_dir: Path, season: str, week: int, log: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[int]]:
    """Finds the newest snapshot for *season* with a week number strictly less than *week* and
    loads it indexed by LoginID. Returns (None, None) when no such snapshot exists -- printing the
    required one-line stderr message rather than inventing a zero-filled base, since zero is a
    legitimate delta value and must never stand in for "no prior data".
    """
    slug = season_filename_slug(season)
    best_week: Optional[int] = None
    best_path: Optional[Path] = None
    if snapshot_dir.exists():
        for p in snapshot_dir.glob(f"pilot_league_{slug}_w*.csv"):
            m = _SNAPSHOT_WEEK_RE.search(p.name)
            if not m:
                continue
            w = int(m.group(1))
            if w < week and (best_week is None or w > best_week):
                best_week, best_path = w, p
    if best_path is None:
        print(f"no snapshot for week {week - 1}, deltas empty", file=sys.stderr)
        return None, None
    df = pd.read_csv(best_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if df.empty:
        print(f"no snapshot for week {week - 1}, deltas empty", file=sys.stderr)
        return None, None
    for col in SNAPSHOT_COLS:
        if col != "LoginID":
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["LoginID"] = df["LoginID"].astype(str).str.strip()
    df = df.set_index("LoginID", drop=False)
    return df, best_week


def _apply_deltas(current_ranked: pd.DataFrame, base_snapshot: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Adds DELTA_COLS + PrevPremierPoints to an already-ranked season table. Every cell stays
    pandas NA (blank in the CSV) for a pilot absent from *base_snapshot* -- no snapshot at all, or
    a debutant identity this week -- rather than 0, since 0 is a legitimate delta and must not be
    mistaken for "no prior data" (PrevRank/RankChange already follow this same blank-not-zero rule).
    """
    out = current_ranked.copy()
    extra_cols = DELTA_COLS + ["PrevPremierPoints"]
    for col in extra_cols:
        out[col] = pd.array([pd.NA] * len(out), dtype="Int64")

    if base_snapshot is None or base_snapshot.empty:
        return out

    for idx, row in out.iterrows():
        lid = str(row.get("LoginID", "")).strip()
        if not lid or lid not in base_snapshot.index:
            continue
        base = base_snapshot.loc[lid]
        if isinstance(base, pd.DataFrame):
            raise AssertionError(f"[league] snapshot has duplicate LoginID {lid}, cannot compute delta")

        prev_points, prev_starts = int(base["Points"]), int(base["Starts"])
        prev_top16, prev_top8 = int(base["Top16"]), int(base["Top8"])
        prev_top4, prev_top2 = int(base["Top4"]), int(base["Top2"])
        prev_wins = int(base["Wins"])
        prev_premier = int(base["PremierPoints"])

        out.at[idx, "PrevPoints"] = prev_points
        out.at[idx, "PrevStarts"] = prev_starts
        out.at[idx, "PrevTop16"] = prev_top16
        out.at[idx, "PrevTop8"] = prev_top8
        out.at[idx, "PrevTop4"] = prev_top4
        out.at[idx, "PrevTop2"] = prev_top2
        out.at[idx, "PrevWins"] = prev_wins
        out.at[idx, "PrevPremierPoints"] = prev_premier

        out.at[idx, "DPoints"] = int(row["Points"]) - prev_points
        out.at[idx, "DStarts"] = int(row["Starts"]) - prev_starts
        out.at[idx, "DTop16"] = int(row["Top16"]) - prev_top16
        out.at[idx, "DTop8"] = int(row["Top8"]) - prev_top8
        out.at[idx, "DTop4"] = int(row["Top4"]) - prev_top4
        out.at[idx, "DTop2"] = int(row["Top2"]) - prev_top2
        out.at[idx, "DWins"] = int(row["Wins"]) - prev_wins

    return out


def _remigrate_snapshot_ranks(snapshot_dir: Path, log: Optional[Callable[[str], None]] = None) -> dict:
    """One-time migration: recomputes Rank inside every existing weekly snapshot with the CURRENT
    tie-break rule (_apply_tie_break_sort, see _rank_table's docstring for the rule itself) and
    overwrites the snapshot in place -- deliberately bypassing write_weekly_snapshot's immutability
    guard, since a controlled, one-time rewrite of already-published snapshots is exactly what this
    migration is for.

    Needed because the tie-break rule changed: re-ranking with the new rule moves most pilots' Rank
    even though their Points/Wins/Top2/.../Starts values are all unchanged. Without this migration,
    the next production run would diff a freshly-built (new-rule) table's Rank against an old-rule
    snapshot's Rank and report a false wave of RankChange movement across the whole table.

    Logs the number of snapshots migrated and the number of rows whose Rank value actually changed.
    That row-changed count is purely informational, printed for visibility only -- it is NOT a
    stable, assertable quantity. It depends on the OLD rule's tie-break order among fully-tied rows,
    which was itself dependent on whatever input order happened to produce that specific snapshot;
    the same recomputation over the same underlying Points/Wins/.../Starts values can legitimately
    report a different changed-row count against a different (differently-ordered) prior snapshot
    of the old rule's output. Never assert on this number.

    Prints "no snapshots to remigrate" and does nothing if *snapshot_dir* has no snapshot files.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)
        else:
            print(msg)

    paths = sorted(snapshot_dir.glob("pilot_league_*_w*.csv")) if snapshot_dir.exists() else []
    if not paths:
        emit("no snapshots to remigrate")
        return {"snapshots_migrated": 0, "rows_changed": 0}

    snapshots_migrated = 0
    rows_changed = 0
    for p in paths:
        df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        if df.empty:
            snapshots_migrated += 1
            continue
        df["LoginID"] = df["LoginID"].astype(str).str.strip()
        for col in SNAPSHOT_COLS:
            if col != "LoginID":
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        old_rank_by_lid = dict(zip(df["LoginID"], df["Rank"]))
        reranked = _apply_tie_break_sort(df)
        reranked["Rank"] = range(1, len(reranked) + 1)
        changed_here = int((reranked["Rank"] != reranked["LoginID"].map(old_rank_by_lid)).sum())

        out = reranked[SNAPSHOT_COLS]
        _write_csv_lf(out, p)  # bypasses write_weekly_snapshot's overwrite guard on purpose
        rows_changed += changed_here
        snapshots_migrated += 1

    emit(
        f"[league] remigrated {snapshots_migrated} snapshot(s), {rows_changed} row(s) with a "
        f"changed Rank (informational count only -- see docstring, not stable/assertable)"
    )
    return {"snapshots_migrated": snapshots_migrated, "rows_changed": rows_changed}


# --- Assertions: fail loudly, print the offending value -----------------------------------------

def _fail(label: str, message: str, offending: object) -> None:
    print(f"[league][invariant] ASSERTION FAILED ({label}): {message} -- offending value: {offending!r}")
    raise AssertionError(f"{label}: {message} (offending value: {offending!r})")


def _validate_num(v: object) -> Optional[int]:
    """Coerces a table cell (python int/float, numpy scalar, pandas NA, or CSV-round-tripped
    string/empty-string) to int, or None for anything that means "blank" -- used throughout
    validate_league so it works identically whether *rows* came straight from build_season_table's
    DataFrame or from re-reading an already-written pilot_league_*.csv."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if v == "":
        return None
    return int(float(v))


def validate_league(rows, n_events: int, n_premier_events: int) -> None:
    """Hard invariants for a rebuilt season table -- run immediately before writing the CSV (see
    run_league_update). Every violation raises AssertionError naming the offending LoginID and
    values; there are no warnings and no silent pass-through here, by design (see this feature's
    task doc -- turning any of these into a warning defeats the point of a hard invariant).

    *rows* is the season table, either a DataFrame or an iterable of row-dicts (e.g.
    table.to_dict("records"), or the result of pd.read_csv(pilot_league_<season>.csv) -- both are
    accepted so this can validate either a freshly-built table or one already round-tripped through
    CSV). *n_events* is the season's total event count across both EventClasses; *n_premier_events*
    is the Premier-only subset -- both counted the same way check_league_invariants already does
    (season_results["EventID"].nunique()).

    Check 14 re-derives the FULL 9-key tie-break chain from zero (see _rank_table's docstring for
    the rule; _full_tie_break_key here is an independent re-implementation of it, not a call into
    _apply_tie_break_sort) and sorts *rows* by it, then compares position-by-position against the
    given Rank column. Every one of the 9 keys is present here, so a stable sort's tie-preservation
    only ever matters for a row-pair genuinely tied on all 9 -- a real, unresolved tie (see
    _rank_table), not an untested gap in this check's own key like the previous (Points, Wins,
    Top2)-only version had.

    Checks 15-16 (delta sanity) run only for a row that actually has a snapshot base (PrevPoints
    not blank) -- a debutant, or a season with no snapshot at all yet, has nothing to check there.
    """
    if isinstance(rows, pd.DataFrame):
        rows = rows.to_dict("records")
    rows = list(rows)
    if not rows:
        return

    num = _validate_num

    def lid_of(row: dict) -> str:
        return str(row.get("LoginID", "")).strip()

    # 1-3: per-pilot checks.
    for row in rows:
        lid = lid_of(row)
        top16, top8, top4, top2 = num(row.get("Top16")) or 0, num(row.get("Top8")) or 0, num(row.get("Top4")) or 0, num(row.get("Top2")) or 0
        wins, starts = num(row.get("Wins")) or 0, num(row.get("Starts")) or 0
        premier, points = num(row.get("PremierPoints")) or 0, num(row.get("Points")) or 0

        expected_points = top16 + top8 + top4 + top2 + wins + premier // 2
        if points != expected_points:
            _fail(
                "points formula",
                f"LoginID {lid}: Points={points} != Top16+Top8+Top4+Top2+Wins+PremierPoints//2={expected_points}",
                {"LoginID": lid, "Points": points, "expected": expected_points},
            )
        if premier % 2 != 0:
            _fail("premier points parity", f"LoginID {lid}: PremierPoints={premier} is odd", {"LoginID": lid, "PremierPoints": premier})
        if not (wins <= top2 <= top4 <= top8 <= top16 <= starts):
            _fail(
                "monotonic counts",
                f"LoginID {lid}: Wins<=Top2<=Top4<=Top8<=Top16<=Starts violated",
                {"LoginID": lid, "Wins": wins, "Top2": top2, "Top4": top4, "Top8": top8, "Top16": top16, "Starts": starts},
            )

    # 4: LoginID unique across the whole table.
    lids = [lid_of(r) for r in rows]
    dupes = sorted({x for x in lids if lids.count(x) > 1})
    if dupes:
        _fail("unique LoginID", "LoginID appears more than once in the table", dupes)

    # 5-12: global sums, all derived from event counts.
    def col_sum(col: str) -> int:
        return sum((num(r.get(col)) or 0) for r in rows)

    for col, expected in [
        ("Starts", 32 * n_events), ("Top16", 16 * n_events), ("Top8", 8 * n_events),
        ("Top4", 4 * n_events), ("Top2", 2 * n_events), ("Wins", 1 * n_events),
    ]:
        actual = col_sum(col)
        if actual != expected:
            _fail("season sum", f"sum({col})={actual} != {expected}", {"col": col, "actual": actual, "expected": expected})

    premier_sum = col_sum("PremierPoints")
    expected_premier_sum = 62 * n_premier_events
    if premier_sum != expected_premier_sum:
        _fail(
            "season sum (PremierPoints)",
            f"sum(PremierPoints)={premier_sum} != 62*{n_premier_events}={expected_premier_sum}",
            premier_sum,
        )

    points_sum = col_sum("Points")
    expected_points_sum = 31 * n_events + 31 * n_premier_events
    if points_sum != expected_points_sum:
        _fail(
            "season sum (Points)",
            f"sum(Points)={points_sum} != 31*{n_events}+31*{n_premier_events}={expected_points_sum}",
            points_sum,
        )

    # 13: Rank is exactly 1..len(rows), no gaps/duplicates.
    ranks = sorted(num(r.get("Rank")) for r in rows)
    expected_ranks = list(range(1, len(rows) + 1))
    if ranks != expected_ranks:
        _fail("rank sequence", "Rank column is not exactly 1..N with no gaps or duplicates", ranks)

    # 14: the FULL 9-key tie-break chain (see _rank_table's docstring for the rule itself),
    # re-derived here from zero rather than reused from _apply_tie_break_sort -- an independent
    # implementation of the same specification, so a bug in the production sort (wrong direction
    # on one key, a dropped key, ...) is actually caught instead of the check silently agreeing
    # with whatever _rank_table happened to produce. LoginID (key 9, ascending, blank sorts last)
    # makes every id-keyed pilot's position fully determined by keys 1-9, so stability now only
    # ever matters for a row-pair genuinely tied on all 9 -- two blank-LoginID name-fallback
    # identities, the one residual case _rank_table's docstring still calls a real, unresolved tie.
    def _full_tie_break_key(r: dict):
        points = num(r.get("Points")) or 0
        starts = num(r.get("Starts")) or 0
        if starts < 1:
            _fail(
                "starts floor",
                f"LoginID {lid_of(r)}: Starts={starts} < 1 -- a pilot only enters this table via "
                f"a Top32 appearance",
                {"LoginID": lid_of(r), "Starts": starts},
            )
        pts_per_start = Fraction(points, starts)
        try:
            lid_sort = (0, int(lid_of(r)))
        except ValueError:
            lid_sort = (1, 0)  # blank/name-fallback identity -- sorts after every real LoginID
        return (
            -points,
            -pts_per_start,
            -(num(r.get("Wins")) or 0),
            -(num(r.get("Top2")) or 0),
            -(num(r.get("Top4")) or 0),
            -(num(r.get("Top8")) or 0),
            -(num(r.get("Top16")) or 0),
            starts,
            lid_sort,
        )

    stable = sorted(rows, key=_full_tie_break_key)
    for i, r in enumerate(stable, start=1):
        actual_rank = num(r.get("Rank"))
        if actual_rank != i:
            _fail(
                "tie-break determinism",
                f"LoginID {lid_of(r)}: the full 9-key tie-break places this row at position {i} "
                f"but its Rank={actual_rank}",
                {"LoginID": lid_of(r), "position": i, "Rank": actual_rank},
            )

    # 15-16: delta checks, only for rows that actually have a snapshot base.
    for row in rows:
        prev_points = num(row.get("PrevPoints"))
        if prev_points is None:
            continue
        lid = lid_of(row)
        deltas = {
            "DPoints": num(row.get("DPoints")) or 0, "DStarts": num(row.get("DStarts")) or 0,
            "DTop16": num(row.get("DTop16")) or 0, "DTop8": num(row.get("DTop8")) or 0,
            "DTop4": num(row.get("DTop4")) or 0, "DTop2": num(row.get("DTop2")) or 0,
            "DWins": num(row.get("DWins")) or 0,
        }
        for name, val in deltas.items():
            if val < 0:
                _fail(
                    "delta non-negative",
                    f"LoginID {lid}: {name}={val} is negative -- past results disappeared",
                    {"LoginID": lid, name: val},
                )

        premier_now = num(row.get("PremierPoints")) or 0
        prev_premier = num(row.get("PrevPremierPoints"))
        if prev_premier is None:
            _fail(
                "delta formula",
                f"LoginID {lid}: has PrevPoints but no PrevPremierPoints -- snapshot delta is incomplete",
                {"LoginID": lid},
            )
        expected_d_points = (
            deltas["DTop16"] + deltas["DTop8"] + deltas["DTop4"] + deltas["DTop2"] + deltas["DWins"]
            + (premier_now - prev_premier) // 2
        )
        if deltas["DPoints"] != expected_d_points:
            _fail(
                "delta formula",
                f"LoginID {lid}: DPoints={deltas['DPoints']} != "
                f"DTop16+DTop8+DTop4+DTop2+DWins+(PremierPoints-PrevPremierPoints)//2={expected_d_points}",
                {"LoginID": lid, "DPoints": deltas["DPoints"], "expected": expected_d_points},
            )


def check_league_invariants(season_results_df: pd.DataFrame, league_table: pd.DataFrame) -> None:
    """The invariants that must hold on every rebuild, regardless of window/season:
      - sum(LeaguePoints) within any single Challenge event == 31
      - sum(LeaguePoints) within any single Premier event == 62
      - sum(LeaguePoints) over the season == 31*n_challenge + 62*n_premier for that season
      - sum(Points) in the league table == sum(LeaguePoints) across the season results
      - sum(PremierPoints) in the league table == 62*n_premier for that season
      - per pilot: Wins <= Top2 <= Top4 <= Top8 <= Top16 <= Starts
      - per pilot, Challenge-ladder points only (Points - PremierPoints) ==
        5*Wins + 4*(Top2-Wins) + 3*(Top4-Top2) + 2*(Top8-Top4) + 1*(Top16-Top8), computed against
        Challenge-only placement counts -- PremierPoints is checked separately above (as the
        doubled ladder, 62*n_premier in aggregate) since a pilot's Wins/Top2/.../Top16 columns
        count a placement once regardless of which ladder scored it, so mixing a premier win into
        this Challenge-ladder formula would silently overcount
      - no (EventID, Place) pair appears twice
      - no EventID appears with more than one EventClass
    Raises AssertionError immediately on the first violation found.
    """
    if season_results_df.empty:
        return

    work = season_results_df.copy()
    work["LeaguePoints"] = pd.to_numeric(work["LeaguePoints"], errors="coerce")
    work["Place"] = pd.to_numeric(work["Place"], errors="coerce")
    if "EventClass" not in work.columns:
        work["EventClass"] = "Challenge"
    work["EventClass"] = work["EventClass"].astype(str).str.strip()

    expected_per_event = {"Challenge": 31, "Premier": 62}
    per_event = work.groupby(["EventID", "EventClass"])["LeaguePoints"].sum()
    for (eid, cls), total in per_event.items():
        expected = expected_per_event.get(cls)
        if expected is not None and total != expected:
            _fail(
                "per-event points",
                f"sum(LeaguePoints) within a single {cls} event != {expected}",
                {"EventID": eid, "EventClass": cls, "total": total},
            )

    n_challenge = work[work["EventClass"] == "Challenge"]["EventID"].nunique()
    n_premier = work[work["EventClass"] == "Premier"]["EventID"].nunique()
    season_sum = int(work["LeaguePoints"].sum())
    expected_season_sum = 31 * n_challenge + 62 * n_premier
    if season_sum != expected_season_sum:
        _fail(
            "season points",
            f"sum(LeaguePoints) over season != 31*{n_challenge} + 62*{n_premier} = {expected_season_sum}",
            season_sum,
        )

    dupe = work.groupby(["EventID", "Place"]).size()
    dupe_bad = dupe[dupe > 1]
    if not dupe_bad.empty:
        _fail("duplicate placement", "(EventID, Place) pair appears more than once", dupe_bad.to_dict())

    class_by_event = work.groupby("EventID")["EventClass"].nunique()
    mixed = class_by_event[class_by_event > 1]
    if not mixed.empty:
        _fail("mixed event class", "EventID appears with more than one EventClass", mixed.index.tolist())

    if not league_table.empty:
        table_sum = int(pd.to_numeric(league_table["Points"], errors="coerce").sum())
        if table_sum != season_sum:
            _fail(
                "table vs results",
                "sum(Points) in league table != sum(LeaguePoints) across season results",
                {"table_sum": table_sum, "results_sum": season_sum},
            )

        table_premier_sum = int(pd.to_numeric(league_table["PremierPoints"], errors="coerce").sum())
        expected_premier_sum = 62 * n_premier
        if table_premier_sum != expected_premier_sum:
            _fail(
                "premier points total",
                f"sum(PremierPoints) in league table != 62*{n_premier} = {expected_premier_sum}",
                table_premier_sum,
            )

        bad_rows = league_table[
            (league_table["Wins"] > league_table["Top2"])
            | (league_table["Top2"] > league_table["Top4"])
            | (league_table["Top4"] > league_table["Top8"])
            | (league_table["Top8"] > league_table["Top16"])
            | (league_table["Top16"] > league_table["Starts"])
        ]
        if not bad_rows.empty:
            _fail(
                "monotonic counts",
                "Wins<=Top2<=Top4<=Top8<=Top16<=Starts violated",
                bad_rows[["Pilot", "Wins", "Top2", "Top4", "Top8", "Top16", "Starts"]].to_dict(orient="records"),
            )

        challenge_work = work[work["EventClass"] == "Challenge"].copy()
        challenge_work["_Key"] = [
            _identity_key(lid, p) for lid, p in zip(challenge_work.get("LoginID", ""), challenge_work.get("Pilot", ""))
        ]
        cgrp = challenge_work.groupby("_Key")
        c_wins = cgrp["Place"].apply(lambda s: int((s == 1).sum()))
        c_top2 = cgrp["Place"].apply(lambda s: int((s <= 2).sum()))
        c_top4 = cgrp["Place"].apply(lambda s: int((s <= 4).sum()))
        c_top8 = cgrp["Place"].apply(lambda s: int((s <= 8).sum()))
        c_top16 = cgrp["Place"].apply(lambda s: int((s <= 16).sum()))
        expected_challenge_points = (
            5 * c_wins + 4 * (c_top2 - c_wins) + 3 * (c_top4 - c_top2) + 2 * (c_top8 - c_top4) + 1 * (c_top16 - c_top8)
        )

        formula_bad = []
        for _, row in league_table.iterrows():
            key = _identity_key(row.get("LoginID", ""), row.get("Pilot", ""))
            challenge_points = int(row["Points"]) - int(row["PremierPoints"])
            expected = int(expected_challenge_points.get(key, 0))
            if challenge_points != expected:
                formula_bad.append(
                    {"Pilot": row["Pilot"], "challenge_points": challenge_points, "expected": expected}
                )
        if formula_bad:
            _fail(
                "points formula",
                "Points-PremierPoints != 5*Wins+4*(Top2-Wins)+3*(Top4-Top2)+2*(Top8-Top4)+1*(Top16-Top8) "
                "for Challenge-only placement counts",
                formula_bad,
            )


def check_premier_completeness(
    premier_history_csv: Optional[Path],
    results_dir: Path,
    format_name: str,
    season_start: date,
    season_end: date,
) -> None:
    """Every EventID in premier_history_modern.csv that falls inside [season_start, season_end]
    must appear exactly once under results_dir with EventClass == "Premier" -- catches a premier
    event silently dropped from sync (e.g. by a future filter regression) rather than letting the
    league table under-report without a trace.
    """
    if premier_history_csv is None or not premier_history_csv.exists():
        return
    hist = load_challenge_history(premier_history_csv)
    if hist.empty:
        return
    fmt_norm = normalize_name(format_name)
    hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
    if hist.empty:
        return
    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    in_season = (dates >= season_start) & (dates <= season_end)
    expected_ids = set(hist[in_season.fillna(False) & (hist["EventID"] != "")]["EventID"])
    if not expected_ids:
        return

    found_premier_ids: set = set()
    if results_dir.exists():
        for p in results_dir.glob("*.csv"):
            if p.stem not in expected_ids:
                continue
            try:
                df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            except Exception:
                continue
            if df.empty:
                continue
            classes = set(df.get("EventClass", pd.Series(dtype=str)).astype(str).str.strip())
            if classes == {"Premier"}:
                found_premier_ids.add(p.stem)

    missing = expected_ids - found_premier_ids
    if missing:
        _fail(
            "premier completeness",
            "premier EventID(s) in season missing from results (or not EventClass=Premier)",
            sorted(missing),
        )


def warn_if_partial_season(
    season: str,
    season_start: date,
    season_results: pd.DataFrame,
    log: Optional[Callable[[str], None]],
) -> None:
    """Warns (does not raise) whenever the earliest EventDate among a season's results is later
    than the season's calendar start -- i.e. this table does not yet cover the whole season and
    must not be mistaken for a complete one if left on disk (this is what makes the current partial
    Summer 2026 table, on disk from 2026-07-13 rather than the season's real 2026-06-01 start,
    printed as a warning on every run instead of silently looking final).
    """
    if log is None or season_results.empty:
        return
    dates = pd.to_datetime(season_results["EventDate"], errors="coerce").dt.date.dropna()
    if dates.empty:
        return
    earliest = dates.min()
    if earliest > season_start:
        log(
            f"[WARN] [league] {season} is PARTIAL: earliest event on disk is {earliest.isoformat()}, "
            f"season starts {season_start.isoformat()} -- do not treat this table as final"
        )


def check_season_tier_coverage(
    history_csv: Path,
    format_name: str,
    as_of: date,
    premier_ids: Optional[set] = None,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """Prints, per month of the season containing *as_of*, the number of Challenge events found
    per tier, and warns (does not raise) whenever a tier that had events in one month has zero in
    the next month of the same season. A missing tier is invisible in the league table itself --
    Starts/Points for that tier's pilots just look a little low, nothing crashes -- which is what
    let the June 2026 gap go unnoticed until a manual audit found it. This is meant to be read
    every week before committing, not just on failure, so it always prints the month/tier grid even
    when nothing looks wrong.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    season, season_start, season_end = season_for_date(as_of)
    hist = load_challenge_history(history_csv)
    if hist.empty:
        emit(f"[league] tier coverage check ({season}): history is empty, nothing to check")
        return

    fmt_norm = normalize_name(format_name)
    hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
    if hist.empty:
        emit(f"[league] tier coverage check ({season}): no history for format {format_name!r}")
        return

    premier_id_set = {str(x).strip() for x in (premier_ids or set())}
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["Tier"] = hist["Tier"].astype(str).str.strip()
    hist = hist[~hist["EventID"].isin(premier_id_set)]

    dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    window_end = min(season_end, as_of)
    in_season = (dates >= season_start) & (dates <= window_end)
    window = hist[in_season.fillna(False) & (hist["EventID"] != "")].copy()
    if window.empty:
        emit(f"[league] tier coverage check ({season}): no Challenge events with a resolved EventID yet")
        return

    window["Month"] = pd.to_datetime(window["EventDate"]).dt.to_period("M").astype(str)
    per_month_tier = window.groupby(["Month", "Tier"])["EventID"].nunique().unstack(fill_value=0)
    per_month_tier = per_month_tier.sort_index()

    emit(f"[league] tier coverage for {season} (Challenge events per tier, by month):")
    for month, row in per_month_tier.iterrows():
        parts = ", ".join(f"C{tier}={int(count)}" for tier, count in row.items())
        emit(f"[league]   {month}: {parts}")

    months = list(per_month_tier.index)
    for i in range(1, len(months)):
        prev_month, cur_month = months[i - 1], months[i]
        for tier in per_month_tier.columns:
            prev_count = per_month_tier.loc[prev_month, tier]
            cur_count = per_month_tier.loc[cur_month, tier]
            if prev_count > 0 and cur_count == 0:
                emit(
                    f"[WARN] [league] tier C{tier} had {int(prev_count)} event(s) in {prev_month} "
                    f"but 0 in {cur_month} -- check for a silent gap before committing"
                )


def assert_event_count_for_window(
    results_dir: Path,
    start_date: date,
    end_date: date,
    expected_total: int,
    expected_by_tier: Dict[str, int],
) -> None:
    """One-off completeness check for a specific window, e.g. verifying a known-good backfill
    target (23 events: 15 C64 + 5 C32 + 3 C96) actually landed on disk as expected.
    """
    ids_by_tier: Dict[str, set] = {}
    if results_dir.exists():
        for p in results_dir.glob("*.csv"):
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            if df.empty:
                continue
            d = pd.to_datetime(df["EventDate"].iloc[0], errors="coerce")
            if pd.isna(d) or not (start_date <= d.date() <= end_date):
                continue
            tier_label = str(df["Tier"].iloc[0])
            tier = tier_label[1:] if tier_label.startswith("C") else tier_label
            ids_by_tier.setdefault(tier, set()).add(str(df["EventID"].iloc[0]))

    total = sum(len(v) for v in ids_by_tier.values())
    if total != expected_total:
        _fail(
            "event count (window)",
            f"distinct EventIDs for {start_date}..{end_date} != {expected_total}",
            {"actual_total": total, "by_tier": {k: len(v) for k, v in ids_by_tier.items()}},
        )
    for tier, expected_n in expected_by_tier.items():
        actual_n = len(ids_by_tier.get(tier, set()))
        if actual_n != expected_n:
            _fail(
                "event count (tier)",
                f"distinct EventIDs for tier C{tier} in {start_date}..{end_date} != {expected_n}",
                actual_n,
            )


def assert_results_row_count_matches_tier_tables(
    results_dir: Path,
    start_date: date,
    end_date: date,
    tier_deck_tables: Dict[str, pd.DataFrame],
) -> None:
    """Challenge-only cross-check: results row count for a window == sum of Top32EntryCount across
    the per-tier deck tabs for that same window. Cross-checks the league's independent read of
    challenge_history_modern.csv against run_challenge_statistics()'s -- they must count the exact
    same underlying rows. Not meaningful once premier rows are mixed into results_dir for the same
    window; callers doing that comparison should filter to EventClass=="Challenge" rows first.
    """
    total_rows = 0
    if results_dir.exists():
        for p in results_dir.glob("*.csv"):
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            if df.empty:
                continue
            if "EventClass" in df.columns and str(df["EventClass"].iloc[0]).strip() == "Premier":
                continue
            d = pd.to_datetime(df["EventDate"].iloc[0], errors="coerce")
            if pd.isna(d) or not (start_date <= d.date() <= end_date):
                continue
            total_rows += len(df)

    expected = sum(
        int(t["Top32EntryCount"].sum()) for t in tier_deck_tables.values() if t is not None and not t.empty
    )
    if total_rows != expected:
        _fail(
            "row count vs tier tables",
            f"results row count for {start_date}..{end_date} != sum(Top32EntryCount)",
            {"results_rows": total_rows, "sum_top32_entry_count": expected},
        )


def run_league_update(
    history_csv: Path,
    league_dir: Path,
    format_name: str,
    start_date: date,
    end_date: date,
    as_of: date,
    premier_history_csv: Optional[Path] = None,
    mtgo_json_cache_dir: Optional[Path] = None,
    today: Optional[date] = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """One call = the full league update for a date range: backfill EventClass on any pre-Premier
    result files, sync per-event result files for both Challenge and Premier events (capturing
    Swiss standings + bracket matches alongside when *mtgo_json_cache_dir* is given -- see
    league_matches.py; data capture only, reads the already-cached mtgo.com JSON, no new network
    requests), rebuild and write the season table(s) touched, upsert season_config.csv, and run the
    standing invariants. This is the single entry point the weekly run and the backfill script both
    call -- there is only one code path that writes league data.

    *today* is passed through to write_weekly_snapshot as the real wall-clock date to judge
    overwrite-vs-frozen against (see its docstring); defaults to date.today() when not given. Kept
    as a separate parameter from *as_of* so a test (or a deliberately backdated backfill run) can
    control it without affecting anything else this function computes from *as_of*.

    Raises LeagueBlockingError (never swallowed by the generic Exception handlers callers may have
    around this function) if either validate_league or the weekly snapshot write fails -- both are
    treated as blocking because a season table that shipped with a broken invariant, or a week that
    silently ended up with no snapshot, needs the run's caller to notice and stop trusting a clean
    exit code. Every other failure in this function (check_league_invariants,
    check_premier_completeness, ...) still raises a plain AssertionError/Exception, unchanged.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    results_dir = league_dir / "results"
    matches_dir = league_dir / "matches"
    no_bracket_csv = matches_dir / "_no_bracket.csv"

    current_season_name, _current_season_start, _current_season_end = season_for_date(as_of)
    emit(f"[league] Current season (run date {as_of.isoformat()}): {current_season_name} -- this is the table that gets published")

    premier_ids = _load_premier_event_ids(premier_history_csv)
    check_season_tier_coverage(history_csv, format_name, as_of, premier_ids=premier_ids, log=log)
    _log_premier_event_slugs(premier_history_csv, log)
    if detect_pilot_renames is not None and mtgo_json_cache_dir is not None:
        detect_pilot_renames(
            history_csv=history_csv, format_name=format_name,
            start_date=start_date, end_date=end_date,
            mtgo_json_cache_dir=mtgo_json_cache_dir, log=log,
        )
        if premier_history_csv is not None:
            detect_pilot_renames(
                history_csv=premier_history_csv, format_name=format_name,
                start_date=start_date, end_date=end_date,
                mtgo_json_cache_dir=mtgo_json_cache_dir, log=log,
            )
    backfill_event_class(results_dir, log=log)

    existing_before = {p.stem for p in results_dir.glob("*.csv")} if results_dir.exists() else set()

    written_challenge = sync_challenge_league_results(
        history_csv=history_csv,
        results_dir=results_dir,
        format_name=format_name,
        start_date=start_date,
        end_date=end_date,
        premier_ids=premier_ids,
        mtgo_json_cache_dir=mtgo_json_cache_dir,
        matches_dir=matches_dir,
        no_bracket_csv=no_bracket_csv,
        log=log,
    )
    written_premier = sync_premier_league_results(
        premier_history_csv=premier_history_csv,
        results_dir=results_dir,
        format_name=format_name,
        start_date=start_date,
        end_date=end_date,
        mtgo_json_cache_dir=mtgo_json_cache_dir,
        matches_dir=matches_dir,
        no_bracket_csv=no_bracket_csv,
        log=log,
    )
    written = written_challenge + written_premier
    emit(
        f"[league] synced {len(written_challenge)} Challenge + {len(written_premier)} Premier "
        f"event result file(s) for {start_date.isoformat()}..{end_date.isoformat()}"
    )

    if mtgo_json_cache_dir is not None and written:
        with_bracket = 0
        without_bracket = 0
        not_scanned = 0
        for eid in written:
            mp = matches_dir / f"{eid}.csv"
            if not mp.exists():
                not_scanned += 1
                continue
            mdf = pd.read_csv(mp, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            if mdf.empty:
                without_bracket += 1
            else:
                with_bracket += 1
        emit(
            f"[league-matches] {with_bracket} event(s) with a bracket, {without_bracket} without, "
            f"{not_scanned} not scanned (no cached JSON) -- out of {len(written)} synced this run"
        )

    class_by_eid = {eid: "Challenge" for eid in written_challenge}
    class_by_eid.update({eid: "Premier" for eid in written_premier})
    newly_added = [eid for eid in written if eid not in existing_before]
    overwritten = [eid for eid in written if eid in existing_before]
    emit(
        f"[league] Added this run ({len(newly_added)}): "
        + (", ".join(f"{eid} ({class_by_eid[eid]})" for eid in newly_added) if newly_added else "none")
    )
    emit(
        f"[league] Already present, overwritten ({len(overwritten)}): "
        + (", ".join(f"{eid} ({class_by_eid[eid]})" for eid in overwritten) if overwritten else "none")
    )

    all_results = load_all_league_results(results_dir)
    written_set = set(written)
    seasons_touched: Dict[str, Tuple[date, date]] = {}
    if written_set and not all_results.empty:
        written_dates = pd.to_datetime(
            all_results[all_results["EventID"].isin(written_set)]["EventDate"], errors="coerce"
        ).dt.date.dropna().unique()
        for d in written_dates:
            name, s, e = season_for_date(d)
            seasons_touched[name] = (s, e)

    summary = {"written_event_ids": written, "seasons": {}}
    snapshot_dir = league_dir / "snapshots"
    for season, (s, e) in sorted(seasons_touched.items(), key=lambda kv: kv[1][0]):
        upsert_season_config(league_dir / "season_config.csv", season, s, e)
        table = build_season_table(
            results_dir, s, e, as_of=as_of, snapshot_dir=snapshot_dir, log=log,
        )

        dates = pd.to_datetime(all_results["EventDate"], errors="coerce").dt.date
        season_results = all_results[((dates >= s) & (dates <= e)).fillna(False)]
        check_league_invariants(season_results, table)

        n_events_season = season_results["EventID"].nunique()
        n_premier_season = season_results[
            season_results.get("EventClass", pd.Series(dtype=str)).astype(str).str.strip() == "Premier"
        ]["EventID"].nunique()
        try:
            validate_league(table, n_events_season, n_premier_season)
        except AssertionError as exc:
            raise LeagueBlockingError(
                f"validate_league failed for {season}: {exc}"
            ) from exc

        # Completeness is checked only for the slice of the season this call actually synced
        # (start_date..end_date intersected with the season), not the whole season -- the design
        # is incremental (each weekly run syncs its own window; earlier weeks' events may simply
        # not be on disk yet in a season still being backfilled run by run), so asserting full-season
        # completeness on every partial run would fail every week until the season is complete.
        check_premier_completeness(
            premier_history_csv, results_dir, format_name, max(s, start_date), min(e, end_date)
        )

        warn_if_partial_season(season, s, season_results, log)

        path = write_season_league_csv(league_dir, season, table)
        try:
            snap_path = write_weekly_snapshot(snapshot_dir, season, as_of, table, today=today, log=log)
        except FileExistsError as exc:
            raise LeagueBlockingError(
                f"league snapshot not written for week {_iso_week(as_of)} ({season}): {exc} -- "
                f"next week's deltas will be empty"
            ) from exc
        if snap_path is not None:
            emit(f"[league] wrote weekly snapshot for {season} -> {snap_path}")
        total_points_season = int(pd.to_numeric(table["Points"], errors="coerce").sum()) if not table.empty else 0
        current_tag = " (current)" if season == current_season_name else ""
        emit(
            f"[league] {season}{current_tag}: {n_events_season} event(s), {len(table)} pilot(s), "
            f"{total_points_season} total points -> {path}"
        )

        top5 = table.head(5)
        if not top5.empty:
            emit(f"[league] {season} top 5:")
            for _, row in top5.iterrows():
                rc = row.get("RankChange")
                if pd.isna(rc):
                    rc_str = "new"
                else:
                    rc_int = int(rc)
                    rc_str = f"+{rc_int}" if rc_int > 0 else str(rc_int)
                login_id = str(row.get("LoginID", "")).strip()
                emit(
                    f"[league]   {int(row['Rank'])}. {row['Pilot']}"
                    f"{f' (LoginID {login_id})' if login_id else ' (no LoginID, name-keyed)'}"
                    f" -- {int(row['Points'])} pts (PremierPoints {int(row['PremierPoints'])}, rank change {rc_str})"
                )

        summary["seasons"][season] = {"table_path": path, "pilots": len(table)}

    return summary
