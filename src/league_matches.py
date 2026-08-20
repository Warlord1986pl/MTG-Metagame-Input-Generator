"""Swiss standings + elimination bracket capture, a third consumer of data the pipeline already
fetches and durably caches: challenge_mtgo_source.fetch_mtgo_event_json() saves the raw
window.MTGO.decklists.data JSON blob for every Challenge/premier event to
outputs/cache/mtgo_json/<format>/<EventID>.json before this module ever runs. This module only
reads that cache -- it never fetches anything itself, so capturing standings/bracket data adds
zero network requests beyond what the existing pipeline already makes.

Data capture only: nothing in this module feeds league scoring or the season table. It exists
because the source pages age out and can't be recovered later, not because anything consumes the
data yet.

Correlation is entirely LoginID-based, entirely within the JSON. standings[], decklists[],
final_rank[] and every brackets[].matches[].players[] entry all carry loginid -- the stable
per-account id -- so cross-referencing "is this bracket player the Place-1 finisher" never needs
to go anywhere near history's Pilot column, which is a frozen display name that can legitimately
differ from whatever the JSON currently shows (see CLAUDE.md: mtgo.com/MTGGoldfish rewrite names
retroactively). WinnerPilot/LoserPilot in the output are display labels only, taken straight from
the JSON entry itself so they're always internally consistent with the LoginID beside them --
never from history. A name-based fallback exists for the rare JSON entry that genuinely lacks a
loginid (see _resolve_loginid); every use of it is counted so a caller can report how often it
actually fires.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    from challenge_history_engine import normalize_name
except ImportError:
    from .challenge_history_engine import normalize_name


def _fail(label: str, message: str, offending: object) -> None:
    """Same contract as league_engine._fail (duplicated, not imported, to avoid a circular import
    -- league_engine imports this module at load time, before any of its own names exist yet)."""
    print(f"[league-matches][invariant] ASSERTION FAILED ({label}): {message} -- offending value: {offending!r}")
    raise AssertionError(f"{label}: {message} (offending value: {offending!r})")


def _write_csv_lf(df: pd.DataFrame, path: Path) -> None:
    """Same contract as league_engine._write_csv_lf (duplicated for the same reason as _fail
    above): UTF-8 without BOM, LF line endings via lineterminator, regardless of platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        df.to_csv(f, index=False, lineterminator="\n")


MATCHES_COLS: List[str] = [
    "EventID", "EventDate", "Tier", "EventClass", "Round",
    "WinnerPilot", "WinnerLoginID", "LoserPilot", "LoserLoginID",
    "WinnerGames", "LoserGames", "WinnerDeck", "LoserDeck",
]

NO_BRACKET_COLS: List[str] = ["EventID", "EventDate", "Reason"]

SWISS_FIELDS = ["SwissRank", "SwissPoints", "OMWP", "GWP", "OGWP"]

_BRACKET_ROUND_BY_INDEX = {2: "QF", 1: "SF", 0: "F"}
_EXPECTED_ROUND_COUNTS = {"QF": 4, "SF": 2, "F": 1}


def load_mtgo_event_json(cache_dir: Optional[Path], event_id: str) -> Optional[dict]:
    """Reads the already-cached raw event JSON. Never fetches -- a cache miss means the scan
    cannot run for this event, not that a request should be made."""
    if cache_dir is None:
        return None
    for name in (f"{event_id}.json", f"{event_id}.0.json"):
        p = cache_dir / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def swiss_rounds_for_event(data: dict) -> Optional[int]:
    """Swiss-only round count, derived from data already in the blob rather than guessed from a
    field-size table: final_rank[].roundnumber is the TOTAL rounds played (Swiss + the 3 fixed
    playoff rounds), constant across every row of one event. Subtracting 3 gives Swiss rounds.
    """
    rounds = {
        str(r.get("roundnumber")) for r in data.get("final_rank", []) if r.get("roundnumber") is not None
    }
    if len(rounds) != 1:
        return None
    try:
        total = int(next(iter(rounds)))
    except ValueError:
        return None
    swiss = total - 3
    return swiss if swiss > 0 else None


