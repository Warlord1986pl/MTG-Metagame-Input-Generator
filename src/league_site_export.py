"""Exports the pilot league to the JSON the static site under docs/ reads, as a fourth consumer of
outputs/league/results/<EventID>.csv (the same files league_engine.build_season_table reads --
this module never fetches anything and never touches outputs/league/ itself, it only reads what
run_league_update already wrote).

Two files per season, matching the site's own split (docs/index.html's season table view loads
only season_<slug>.json; a pilot profile then pulls its one entry out of pilots_<slug>.json):

  docs/data/season_<slug>.json   -- the season table: one row per Top32 pilot, rank + movement +
                                     the handful of counting stats the table displays, plus enough
                                     name history for the search box to match an old nickname.
  docs/data/pilots_<slug>.json   -- one entry per pilot, keyed by the same id season json uses for
                                     routing (LoginID, or "name:<display name>" for the rare
                                     pre-LoginID fallback identity -- see league_engine._identity_key).
                                     Carries the full per-event results table; the profile page
                                     computes every average (and its confidence interval) from this
                                     array client-side, so there is exactly one source of truth for
                                     "how many events does this average rest on" -- see
                                     docs/index.html's statsFromResults().

docs/data/seasons.json is a small manifest (season label, slug, start/end, whether it is the
season current as of the run) letting the site discover which season files exist without the
viewer having to know a season name in advance. Not mentioned by name in the site spec, but
necessary for the site to pick a default season to render.

Determinism (required: two back-to-back runs over the same on-disk results must produce
byte-identical docs/data/*.json): every list here is built from an already-deterministic upstream
order (league_engine._rank_table's tie-broken season table for the pilot list; explicit sort keys
for each pilot's results list) and every object is serialized with json.dumps(sort_keys=True,
indent=2) -- key order does not depend on dict-construction/iteration order even if that ever
changed upstream. Nothing here reads the wall clock; the only "current time" concept is *as_of*,
passed in by the caller (the same value the weekly run already threads through to
league_engine.run_league_update), not read fresh from inside this module.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    from league_engine import (
        LEAGUE_RESULTS_COLS,
        build_season_table,
        load_all_league_results,
        season_filename_slug,
        _identity_key,
    )
except ImportError:
    from .league_engine import (
        LEAGUE_RESULTS_COLS,
        build_season_table,
        load_all_league_results,
        season_filename_slug,
        _identity_key,
    )

try:
    import identity as pilot_identity
except ImportError:
    from . import identity as pilot_identity


def _write_json(obj: object, path: Path) -> None:
    """UTF-8, no BOM, LF line endings, sorted keys -- see module docstring on determinism."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(text)


def _event_label(tier: str, event_class: str) -> str:
    """Human-readable event name for the pilot results table. Challenge tiers ("C64") become
    "Modern Challenge 64"; premier rows carry their raw EventSlug in Tier (see
    sync_premier_league_results) and get title-cased word-by-word ("modern-rc-super-qualifier" ->
    "Modern Rc Super Qualifier" -> corrected acronym casing for the handful of known abbreviations).
    """
    tier = str(tier).strip()
    if event_class == "Challenge" and tier.startswith("C") and tier[1:].isdigit():
        return f"Modern Challenge {tier[1:]}"
    words = re.split(r"[-_\s]+", tier.strip()) if tier else ["Premier"]
    fixups = {"rc": "RC"}
    return " ".join(fixups.get(w.lower(), w.capitalize()) for w in words if w)


def _to_float_or_none(value: object) -> Optional[float]:
    s = str(value).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int_or_none(value: object) -> Optional[int]:
    f = _to_float_or_none(value)
    return None if f is None else int(round(f))


def _pilot_key(login_id: str, name: str) -> str:
    """Same "id:"/"name:" scheme as league_engine._identity_key, used verbatim as the site's
    routing id (#/pilot/<key>) and as the pilots_<slug>.json lookup key, so a name-keyed fallback
    identity (no LoginID captured) can never collide with a real LoginID that happens to look like
    a name.
    """
    return _identity_key(login_id, name)


