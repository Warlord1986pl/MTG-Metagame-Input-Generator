"""Pilot identity overlay: resolves a set of MTGO loginids that belong to the same person to one
canonical `pilot_id`, without ever touching event files or the mtgo.com JSON cache.

This is a pure overlay on top of `data/pilot_identity.csv` (loginid -> pilot_id) and
`data/pilot_profile.csv` (pilot_id -> display name / consent-gated X handle / hidden flag). Neither
file is required to exist -- a loginid absent from pilot_identity.csv resolves to itself, and a
pilot_id absent from pilot_profile.csv has no profile overrides -- so this module can be imported
and called from every identity-grouping call site in the pipeline (league_engine._identity_key,
challenge_history_engine._identity_key) with zero risk of breaking anything before the first merge
is ever recorded.

This module deliberately never reads event data (challenge/premier history CSVs, league results,
the mtgo.com JSON cache). `display_name()`/`all_names()` take the "latest name from data" /
"candidate names" as caller-supplied arguments -- the caller (league_engine, challenge_history_engine,
league_site_export, pilot_identity_cli) already has that data loaded; this module only ever
overlays `data/pilot_identity.csv` + `data/pilot_profile.csv` on top of it.

Write-side helpers (write_identity_rows/write_profile_rows/append_merge_log_rows) and the
a/b/c structural validations are used only by pilot_identity_cli.py -- the read-heavy engine code
(league_engine, challenge_history_engine) only ever calls resolve()/profile()/display_name()/
all_names().
"""

from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"

IDENTITY_COLS: List[str] = [
    "loginid", "pilot_id", "role", "added_on", "source", "evidence",
    # Documentation, not validation logic (validate_identity_rows never reads these) -- so a
    # future reader can tell a confirmed merge from a guess without asking anyone. EvidenceType is
    # a short classification (e.g. "direct_confirmation") of *evidence*'s freeform text;
    # ConfirmedOn is when that confirmation actually happened, which can predate added_on (the
    # date this row was recorded) by however long the merge sat un-applied. Both blank for a
    # primary-only row (nothing to confirm -- there is no alias to merge).
    "evidence_type", "confirmed_on",
    "note",
]
PROFILE_COLS: List[str] = [
    "pilot_id", "display_name", "x_handle", "x_consent", "x_confirmed_on", "profile_hidden", "note",
]
MERGE_LOG_COLS: List[str] = ["timestamp", "action", "pilot_id", "loginid", "source", "evidence", "operator_note"]

# Mutable module-level path + cache so pilot_identity_cli.py can point this module at a temp file to
# compute an "after a candidate merge" state using the real production code path (league_engine/
# challenge_history_engine unchanged), then restore the real path. See set_identity_csv_path().
_identity_csv_path: Path = DEFAULT_DATA_DIR / "pilot_identity.csv"
_profile_csv_path: Path = DEFAULT_DATA_DIR / "pilot_profile.csv"
_merge_log_csv_path: Path = DEFAULT_DATA_DIR / "pilot_merge_log.csv"

_identity_cache: Optional[Dict[str, str]] = None
_profile_cache: Optional[Dict[str, dict]] = None


def _norm(value: object) -> str:
    return str(value or "").strip()