def _loginid_by_name_from_decklists(data: dict) -> Dict[str, str]:
    """normalize_name(player) -> loginid, built from decklists[] -- which, like standings[],
    final_rank[] and every bracket player entry, carries loginid directly. This exists purely as
    a fallback resource for the rare case some OTHER section's entry lacks a loginid but has a
    name; decklists is simply the most reliably-populated section to borrow one from. JSON-
    internal only -- never touches history.
    """
    out: Dict[str, str] = {}
    for d in data.get("decklists", []):
        lid = str(d.get("loginid", "")).strip()
        name = str(d.get("player", "")).strip()
        if lid and name:
            out[normalize_name(name)] = lid
    return out


def _resolve_loginid(
    raw_loginid: object, name: str, loginid_by_name: Dict[str, str], fallback_counter: List[int]
) -> str:
    """Prefers the JSON entry's own loginid. Falls back to a name-based lookup against
    decklists[] (see _loginid_by_name_from_decklists -- itself JSON-internal, never history) only
    when the entry genuinely has no loginid of its own. Every fallback use increments
    fallback_counter[0], so a caller can report exactly how often this path fires.
    """
    lid = str(raw_loginid or "").strip()
    if lid:
        return lid
    fallback_counter[0] += 1
    return loginid_by_name.get(normalize_name(name), "")


def standings_lookup(
    data: dict,
    loginid_by_name: Optional[Dict[str, str]] = None,
    fallback_counter: Optional[List[int]] = None,
) -> Dict[str, dict]:
    """LoginID -> {SwissRank, SwissPoints, OMWP, GWP, OGWP, PilotName}, values stored as the raw
    published strings (never rounded, never re-derived). Keyed by LoginID, not by login_name, so a
    caller can correlate this against other loginid-keyed JSON sections (final_rank, bracket
    players) even when the display name has drifted since this data was cached.
    """
    loginid_by_name = loginid_by_name or {}
    fallback_counter = fallback_counter if fallback_counter is not None else [0]
    out: Dict[str, dict] = {}
    for row in data.get("standings", []):
        pilot = str(row.get("login_name", "")).strip()
        loginid = _resolve_loginid(row.get("loginid"), pilot, loginid_by_name, fallback_counter)
        if not loginid:
            continue
        out[loginid] = {
            "SwissRank": str(row.get("rank", "")).strip(),
            "SwissPoints": str(row.get("score", "")).strip(),
            "OMWP": str(row.get("opponentmatchwinpercentage", "")).strip(),
            "GWP": str(row.get("gamewinpercentage", "")).strip(),
            "OGWP": str(row.get("opponentgamewinpercentage", "")).strip(),
            "PilotName": pilot,
        }
    return out


def _place_by_loginid(
    data: dict, loginid_by_name: Dict[str, str], fallback_counter: List[int]
) -> Dict[str, int]:
    """Place (the final post-bracket standing, 1..32) per LoginID, built entirely from this
    event's own final_rank[] -- the authoritative place source, independent of history and
    independent of any display name (final_rank carries no name field at all, unlike standings/
    decklists/bracket players). A final_rank entry with no loginid is therefore simply
    unresolvable (the name-based fallback has nothing to match against) and is skipped -- this has
    never been observed in practice across the whole cache.
    """
    out: Dict[str, int] = {}
    for row in data.get("final_rank", []):
        lid = str(row.get("loginid", "")).strip()
        if not lid:
            continue
        try:
            out[lid] = int(row.get("rank"))
        except (TypeError, ValueError):
            continue
    return out