def _name_history(all_results: pd.DataFrame) -> Dict[str, dict]:
    """One entry per identity across the FULL results history on disk (not season-scoped -- a
    rename can predate the season being rendered), keyed by the same id _pilot_key produces:
    {"current": Pilot value of the row with the latest EventDate, "prior": [other distinct Pilot
    values, oldest first]}.

    "current" must be the value from the truly latest-dated row, not "whichever distinct name's
    FIRST occurrence sorts last" -- those only coincide when a name changes once, monotonically.
    They diverge as soon as one identity's rows interleave in time with another's (e.g. after a
    pilot_identity.csv merge joins two accounts that were both active concurrently): the account
    whose first-ever row happens to be earliest can dominate "first occurrence order" even though
    the other account's rows run later. This is not merge-specific -- it's a possible source of a
    wrong "current" name for any pilot whose raw Pilot strings, sorted by date, aren't a clean
    old-then-new sequence.
    """
    if all_results.empty:
        return {}
    work = all_results.copy()
    work["Pilot"] = work["Pilot"].astype(str).str.strip()
    work["LoginID"] = work.get("LoginID", "").astype(str).str.strip()
    work = work[work["Pilot"] != ""]
    work["_Key"] = [_pilot_key(lid, p) for lid, p in zip(work["LoginID"], work["Pilot"])]
    work["_Date"] = pd.to_datetime(work["EventDate"], errors="coerce")

    out: Dict[str, dict] = {}
    for key, grp in work.groupby("_Key"):
        dated = grp.dropna(subset=["_Date"]).sort_values("_Date", kind="mergesort")
        if dated.empty:
            # No row in this group has a parseable date -- no way to determine a true "latest",
            # so fall back to first-occurrence order (previous behavior for this edge case only).
            names_in_order = list(dict.fromkeys(grp["Pilot"].tolist()))
            current = names_in_order[-1]
            prior = names_in_order[:-1]
        else:
            current = dated.iloc[-1]["Pilot"]
            names_in_order = list(dict.fromkeys(dated["Pilot"].tolist()))
            prior = [n for n in names_in_order if n != current]
        out[key] = {"current": current, "prior": prior}
    return out


def _pilot_results_rows(grp: pd.DataFrame) -> List[dict]:
    """One row per event this identity appeared in, most recent first (ties broken by EventID
    ascending, for determinism when two events share a date)."""
    g = grp.copy()
    g["_Date"] = pd.to_datetime(g["EventDate"], errors="coerce")
    g = g.sort_values(["_Date", "EventID"], ascending=[False, True], kind="mergesort")

    rows = []
    for _, r in g.iterrows():
        tier = str(r.get("Tier", "")).strip()
        event_class = str(r.get("EventClass", "Challenge")).strip()
        rows.append({
            "date": str(r.get("EventDate", "")).strip(),
            "eventId": str(r.get("EventID", "")).strip(),
            "event": _event_label(tier, event_class),
            "tier": tier,
            "eventClass": event_class,
            "finish": _to_int_or_none(r.get("Place")),
            "deck": str(r.get("Deck", "")).strip(),
            "points": _to_int_or_none(r.get("LeaguePoints")) or 0,
            "swissPoints": _to_int_or_none(r.get("SwissPoints")),
            "gwp": _to_float_or_none(r.get("GWP")),
        })
    return rows


