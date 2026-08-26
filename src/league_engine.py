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
from datetime import date, timedelta
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

PILOT_TABLE_COLS: List[str] = [
    "Rank", "Pilot", "LoginID", "Points", "PremierPoints", "Wins", "Top2", "Top4", "Top8", "Top16",
    "Starts", "PrevRank", "RankChange",
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

    Also carries a hidden "_AvgFinish" column (mean Place across the identity's Top32 appearances)
    used only as a rank tie-break by _rank_table -- not part of PILOT_TABLE_COLS, stripped before
    the season table is written.
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
    avg_finish = grp["Place"].mean()

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
        "_AvgFinish": avg_finish.reindex(idx).values,
    })
    out["Points"] = out["Points"].astype(int)
    out["PremierPoints"] = out["PremierPoints"].astype(int)
    return out[["_Key"] + cols + ["_AvgFinish"]]


def _rank_table(agg: pd.DataFrame) -> pd.DataFrame:
    """Tie-break, applied in sequence: Points, Wins, Top2, Top4, Top8, Top16, Starts (more is
    better for all seven -- a pilot who has shown up to more events is not worse, on an equal
    points/placement record, than one who has shown up to fewer), then a BETTER (lower) average
    finish across that identity's Top32 appearances, then the displayed Pilot name ascending, then
    LoginID ascending as a final determinism guard.

    Average finish is deliberately second-to-last, after Starts and not before it: with several
    hundred pilots sitting on zero points, Starts is what actually separates them (more events
    entered, on an otherwise identical zero-point record, ranks higher) -- putting average finish
    ahead of Starts would instead favour a pilot with two lucky appearances over one who entered
    fifteen, which is the wrong story for a season-long table.

    The Pilot/LoginID pair (not Pilot alone) makes the ordering a total order -- deterministic
    across runs on identical data, and still deterministic if two different pilots happen to share
    a display name (LoginID breaks that tie; a name-keyed fallback row has no LoginID and sorts by
    the empty string, i.e. first among ties, which is fine since it's merely a tie-break, not a
    ranking signal). PremierPoints plays no part in the tie-break -- it is informational only,
    carried through unchanged.
    """
    if agg.empty:
        out = agg.copy()
        out.insert(0, "Rank", pd.Series(dtype=int))
        return out
    ranked = agg.sort_values(
        ["Points", "Wins", "Top2", "Top4", "Top8", "Top16", "Starts", "_AvgFinish", "Pilot", "LoginID"],
        ascending=[False, False, False, False, False, False, False, True, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
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
    prevrank_cutoff_days: int = 7,
    log: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Rebuilt from scratch every call by reading every file in results_dir whose EventDate falls
    in [season_start, season_end] -- never an incremented running total, so a corrected event file
    propagates automatically. PrevRank/RankChange are computed live from the same raw files with an
    earlier cutoff (as_of - prevrank_cutoff_days); no snapshot is ever stored.

    Grouped and ranked by identity (LoginID, falling back to display name -- see _identity_key),
    including for the PrevRank lookup: matching prev-vs-current by identity rather than by raw
    Pilot string is what stops a rename inside the cutoff window from showing up as one pilot
    vanishing and a different one appearing from nowhere.
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

    cutoff_date = as_of - timedelta(days=prevrank_cutoff_days)
    prev_mask = season_mask & (dates <= cutoff_date)
    prev_results = all_results[prev_mask]
    prev_ranked = _rank_table(aggregate_pilot_table(prev_results))
    prev_rank_lookup = (
        prev_ranked.set_index("_Key")["Rank"] if not prev_ranked.empty else pd.Series(dtype="int64")
    )

    current_ranked["PrevRank"] = current_ranked["_Key"].map(prev_rank_lookup).astype("Int64")
    current_ranked["RankChange"] = (current_ranked["PrevRank"] - current_ranked["Rank"]).astype("Int64")
    return current_ranked[PILOT_TABLE_COLS]


def write_season_league_csv(league_dir: Path, season: str, table: pd.DataFrame) -> Path:
    path = league_dir / f"pilot_league_{season_filename_slug(season)}.csv"
    _write_csv_lf(table, path)
    return path


# --- Assertions: fail loudly, print the offending value -----------------------------------------

def _fail(label: str, message: str, offending: object) -> None:
    print(f"[league][invariant] ASSERTION FAILED ({label}): {message} -- offending value: {offending!r}")
    raise AssertionError(f"{label}: {message} (offending value: {offending!r})")


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
    prevrank_cutoff_days: int = 7,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """One call = the full league update for a date range: backfill EventClass on any pre-Premier
    result files, sync per-event result files for both Challenge and Premier events (capturing
    Swiss standings + bracket matches alongside when *mtgo_json_cache_dir* is given -- see
    league_matches.py; data capture only, reads the already-cached mtgo.com JSON, no new network
    requests), rebuild and write the season table(s) touched, upsert season_config.csv, and run the
    standing invariants. This is the single entry point the weekly run and the backfill script both
    call -- there is only one code path that writes league data.
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
    for season, (s, e) in sorted(seasons_touched.items(), key=lambda kv: kv[1][0]):
        upsert_season_config(league_dir / "season_config.csv", season, s, e)
        table = build_season_table(results_dir, s, e, as_of=as_of, prevrank_cutoff_days=prevrank_cutoff_days, log=log)

        dates = pd.to_datetime(all_results["EventDate"], errors="coerce").dt.date
        season_results = all_results[((dates >= s) & (dates <= e)).fillna(False)]
        check_league_invariants(season_results, table)
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
        n_events_season = season_results["EventID"].nunique()
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