def check_swiss_points_consistency(event_id: str, data: dict, log: Optional[Callable[[str], None]]) -> None:
    """Prints (does not raise) any pilot whose SwissPoints value cannot be produced by any
    combination of match wins (3 pts) and draws (1 pt) over this event's actual Swiss round count.
    An impossible value means a parsing error, not a real draw -- draws are legitimate (hence not
    simply requiring divisibility by 3), but not *any* number is reachable.
    """
    n = swiss_rounds_for_event(data)
    if n is None:
        if log:
            log(f"[WARN] [league-matches] {event_id}: could not determine Swiss round count, skipping SwissPoints consistency check")
        return
    valid = {3 * w + d for w in range(n + 1) for d in range(n - w + 1)}
    for row in data.get("standings", []):
        pilot = row.get("login_name", "")
        raw_score = row.get("score")
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            if log:
                log(f"[WARN] [league-matches] {event_id}: pilot {pilot!r} has non-numeric SwissPoints {raw_score!r}")
            continue
        if score not in valid:
            if log:
                log(
                    f"[WARN] [league-matches] {event_id}: pilot {pilot!r} SwissPoints={score} is not "
                    f"reachable via any wins/draws combination over {n} Swiss round(s) (range 0..{3 * n}) "
                    "-- looks like a parsing error"
                )


def check_standings_shape(event_id: str, data: dict, log: Optional[Callable[[str], None]]) -> None:
    """HARD checks (raise on violation): row count matches the field size (32, or fewer if the
    field itself had fewer entrants), SwissRank is 1..N with no gaps/duplicates, and every loginid
    in final_rank has a matching standings entry.

    Entirely JSON-internal. This used to compare the caller's history-derived pilot list against
    standings' login_names, which broke on an account rename -- final_rank and standings always
    agree with EACH OTHER (they're parts of the same already-fetched blob), but history's frozen
    Pilot name can legitimately differ from whatever the JSON currently shows. Comparing
    final_rank's own loginids against standings' own loginids means this check no longer depends
    on history at all.
    """
    standings = data.get("standings", [])
    player_count = data.get("player_count", {})
    try:
        field_size = int(player_count.get("players"))
    except (TypeError, ValueError):
        field_size = None

    expected_n = 32 if (field_size is None or field_size >= 32) else field_size
    if field_size is not None and field_size < 32 and log:
        log(f"[league-matches] {event_id}: field size {field_size} < 32 (from page header)")
    if len(standings) != expected_n:
        _fail(
            "standings row count",
            f"expected {expected_n} standings row(s), got {len(standings)}",
            {"EventID": event_id, "field_size": field_size},
        )

    ranks: List[int] = []
    for row in standings:
        try:
            ranks.append(int(row.get("rank")))
        except (TypeError, ValueError):
            _fail("standings rank parse", f"non-numeric rank for pilot {row.get('login_name')!r}", event_id)
    if sorted(ranks) != list(range(1, len(ranks) + 1)):
        _fail(
            "standings rank sequence",
            f"SwissRank values are not 1..{len(ranks)} without gaps or duplicates",
            {"EventID": event_id, "ranks": sorted(ranks)},
        )

    final_rank_loginids = {str(r.get("loginid", "")).strip() for r in data.get("final_rank", [])} - {""}
    standings_loginids = {str(s.get("loginid", "")).strip() for s in standings} - {""}
    missing = final_rank_loginids - standings_loginids
    if missing:
        _fail(
            "missing SwissRank",
            "loginid(s) in final_rank have no matching standings entry",
            {"EventID": event_id, "loginids": sorted(missing)},
        )