def _load_bracket_matches(matches_dir: Optional[Path], season_start: date, season_end: date) -> pd.DataFrame:
    """Reads every outputs/league/matches/<EventID>.csv (elimination-bracket matches only -- Round
    is QF/SF/F, never a Swiss round, see league_matches.py) whose EventDate falls in the season,
    skipping the _no_bracket.csv sentinel file. Returns an empty frame (not an error) when
    matches_dir is None or missing, or when no event in range has bracket data captured -- a
    pilot's opponent list is simply omitted for that case, not treated as a failure.
    """
    cols = ["EventID", "EventDate", "Tier", "EventClass", "Round", "WinnerPilot", "WinnerLoginID",
            "LoserPilot", "LoserLoginID", "WinnerGames", "LoserGames", "WinnerDeck", "LoserDeck"]
    if matches_dir is None or not matches_dir.exists():
        return pd.DataFrame(columns=cols)
    frames = []
    for p in sorted(matches_dir.glob("*.csv")):
        if p.name == "_no_bracket.csv":
            continue
        try:
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=cols)
    combined = pd.concat(frames, ignore_index=True)
    dates = pd.to_datetime(combined["EventDate"], errors="coerce").dt.date
    in_season = ((dates >= season_start) & (dates <= season_end)).fillna(False)
    return combined[in_season]