def _read_rows(path: Path, columns: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            cleaned = {col: _norm(row.get(col, "")) for col in columns}
            if any(cleaned.values()):
                rows.append(cleaned)
        return rows


def _write_rows(path: Path, columns: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def _backup_if_exists(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


# --------------------------------------------------------------------------------------------
# Path configuration / cache control (used by pilot_identity_cli.py for the before/after dance,
# and by tests)
# --------------------------------------------------------------------------------------------

def set_identity_csv_path(path: Path) -> None:
    global _identity_csv_path
    _identity_csv_path = Path(path)
    reset_cache()


def set_profile_csv_path(path: Path) -> None:
    global _profile_csv_path
    _profile_csv_path = Path(path)
    reset_cache()


def set_merge_log_csv_path(path: Path) -> None:
    global _merge_log_csv_path
    _merge_log_csv_path = Path(path)


def set_data_dir(data_dir: Path) -> None:
    """Convenience: points all three files at <data_dir>/pilot_identity.csv etc. in one call."""
    data_dir = Path(data_dir)
    set_identity_csv_path(data_dir / "pilot_identity.csv")
    set_profile_csv_path(data_dir / "pilot_profile.csv")
    set_merge_log_csv_path(data_dir / "pilot_merge_log.csv")


def reset_cache() -> None:
    global _identity_cache, _profile_cache
    _identity_cache = None
    _profile_cache = None


# --------------------------------------------------------------------------------------------
# Read side -- used by league_engine._identity_key / challenge_history_engine._identity_key and
# everything downstream of them.
# --------------------------------------------------------------------------------------------

def read_identity_rows(path: Optional[Path] = None) -> List[Dict[str, str]]:
    return _read_rows(path or _identity_csv_path, IDENTITY_COLS)


def read_profile_rows(path: Optional[Path] = None) -> List[Dict[str, str]]:
    return _read_rows(path or _profile_csv_path, PROFILE_COLS)


def read_merge_log_rows(path: Optional[Path] = None) -> List[Dict[str, str]]:
    return _read_rows(path or _merge_log_csv_path, MERGE_LOG_COLS)


def load_identity() -> Dict[str, str]:
    """loginid -> pilot_id, cached after first read. Missing file -> empty dict (every loginid
    then resolves to itself via resolve())."""
    global _identity_cache
    if _identity_cache is None:
        _identity_cache = {row["loginid"]: row["pilot_id"] for row in read_identity_rows() if row["loginid"]}
    return _identity_cache


def load_profiles() -> Dict[str, dict]:
    global _profile_cache
    if _profile_cache is None:
        _profile_cache = {row["pilot_id"]: row for row in read_profile_rows() if row["pilot_id"]}
    return _profile_cache


def resolve(loginid: object, identity_map: Optional[Dict[str, str]] = None) -> str:
    """loginid -> pilot_id. A loginid absent from the map resolves to itself. Blank input stays
    blank (the caller -- _identity_key -- treats a blank loginid as "no LoginID available" and
    falls back to a name-keyed identity; resolving "" to "" preserves that)."""
    lid = _norm(loginid)
    if not lid:
        return lid
    m = identity_map if identity_map is not None else load_identity()
    return m.get(lid, lid)


def profile(pilot_id: object, profile_map: Optional[Dict[str, dict]] = None) -> Optional[dict]:
    pid = _norm(pilot_id)
    if not pid:
        return None
    m = profile_map if profile_map is not None else load_profiles()
    return m.get(pid)


def display_name(pilot_id: object, fallback: str = "", profile_map: Optional[Dict[str, dict]] = None) -> str:
    """Canonical display name for pilot_id, if pilot_profile.csv sets one; else *fallback* (the
    caller's own "latest name observed in the data" computation -- this module never reads event
    data itself)."""
    p = profile(pilot_id, profile_map)
    if p:
        name = _norm(p.get("display_name"))
        if name:
            return name
    return fallback


def all_names(pilot_id: object, candidate_names: Iterable[str], profile_map: Optional[Dict[str, dict]] = None) -> List[str]:
    """Dedup *candidate_names* (every historical display name the caller observed across every
    loginid that resolves to pilot_id), order-preserving, with the canonical display_name (if set)
    moved to the front."""
    seen: List[str] = []
    for n in candidate_names:
        n = _norm(n)
        if n and n not in seen:
            seen.append(n)
    canonical = display_name(pilot_id, fallback="", profile_map=profile_map)
    if canonical:
        seen = [canonical] + [n for n in seen if n != canonical]
    return seen


def is_x_handle_visible(pilot_id: object, profile_map: Optional[Dict[str, dict]] = None) -> bool:
    """True only when pilot_profile.csv has x_consent == true for this pilot_id. This is the one
    and only gate other code should ever need -- get_visible_x_handle() below already applies it,
    so callers should not read row["x_handle"] directly."""
    p = profile(pilot_id, profile_map)
    return bool(p) and _norm(p.get("x_consent")).lower() in ("true", "1", "yes")


def get_visible_x_handle(pilot_id: object, profile_map: Optional[Dict[str, dict]] = None) -> Optional[str]:
    """The X handle if, and only if, x_consent == true. Returns None otherwise (no handle, or
    consent not given) -- callers should never branch on x_consent themselves, only call this."""
    if not is_x_handle_visible(pilot_id, profile_map):
        return None
    p = profile(pilot_id, profile_map)
    handle = _norm(p.get("x_handle")) if p else ""
    return handle or None


def is_profile_hidden(pilot_id: object, profile_map: Optional[Dict[str, dict]] = None) -> bool:
    p = profile(pilot_id, profile_map)
    return bool(p) and _norm(p.get("profile_hidden")).lower() in ("true", "1", "yes")


# --------------------------------------------------------------------------------------------
# Structural validations (a)/(b)/(c) -- pure, over pilot_identity.csv row dicts. Used by
# pilot_identity_cli.py before ever writing a candidate row set.
# --------------------------------------------------------------------------------------------

def validate_identity_rows(rows: List[Dict[str, str]]) -> List[str]:
    problems: List[str] = []

    # (a) each loginid appears at most once
    seen: Dict[str, int] = {}
    for row in rows:
        lid = row["loginid"]
        if not lid:
            continue
        seen[lid] = seen.get(lid, 0) + 1
    dupes = sorted(lid for lid, n in seen.items() if n > 1)
    if dupes:
        problems.append(f"(a) loginid appears more than once in pilot_identity.csv: {dupes}")

    primaries = {row["loginid"] for row in rows if row["role"] == "primary" and row["loginid"]}
    aliases = {row["loginid"] for row in rows if row["role"] == "alias" and row["loginid"]}

    # (b) each alias's pilot_id must be a loginid with an explicit role=primary row
    bad_targets = sorted(
        f"{row['loginid']} -> pilot_id {row['pilot_id']} (no role=primary row for that pilot_id)"
        for row in rows
        if row["role"] == "alias" and row["pilot_id"] not in primaries
    )
    if bad_targets:
        problems.append(f"(b) alias points at a pilot_id with no primary row: {bad_targets}")

    # (c) no chains -- an alias's pilot_id must not itself be a loginid that is ALSO an alias
    chains = sorted(
        f"{row['loginid']} -> pilot_id {row['pilot_id']} (which is itself an alias)"
        for row in rows
        if row["role"] == "alias" and row["pilot_id"] in aliases
    )
    if chains:
        problems.append(f"(c) alias chain detected (alias points at another alias): {chains}")

    unknown_roles = sorted({row["role"] for row in rows if row["role"] not in ("primary", "alias")})
    if unknown_roles:
        problems.append(f"role must be 'primary' or 'alias', found: {unknown_roles}")

    return problems


# --------------------------------------------------------------------------------------------
# Write side -- used only by pilot_identity_cli.py.
# --------------------------------------------------------------------------------------------

def write_identity_rows(rows: List[Dict[str, str]], path: Optional[Path] = None, backup: bool = True) -> Optional[Path]:
    target = path or _identity_csv_path
    backup_path = _backup_if_exists(target) if backup else None
    _write_rows(target, IDENTITY_COLS, rows)
    reset_cache()
    return backup_path


def write_profile_rows(rows: List[Dict[str, str]], path: Optional[Path] = None, backup: bool = True) -> Optional[Path]:
    target = path or _profile_csv_path
    backup_path = _backup_if_exists(target) if backup else None
    _write_rows(target, PROFILE_COLS, rows)
    reset_cache()
    return backup_path


def append_merge_log_rows(rows: List[Dict[str, str]], path: Optional[Path] = None, backup: bool = True) -> Optional[Path]:
    """Append-only: existing rows are preserved, *rows* is appended after them. Still backed up
    before the write, same as the other two files."""
    target = path or _merge_log_csv_path
    backup_path = _backup_if_exists(target) if backup else None
    existing = read_merge_log_rows(target)
    _write_rows(target, MERGE_LOG_COLS, existing + rows)
    return backup_path