def extract_bracket_rows(
    data: dict, loginid_by_name: Dict[str, str], fallback_counter: List[int]
) -> Optional[List[dict]]:
    """Returns 7 rows (4 QF, 2 SF, 1 F, in that order) built from the bracket JSON, or None if
    the event has no bracket section at all (empty/missing "brackets" key) -- the empty-file-vs-
    missing-file distinction downstream depends on this returning None, not an empty list, for
    "no bracket published" (an empty list would mean "bracket present but somehow zero matches",
    which is a parsing failure the caller's structure check must catch, not a quiet no-op).

    WinnerPilot/LoserPilot are display labels taken directly from this bracket entry -- never from
    history -- so they're always internally consistent with WinnerLoginID/LoserLoginID beside
    them. LoginID prefers the entry's own loginid field; _resolve_loginid supplies the rare
    fallback.
    """
    brackets = data.get("brackets") or []
    if not brackets:
        return None
    by_index = {b.get("index"): b.get("matches", []) for b in brackets}
    rows: List[dict] = []
    for idx in (2, 1, 0):
        round_name = _BRACKET_ROUND_BY_INDEX[idx]
        for m in by_index.get(idx, []):
            players = m.get("players", [])
            if len(players) != 2:
                continue
            winner = next((p for p in players if p.get("winner")), None)
            loser = next((p for p in players if not p.get("winner")), None)
            if winner is None or loser is None:
                continue
            winner_name = str(winner.get("player", "")).strip()
            loser_name = str(loser.get("player", "")).strip()
            rows.append({
                "Round": round_name,
                "WinnerPilot": winner_name,
                "WinnerLoginID": _resolve_loginid(winner.get("loginid"), winner_name, loginid_by_name, fallback_counter),
                "LoserPilot": loser_name,
                "LoserLoginID": _resolve_loginid(loser.get("loginid"), loser_name, loginid_by_name, fallback_counter),
                "WinnerGames": winner.get("wins"),
                "LoserGames": loser.get("wins"),
                "WinnerSwissRank": winner.get("seeding"),
                "LoserSwissRank": loser.get("seeding"),
            })
    return rows