def build_season_site_data(
    results_dir: Path,
    season: str,
    season_start: date,
    season_end: date,
    as_of: date,
    prevrank_cutoff_days: int = 7,
    matches_dir: Optional[Path] = None,
) -> tuple:
    """Returns (season_doc, pilots_doc) for one season -- pure computation, no file I/O, so it can
    be unit-tested and diffed without touching disk.
    """
    table = build_season_table(
        results_dir, season_start, season_end, as_of=as_of, prevrank_cutoff_days=prevrank_cutoff_days
    )
    all_results = load_all_league_results(results_dir)
    names = _name_history(all_results)

    dates = pd.to_datetime(all_results["EventDate"], errors="coerce").dt.date
    season_mask = ((dates >= season_start) & (dates <= season_end)).fillna(False)
    season_results = all_results[season_mask].copy()
    season_results["_Key"] = [
        _pilot_key(lid, p) for lid, p in zip(season_results.get("LoginID", ""), season_results.get("Pilot", ""))
    ]
    results_by_key = {k: g for k, g in season_results.groupby("_Key")}

    season_pilots: List[dict] = []
    pilots_doc_entries: Dict[str, dict] = {}

    profile_map = pilot_identity.load_profiles()

    for _, row in table.iterrows():
        login_id = str(row.get("LoginID", "")).strip()
        name = str(row.get("Pilot", "")).strip()
        key = _pilot_key(login_id, name)
        hist = names.get(key, {"current": name, "prior": []})
        # login_id here is already the canonical pilot_id (build_season_table/aggregate_pilot_table
        # resolve it). `name` may be a pilot_profile.csv display_name override, which can differ
        # from hist["current"] (the raw account's own truly-latest name) -- so hist["current"] must
        # be in the candidate pool too, not just hist["prior"]. Excluding it silently drops a real
        # historical name whenever the override happens to equal one of the OTHER raw names (e.g.
        # merging two accounts and choosing the older one's name as canonical: hist["current"] is
        # then the newer account's raw name, genuinely distinct, and must still show up here).
        prior_names = [n for n in [hist["current"]] + hist["prior"] if n != name]
        profile_hidden = login_id and pilot_identity.is_profile_hidden(login_id, profile_map)
        x_handle = pilot_identity.get_visible_x_handle(login_id, profile_map) if login_id else None

        prev_rank = row.get("PrevRank")
        prev_rank_val = None if pd.isna(prev_rank) else int(prev_rank)
        rank = int(row["Rank"])
        if prev_rank_val is None:
            movement = "new"
        elif prev_rank_val > rank:
            movement = "up"
        elif prev_rank_val < rank:
            movement = "down"
        else:
            movement = "same"

        season_pilots.append({
            "rank": rank,
            "loginId": login_id or None,
            "id": key,
            "name": name,
            "priorNames": prior_names,
            "points": int(row["Points"]),
            "premierPoints": int(row["PremierPoints"]),
            "wins": int(row["Wins"]),
            "top2": int(row["Top2"]),
            "top4": int(row["Top4"]),
            "top8": int(row["Top8"]),
            "top16": int(row["Top16"]),
            "starts": int(row["Starts"]),
            "prevRank": prev_rank_val,
            "movement": movement,
            # See pilot_identity.is_profile_hidden(): the pilot stays in the season table under
            # their canonical name, but the site must not link to a profile page for them (their
            # entry is omitted from pilots_doc_entries below, so a stale/guessed link 404s).
            "profileHidden": bool(profile_hidden),
        })

        if profile_hidden:
            # Never serialize this identity's per-event results/bracket history at all -- same
            # "don't even put it on the wire" posture as x_handle consent below.
            continue

        grp = results_by_key.get(key)
        results_rows = _pilot_results_rows(grp) if grp is not None else []
        if grp is not None:
            decks = grp["Deck"].astype(str).str.strip()
            distinct_decks = int(decks[decks != ""].nunique())
        else:
            distinct_decks = 0

        pilots_doc_entries[key] = {
            "id": key,
            "loginId": login_id or None,
            "name": name,
            "priorNames": prior_names,
            "rank": rank,
            "points": int(row["Points"]),
            "premierPoints": int(row["PremierPoints"]),
            "counts": {
                "starts": int(row["Starts"]),
                "top16": int(row["Top16"]),
                "top8": int(row["Top8"]),
                "top4": int(row["Top4"]),
                "top2": int(row["Top2"]),
                "wins": int(row["Wins"]),
                "distinctDecks": distinct_decks,
            },
            "results": results_rows,
            # Only ever set when pilot_profile.csv has x_consent == true for this pilot -- see
            # pilot_identity.get_visible_x_handle(). This is the only place in the whole export
            # that reads x_handle at all, so a non-consented handle is never serialized, not just
            # hidden client-side.
            "xHandle": x_handle,
            # Elimination-bracket opponents (Top8/Top4/Top2 matches only -- never Swiss), filled
            # in below from outputs/league/matches/. Defaults to empty so the site never has to
            # special-case a missing key, only an empty list (e.g. no bracket capture for any of
            # this pilot's events, or the pilot never reached the bracket at all).
            "bracketMatches": [],
        }

    # Attach each pilot's elimination-bracket matches (see league_matches.py: Round is QF/SF/F,
    # captured from the mtgo.com JSON cache alongside the results sync, entirely separate from
    # this season table's Place-based scoring). Two entries per match row -- one from the winner's
    # side, one from the loser's -- each pointing at the other as "opponent". Only pilots already
    # in pilots_doc_entries can appear here (every bracket participant reached a Top32, so is
    # already present), which is also what keeps a stray/corrupt LoginID in the matches file from
    # silently fabricating a new pilot entry.
    bracket = _load_bracket_matches(matches_dir, season_start, season_end)
    if not bracket.empty:
        b = bracket.copy()
        b["_WinnerKey"] = [_pilot_key(lid, p) for lid, p in zip(b["WinnerLoginID"], b["WinnerPilot"])]
        b["_LoserKey"] = [_pilot_key(lid, p) for lid, p in zip(b["LoserLoginID"], b["LoserPilot"])]
        b["_Date"] = pd.to_datetime(b["EventDate"], errors="coerce")
        b = b.sort_values(["_Date", "EventID", "Round"], ascending=[False, True, True], kind="mergesort")

        for _, r in b.iterrows():
            w_key, l_key = r["_WinnerKey"], r["_LoserKey"]
            tier = str(r.get("Tier", "")).strip()
            event_class = str(r.get("EventClass", "Challenge")).strip()
            event_label = _event_label(tier, event_class)
            date_str = str(r.get("EventDate", "")).strip()
            round_label = str(r.get("Round", "")).strip()
            event_id = str(r.get("EventID", "")).strip()
            winner_deck = str(r.get("WinnerDeck", "")).strip()
            loser_deck = str(r.get("LoserDeck", "")).strip()

            w_name = names.get(w_key, {}).get("current") or str(r.get("WinnerPilot", "")).strip()
            l_name = names.get(l_key, {}).get("current") or str(r.get("LoserPilot", "")).strip()

            if w_key in pilots_doc_entries:
                pilots_doc_entries[w_key]["bracketMatches"].append({
                    "date": date_str, "eventId": event_id, "event": event_label, "tier": tier,
                    "round": round_label, "result": "W",
                    "opponentId": l_key, "opponentName": l_name,
                    "pilotDeck": winner_deck, "opponentDeck": loser_deck,
                })
            if l_key in pilots_doc_entries:
                pilots_doc_entries[l_key]["bracketMatches"].append({
                    "date": date_str, "eventId": event_id, "event": event_label, "tier": tier,
                    "round": round_label, "result": "L",
                    "opponentId": w_key, "opponentName": w_name,
                    "pilotDeck": loser_deck, "opponentDeck": winner_deck,
                })

    season_doc = {
        "season": season,
        "seasonStart": season_start.isoformat(),
        "seasonEnd": season_end.isoformat(),
        "asOf": as_of.isoformat(),
        "prevRankCutoffDays": prevrank_cutoff_days,
        "pilotCount": len(season_pilots),
        "pilots": season_pilots,
    }
    pilots_doc = {
        "season": season,
        "pilots": pilots_doc_entries,
    }
    return season_doc, pilots_doc


def export_league_site(
    league_dir: Path,
    docs_data_dir: Path,
    as_of: date,
    prevrank_cutoff_days: int = 7,
    log: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Rewrites docs/data/season_<slug>.json + pilots_<slug>.json for every season on record in
    outputs/league/season_config.csv (not just the season(s) a given weekly run touched) so the
    site's docs/data/ directory always reflects the full current state of outputs/league/results --
    the same "rebuilt from scratch every call" contract league_engine.build_season_table already
    has, extended to the site's own output. Also (re)writes docs/data/seasons.json, the manifest the
    site uses to find those files and pick a default season without the viewer supplying one.

    Returns the list of season names written. Safe to call with an empty/missing season_config.csv
    (writes an empty seasons.json and nothing else) -- e.g. the very first run before any season has
    been touched.
    """
    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    results_dir = league_dir / "results"
    matches_dir = league_dir / "matches"
    config_csv = league_dir / "season_config.csv"
    if not config_csv.exists():
        _write_json({"currentSeason": None, "seasons": []}, docs_data_dir / "seasons.json")
        return []

    config = pd.read_csv(config_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if config.empty:
        _write_json({"currentSeason": None, "seasons": []}, docs_data_dir / "seasons.json")
        return []

    config = config.sort_values("StartDate", kind="mergesort")

    from league_engine import season_for_date  # local import: avoids a cycle at module load time
    try:
        current_season_name, _s, _e = season_for_date(as_of)
    except Exception:
        current_season_name = None

    written_seasons: List[str] = []
    manifest_seasons: List[dict] = []

    for _, row in config.iterrows():
        season = str(row["Season"]).strip()
        s_start = pd.to_datetime(row["StartDate"]).date()
        s_end = pd.to_datetime(row["EndDate"]).date()
        slug = season_filename_slug(season)

        season_doc, pilots_doc = build_season_site_data(
            results_dir, season, s_start, s_end, as_of=as_of, prevrank_cutoff_days=prevrank_cutoff_days,
            matches_dir=matches_dir,
        )
        _write_json(season_doc, docs_data_dir / f"season_{slug}.json")
        _write_json(pilots_doc, docs_data_dir / f"pilots_{slug}.json")
        written_seasons.append(season)
        emit(f"[league-site] {season}: {len(season_doc['pilots'])} pilot(s) -> docs/data/season_{slug}.json + pilots_{slug}.json")

        manifest_seasons.append({
            "season": season,
            "slug": slug,
            "startDate": s_start.isoformat(),
            "endDate": s_end.isoformat(),
            "isCurrent": season == current_season_name,
        })

    _write_json(
        {"currentSeason": current_season_name, "seasons": manifest_seasons},
        docs_data_dir / "seasons.json",
    )
    emit(f"[league-site] wrote docs/data/seasons.json (current season: {current_season_name})")
    return written_seasons