def check_bracket_invariants(
    event_id: str,
    rows: List[dict],
    place_by_loginid: Dict[str, int],
    swiss: Dict[str, dict],
    log: Optional[Callable[[str], None]],
) -> None:
    """HARD checks (raise): match counts per round, QF loginid set == Place 1-8, F winner/loser ==
    Place 1/2, SF losers == Place 3/4, WinnerGames > LoserGames always. All keyed on LoginID
    (place_by_loginid, from final_rank -- see _place_by_loginid) and cross-referenced against the
    bracket rows' own WinnerLoginID/LoserLoginID -- entirely JSON-internal, no history involved.

    SOFT checks (print only): WinnerGames/LoserGames not the usual 2-0 or 2-1, and the QF seeding
    pairing (1v8/4v5/3v6/2v7 by SwissRank) not holding -- both can legitimately happen (a bye, an
    irregular field) without indicating a parsing error.
    """
    by_round: Dict[str, List[dict]] = {"QF": [], "SF": [], "F": []}
    for r in rows:
        by_round.setdefault(r["Round"], []).append(r)
    actual_counts = {k: len(v) for k, v in by_round.items()}
    if actual_counts != _EXPECTED_ROUND_COUNTS or len(rows) != 7:
        _fail(
            "bracket match count",
            "expected 4 QF + 2 SF + 1 F = 7 matches",
            {"EventID": event_id, "actual": actual_counts, "total": len(rows)},
        )

    qf_loginids = {p for r in by_round["QF"] for p in (r["WinnerLoginID"], r["LoserLoginID"])}
    expected_qf_loginids = {lid for lid, pl in place_by_loginid.items() if pl <= 8}
    if qf_loginids != expected_qf_loginids:
        _fail(
            "QF pilot set",
            "the 8 QF loginids are not exactly the Place 1-8 loginids from final_rank",
            {"EventID": event_id, "qf_loginids": sorted(qf_loginids), "place_1_8": sorted(expected_qf_loginids)},
        )

    f_row = by_round["F"][0]
    if place_by_loginid.get(f_row["WinnerLoginID"]) != 1:
        _fail(
            "final winner",
            "final winner is not the Place 1 loginid",
            {"EventID": event_id, "winner": f_row["WinnerPilot"], "winner_loginid": f_row["WinnerLoginID"]},
        )
    if place_by_loginid.get(f_row["LoserLoginID"]) != 2:
        _fail(
            "final loser",
            "final loser is not the Place 2 loginid",
            {"EventID": event_id, "loser": f_row["LoserPilot"], "loser_loginid": f_row["LoserLoginID"]},
        )

    sf_losers = {r["LoserLoginID"] for r in by_round["SF"]}
    expected_sf_losers = {lid for lid, pl in place_by_loginid.items() if pl in (3, 4)}
    if sf_losers != expected_sf_losers:
        _fail(
            "SF losers",
            "SF losers are not exactly the Place 3 and 4 loginids",
            {"EventID": event_id, "sf_losers": sorted(sf_losers), "place_3_4": sorted(expected_sf_losers)},
        )

    for r in rows:
        try:
            wg, lg = int(r["WinnerGames"]), int(r["LoserGames"])
        except (TypeError, ValueError):
            _fail(
                "game score parse",
                f"non-numeric game score in {r['Round']} {r['WinnerPilot']} vs {r['LoserPilot']}",
                {"EventID": event_id, "WinnerGames": r["WinnerGames"], "LoserGames": r["LoserGames"]},
            )
            continue
        if not (wg > lg):
            _fail(
                "winner games > loser games",
                f"{r['Round']} {r['WinnerPilot']} {wg}-{lg} {r['LoserPilot']}",
                {"EventID": event_id},
            )
        if not (wg == 2 and lg in (0, 1)) and log:
            log(
                f"[league-matches] {event_id}: {r['Round']} unusual game score {wg}-{lg} "
                f"({r['WinnerPilot']} over {r['LoserPilot']})"
            )

    expected_pairs = {frozenset((1, 8)), frozenset((4, 5)), frozenset((3, 6)), frozenset((2, 7))}
    actual_pairs = set()
    seeding_parseable = True
    # LoginID-keyed: swiss (from standings_lookup) and the bracket rows both carry loginid
    # directly, so this needs no name matching at all, renamed account or not.
    for r in by_round["QF"]:
        wr = swiss.get(r["WinnerLoginID"], {}).get("SwissRank")
        lr = swiss.get(r["LoserLoginID"], {}).get("SwissRank")
        try:
            actual_pairs.add(frozenset((int(wr), int(lr))))
        except (TypeError, ValueError):
            seeding_parseable = False
    if not seeding_parseable or actual_pairs != expected_pairs:
        if log:
            log(
                f"[league-matches] {event_id}: QF seeding does not match the standard "
                f"1v8/4v5/3v6/2v7 pattern (by SwissRank) -- actual pairs: "
                f"{sorted(tuple(sorted(p)) for p in actual_pairs) if seeding_parseable else 'unparseable'}"
            )


def upsert_no_bracket_row(no_bracket_csv: Path, event_id: str, event_date: str, reason: str) -> None:
    """Idempotent by EventID, same pattern as league_engine.upsert_season_config."""
    if no_bracket_csv.exists():
        try:
            df = pd.read_csv(no_bracket_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            df = pd.DataFrame(columns=NO_BRACKET_COLS)
    else:
        df = pd.DataFrame(columns=NO_BRACKET_COLS)
    for col in NO_BRACKET_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[df["EventID"] != event_id]
    new_row = pd.DataFrame([{"EventID": event_id, "EventDate": event_date, "Reason": reason}])
    df = pd.concat([df[NO_BRACKET_COLS], new_row], ignore_index=True)
    df = df.sort_values("EventDate", kind="mergesort").reset_index(drop=True)
    _write_csv_lf(df, no_bracket_csv)


def write_matches_csv(
    matches_dir: Path, event_id: str, event_date: str, tier: str, event_class: str, rows: List[dict]
) -> Path:
    """Writes matches/<EventID>.csv -- header-only when *rows* is empty (confirmed no bracket),
    as distinct from the file not existing at all (scan never ran for this event)."""
    matches_dir.mkdir(parents=True, exist_ok=True)
    path = matches_dir / f"{event_id}.csv"
    out_rows = [
        {
            "EventID": event_id,
            "EventDate": event_date,
            "Tier": tier,
            "EventClass": event_class,
            "Round": r["Round"],
            "WinnerPilot": r["WinnerPilot"],
            "WinnerLoginID": r.get("WinnerLoginID", ""),
            "LoserPilot": r["LoserPilot"],
            "LoserLoginID": r.get("LoserLoginID", ""),
            "WinnerGames": r["WinnerGames"],
            "LoserGames": r["LoserGames"],
            "WinnerDeck": r.get("WinnerDeck", ""),
            "LoserDeck": r.get("LoserDeck", ""),
        }
        for r in rows
    ]
    df = pd.DataFrame(out_rows, columns=MATCHES_COLS) if out_rows else pd.DataFrame(columns=MATCHES_COLS)
    _write_csv_lf(df, path)
    return path


def detect_pilot_renames(
    history_csv: Path,
    format_name: str,
    start_date: date,
    end_date: date,
    mtgo_json_cache_dir: Optional[Path],
    log: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """INFORMATIONAL ONLY -- not a correctness check. The league groups pilots by LoginID (see
    league_engine._identity_key), which already makes an account rename a non-event for scoring
    and ranking: the two halves of a renamed account's record collapse into one identity
    regardless of what this function finds. What's left for this function to report is drift
    between the two *name* streams -- the frozen Pilot already in *history_csv* versus the name
    currently attached to that same placement in the cached mtgo.com JSON -- which is useful for
    the weekly write-up (a diff here is worth a human glance) and for noticing normalization drift
    between MTGGoldfish (most Challenge rows' Pilot source) and mtgo.com's JSON (the loginid
    source) independent of any real rename. Offline: reads only the already-cached JSON, makes no
    network request. Never raises, never blocks the run, never writes to history_csv.

    Placement comparison, not "does the other name appear anywhere in the event": an earlier
    attempt compared whether two names appeared anywhere in the same event and produced roughly
    65,000 false positives (any two of up to several hundred entrants in a shared field). Comparing
    at the exact placement is deterministic and found exactly the two real cases across the whole
    file (justAlice -> justFeather, MARZIANO -> surgetemelo).
    """
    if mtgo_json_cache_dir is None or not history_csv.exists():
        return []
    try:
        hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception:
        return []
    if hist.empty or "EventID" not in hist.columns:
        return []

    fmt_norm = normalize_name(format_name)
    hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
    hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    dates = pd.to_datetime(hist["EventDate"], errors="coerce").dt.date
    in_range = (dates >= start_date) & (dates <= end_date)
    window = hist[in_range.fillna(False) & (hist["EventID"] != "")]
    if window.empty:
        return []

    mismatches: List[dict] = []
    json_cache: Dict[str, Optional[dict]] = {}
    for eid, grp in window.groupby("EventID", sort=True):
        if eid not in json_cache:
            json_cache[eid] = load_mtgo_event_json(mtgo_json_cache_dir, eid)
        data = json_cache[eid]
        if data is None:
            continue
        event_date = str(grp["EventDate"].iloc[0])
        for _, row in grp.iterrows():
            place, hist_pilot = str(row["Place"]).strip(), str(row["Pilot"]).strip()
            rank_row = [fr for fr in data.get("final_rank", []) if fr.get("rank") == place]
            if not rank_row:
                continue
            loginid = rank_row[0]["loginid"]
            standing = [s for s in data.get("standings", []) if s.get("loginid") == loginid]
            cache_pilot = standing[0]["login_name"] if standing else None
            if cache_pilot is not None and cache_pilot != hist_pilot:
                mismatches.append({
                    "EventID": eid, "EventDate": event_date, "Place": place,
                    "HistoryPilot": hist_pilot, "CachePilot": cache_pilot,
                })

    if log is not None:
        if mismatches:
            log(
                f"[league-name-drift] {len(mismatches)} name difference(s) between history and the "
                f"cached JSON in this window -- informational, the league already keys on LoginID "
                f"so this does not affect scoring or ranking:"
            )
            for m in mismatches:
                log(
                    f"[league-name-drift]   {m['EventID']} {m['EventDate']} place {m['Place']}: "
                    f"history={m['HistoryPilot']!r} cache={m['CachePilot']!r}"
                )
        else:
            log(f"[league-name-drift] no name differences between history and the cached JSON in {start_date.isoformat()}..{end_date.isoformat()}")

    return mismatches


def process_event_standings_and_bracket(
    event_id: str,
    event_date: str,
    tier: str,
    event_class: str,
    deck_by_loginid: Dict[str, str],
    mtgo_json_cache_dir: Optional[Path],
    matches_dir: Path,
    no_bracket_csv: Path,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, dict]]:
    """One call = the full standings+bracket capture for one event: load the already-cached JSON,
    validate and return the LoginID -> SwissRank/SwissPoints/OMWP/GWP/OGWP lookup for the caller
    to merge into the results row (by LoginID, not Pilot), and write matches/<EventID>.csv (real
    rows, or header-only + a _no_bracket.csv entry when there's no bracket).

    All cross-referencing (standings vs final_rank vs bracket) happens entirely inside this one
    JSON blob, keyed on LoginID -- *deck_by_loginid* (from the caller's results rows, keyed by
    history's own LoginID column) is the only place history data enters this function at all, and
    only to attach a Deck label to a bracket row; it never participates in identity matching.

    Returns None -- and touches nothing under matches/ -- when there is no cached JSON at all,
    which means the scan could not run for this event (as opposed to having run and found no
    bracket). The caller should leave the five new results columns blank in that case.
    """
    data = load_mtgo_event_json(mtgo_json_cache_dir, event_id)
    if data is None:
        if log:
            log(f"[league-matches] {event_id}: no cached mtgo.com JSON found, skipping standings/matches capture")
        return None

    loginid_by_name = _loginid_by_name_from_decklists(data)
    fallback_counter = [0]

    swiss = standings_lookup(data, loginid_by_name, fallback_counter)
    check_standings_shape(event_id, data, log)
    check_swiss_points_consistency(event_id, data, log)

    bracket_rows = extract_bracket_rows(data, loginid_by_name, fallback_counter)
    if bracket_rows is None:
        write_matches_csv(matches_dir, event_id, event_date, tier, event_class, [])
        upsert_no_bracket_row(no_bracket_csv, event_id, event_date, "no bracket section in source data")
        if log:
            log(f"[league-matches] {event_id}: NO bracket published -- header-only matches file, registered in _no_bracket.csv")
        if fallback_counter[0] and log:
            log(f"[league-matches] {event_id}: name-based loginid fallback used {fallback_counter[0]} time(s)")
        return swiss

    for r in bracket_rows:
        r["WinnerDeck"] = deck_by_loginid.get(r["WinnerLoginID"], "")
        r["LoserDeck"] = deck_by_loginid.get(r["LoserLoginID"], "")

    place_by_loginid = _place_by_loginid(data, loginid_by_name, fallback_counter)
    check_bracket_invariants(event_id, bracket_rows, place_by_loginid, swiss, log)
    path = write_matches_csv(matches_dir, event_id, event_date, tier, event_class, bracket_rows)
    if log:
        log(f"[league-matches] {event_id}: {len(bracket_rows)} match(es) captured -> {path}")
    if fallback_counter[0] and log:
        log(f"[league-matches] {event_id}: name-based loginid fallback used {fallback_counter[0]} time(s)")
    return swiss
