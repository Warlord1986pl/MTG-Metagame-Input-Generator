"""Offline, deterministic tests for src/identity.py, its wiring into league_engine._identity_key /
challenge_history_engine._identity_key, and src/pilot_identity_cli.py.

No pytest fixtures, no network access -- plain functions + manual tempfile.mkdtemp/shutil.rmtree,
runnable directly like tests/test_challenge_completeness.py.

Run directly: python tests/test_pilot_identity.py
Or via pytest (if installed): pytest tests/test_pilot_identity.py
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd  # noqa: E402

import identity  # noqa: E402
import pilot_identity_cli  # noqa: E402
from league_engine import _identity_key as league_identity_key  # noqa: E402
from league_engine import load_all_league_results, season_filename_slug  # noqa: E402
from challenge_history_engine import _identity_key as challenge_identity_key  # noqa: E402
from pilot_identity_cli import _season_config_rows  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "pilot_identity_zero_regression"
FIXTURE_AS_OF = date(2026, 8, 24)  # must match FIXTURE_DIR/expected/*'s generation date -- see
                                    # FIXTURE_DIR/README.md ("Why frozen, not live").

LEAGUE_RESULTS_HEADER = (
    "EventID,EventDate,Tier,EventClass,Pilot,LoginID,Place,Deck,LeaguePoints,"
    "SwissRank,SwissPoints,OMWP,GWP,OGWP"
)


@contextlib.contextmanager
def _isolated_identity():
    """Point identity.py at a fresh temp data dir for the duration of the block, then restore the
    real default. Prevents one test's pilot_identity.csv from leaking into the next."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="pilot_identity_test_"))
    try:
        identity.set_data_dir(tmp_dir)
        yield tmp_dir
    finally:
        identity.set_data_dir(identity.DEFAULT_DATA_DIR)
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# Missing-file resolves-to-self
# --------------------------------------------------------------------------------------------

def test_missing_file_resolves_to_self() -> None:
    with _isolated_identity() as tmp_dir:
        assert not (tmp_dir / "pilot_identity.csv").exists()
        assert identity.load_identity() == {}
        assert identity.resolve("2903591") == "2903591"
        assert identity.resolve("") == "", "blank loginid must stay blank, not become '' -> something"
        assert identity.profile("2903591") is None
        assert identity.display_name("2903591", fallback="RawName") == "RawName"


# --------------------------------------------------------------------------------------------
# Primary+alias resolution collapses two loginids into one _identity_key
# --------------------------------------------------------------------------------------------

def test_primary_alias_resolution_collapses_identity_key() -> None:
    with _isolated_identity() as tmp_dir:
        identity.write_identity_rows([
            {"loginid": "2903591", "pilot_id": "2903591", "role": "primary",
             "added_on": "2026-08-26", "source": "test", "evidence": "test", "note": ""},
            {"loginid": "3263693", "pilot_id": "2903591", "role": "alias",
             "added_on": "2026-08-26", "source": "test", "evidence": "test", "note": ""},
        ], backup=False)

        assert identity.resolve("3263693") == "2903591"
        assert identity.resolve("2903591") == "2903591"

        # The two _identity_key definitions (league_engine, challenge_history_engine) are the
        # choke point every downstream table groups by -- both must collapse the alias.
        key_primary_league = league_identity_key("2903591", "MeninoNey")
        key_alias_league = league_identity_key("3263693", "MeninooNey")
        assert key_primary_league == key_alias_league == "id:2903591"

        key_primary_challenge = challenge_identity_key("2903591", "MeninoNey")
        key_alias_challenge = challenge_identity_key("3263693", "MeninooNey")
        assert key_primary_challenge == key_alias_challenge == "id:2903591"


# --------------------------------------------------------------------------------------------
# Validations (a)/(b)/(c), each triggered individually
# --------------------------------------------------------------------------------------------

def test_validate_duplicate_loginid_triggers_a() -> None:
    rows = [
        {"loginid": "111", "pilot_id": "111", "role": "primary", "added_on": "", "source": "", "evidence": "", "note": ""},
        {"loginid": "111", "pilot_id": "222", "role": "primary", "added_on": "", "source": "", "evidence": "", "note": ""},
    ]
    problems = identity.validate_identity_rows(rows)
    assert any(p.startswith("(a)") for p in problems), f"expected (a) violation, got: {problems}"
    assert not any(p.startswith("(b)") for p in problems)
    assert not any(p.startswith("(c)") for p in problems)


def test_validate_alias_to_non_primary_triggers_b() -> None:
    # alias points at pilot_id "999" but no row has loginid=999/role=primary.
    rows = [
        {"loginid": "111", "pilot_id": "999", "role": "alias", "added_on": "", "source": "", "evidence": "", "note": ""},
    ]
    problems = identity.validate_identity_rows(rows)
    assert any(p.startswith("(b)") for p in problems), f"expected (b) violation, got: {problems}"
    assert not any(p.startswith("(a)") for p in problems)
    assert not any(p.startswith("(c)") for p in problems)


def test_validate_alias_chain_triggers_c() -> None:
    # 333 is primary; 222 is an alias of 333; 111 tries to alias to 222, which is itself an alias.
    rows = [
        {"loginid": "333", "pilot_id": "333", "role": "primary", "added_on": "", "source": "", "evidence": "", "note": ""},
        {"loginid": "222", "pilot_id": "333", "role": "alias", "added_on": "", "source": "", "evidence": "", "note": ""},
        {"loginid": "111", "pilot_id": "222", "role": "alias", "added_on": "", "source": "", "evidence": "", "note": ""},
    ]
    problems = identity.validate_identity_rows(rows)
    assert any(p.startswith("(c)") for p in problems), f"expected (c) violation, got: {problems}"
    # 111 -> 222 also trips (b): 222 has no role=primary row (it's an alias). Both are legitimate
    # for this row set; only assert (c) fired, since that's what this test targets.


# --------------------------------------------------------------------------------------------
# Validation (e): merging two loginids that collide on (EventID, pilot_id) must be caught and
# must block the write, through the real pilot_identity_cli.main() entry point (not a
# reimplementation of the check).
# --------------------------------------------------------------------------------------------

def _write_csv(path: Path, header: str, rows: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def test_event_collision_blocks_write() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pilot_identity_collision_"))
    try:
        outputs_base = tmp_dir / "outputs"
        league_dir = outputs_base / "league"
        docs_dir = tmp_dir / "docs"
        data_dir = tmp_dir / "data"

        # Two rows in the SAME EventID under two different LoginIDs -- 9000001 (to become primary)
        # and 9000002 (to become its alias). Merging them must collide: one identity, two results
        # in the same event, which build_season_table's own points math cannot represent validly.
        _write_csv(
            league_dir / "results" / "90000001.csv", LEAGUE_RESULTS_HEADER,
            [
                "90000001,2026-07-01,C32,Challenge,PrimaryName,9000001,5,Domain Zoo,2,10,12,0.50000,0.50000,0.50000",
                "90000001,2026-07-01,C32,Challenge,AliasName,9000002,1,Domain Zoo,5,1,18,0.60000,0.60000,0.60000",
            ],
        )
        (league_dir / "matches").mkdir(parents=True, exist_ok=True)
        _write_csv(
            league_dir / "season_config.csv", "Season,StartDate,EndDate",
            ["TestSeason,2026-06-01,2026-07-31"],
        )

        assert not data_dir.exists()

        argv = [
            "--outputs-base", str(outputs_base),
            "--docs-dir", str(docs_dir),
            "--data-dir", str(data_dir),
            "merge",
            "--primary", "9000001",
            "--alias", "9000002",
            "--source", "test",
            "--evidence", "test-evidence",
            "--apply",  # even with --apply requested, a validation failure must still block the write
        ]
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                rc = pilot_identity_cli.main(argv)
        finally:
            identity.set_data_dir(identity.DEFAULT_DATA_DIR)

        output = captured.getvalue()
        assert rc != 0, f"expected non-zero exit on an (e) collision, got {rc}. Output:\n{output}"
        assert "Validation (e)" in output
        assert "FAIL" in output
        assert "90000001" in output, "the offending EventID must be named in the report"

        # Nothing written -- not even a partial file.
        assert not (data_dir / "pilot_identity.csv").exists()
        assert not (data_dir / "pilot_profile.csv").exists()
        assert not (data_dir / "pilot_merge_log.csv").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# Zero regression: with no data/pilot_identity.csv on the resolution path, recomputing the real
# season/site output from the frozen pre-identity-layer fixture must reproduce it exactly for
# every field that exists in both schemas. Fields that exist only in the recomputed output
# (profileHidden, xHandle -- added by this session's league_site_export.py/docs/index.html
# changes, independent of identity merging) are allowed, but must sit at their documented
# no-merge default. See tests/fixtures/pilot_identity_zero_regression/README.md.
# --------------------------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_common_fields_equal(label: str, before: dict, after: dict) -> None:
    keys = set(before) | set(after)
    for k in keys:
        if k not in before or k not in after:
            continue  # schema-only addition, not a value regression -- allowed, checked separately
        assert before[k] == after[k], f"{label}: field {k!r} differs -- before={before[k]!r} after={after[k]!r}"


def test_zero_regression_no_identity_file() -> None:
    with _isolated_identity():
        # identity.py points at an empty temp dir (via _isolated_identity) -- resolve() is a no-op
        # for every loginid, same as "data/ doesn't exist at all" (missing-file and empty-dir are
        # equivalent: load_identity() reads nothing either way).
        from league_engine import build_season_table, write_season_league_csv, season_filename_slug
        from league_site_export import export_league_site

        league_input = FIXTURE_DIR / "league_input"
        expected = FIXTURE_DIR / "expected"
        config = pd.read_csv(league_input / "season_config.csv", dtype=str, encoding="utf-8-sig", keep_default_na=False)

        with tempfile.TemporaryDirectory(prefix="zero_regression_out_") as tmp:
            tmp = Path(tmp)
            out_league_dir = tmp / "league"
            out_docs_data_dir = tmp / "docs_data"
            out_league_dir.mkdir()
            out_docs_data_dir.mkdir()

            for _, row in config.iterrows():
                season = str(row["Season"]).strip()
                s_start = pd.to_datetime(row["StartDate"]).date()
                s_end = pd.to_datetime(row["EndDate"]).date()
                table = build_season_table(league_input / "results", s_start, s_end, as_of=FIXTURE_AS_OF, prevrank_cutoff_days=7)
                new_csv = write_season_league_csv(out_league_dir, season, table)
                expected_csv = expected / f"pilot_league_{season_filename_slug(season)}.csv"
                assert new_csv.read_bytes() == expected_csv.read_bytes(), (
                    f"{expected_csv.name}: byte-identical comparison failed -- this file's schema "
                    "hasn't changed, so any difference here IS a real regression."
                )

            export_league_site(league_input, out_docs_data_dir, as_of=FIXTURE_AS_OF)

            # seasons.json: no schema change -- byte-identical.
            assert (out_docs_data_dir / "seasons.json").read_bytes() == (expected / "seasons.json").read_bytes()

            # season_<slug>.json / pilots_<slug>.json: field-by-field over fields common to both.
            for name, id_field in (("season_Summer_2026.json", "id"), ("pilots_Summer_2026.json", None)):
                new_doc = _load_json(out_docs_data_dir / name)
                old_doc = _load_json(expected / name)
                assert new_doc["season"] == old_doc["season"]

                if name == "season_Summer_2026.json":
                    new_pilots = {p["id"]: p for p in new_doc["pilots"]}
                    old_pilots = {p["id"]: p for p in old_doc["pilots"]}
                else:
                    new_pilots = new_doc["pilots"]
                    old_pilots = old_doc["pilots"]

                assert set(new_pilots) == set(old_pilots), (
                    f"{name}: pilot id set changed -- new-only={set(new_pilots) - set(old_pilots)} "
                    f"missing={set(old_pilots) - set(new_pilots)}"
                )
                for pid in old_pilots:
                    _assert_common_fields_equal(f"{name} pilot {pid}", old_pilots[pid], new_pilots[pid])

                # New-schema-only fields must sit at their documented no-merge default.
                if name == "season_Summer_2026.json":
                    hidden_values = {p.get("profileHidden") for p in new_pilots.values()}
                    assert hidden_values == {False}, (
                        f"profileHidden must be False for every pilot with no data/pilot_profile.csv, got: {hidden_values}"
                    )
                else:
                    handle_values = {p.get("xHandle") for p in new_pilots.values()}
                    assert handle_values == {None}, (
                        f"xHandle must be None for every pilot with no data/pilot_profile.csv, got: {handle_values}"
                    )


# --------------------------------------------------------------------------------------------
# Regression guard for the third bug (found by hand, not by any simulation-based test above):
# build_season_site_data's prior_names only ever drew from hist["prior"], never hist["current"],
# so a real display_name override could silently drop a merged pilot's own raw historical name
# from priorNames. This reads the REAL, currently-written docs/data/*.json on disk -- not a
# recompute, not a simulation -- because that is exactly what the earlier bug slipped past: every
# in-memory "after" preview looked fine, only the file --apply actually wrote was wrong.
# --------------------------------------------------------------------------------------------

def test_saved_json_priornames_cover_all_aliases() -> None:
    identity_csv = REPO_ROOT / "data" / "pilot_identity.csv"
    if not identity_csv.exists():
        print("  (no data/pilot_identity.csv on disk -- nothing to verify yet)")
        return
    alias_rows = [r for r in identity.read_identity_rows(identity_csv) if r["role"] == "alias"]
    if not alias_rows:
        print("  (no alias rows in data/pilot_identity.csv -- nothing to verify yet)")
        return

    results_dir = REPO_ROOT / "outputs" / "league" / "results"
    all_results = load_all_league_results(results_dir)
    all_results = all_results.copy()
    all_results["LoginID"] = all_results.get("LoginID", "").astype(str).str.strip()
    all_results["Pilot"] = all_results.get("Pilot", "").astype(str).str.strip()
    raw_names_by_loginid: Dict[str, set] = {}
    for lid, grp in all_results.groupby("LoginID"):
        if lid:
            raw_names_by_loginid[lid] = set(n for n in grp["Pilot"].tolist() if n)

    league_dir = REPO_ROOT / "outputs" / "league"
    docs_data_dir = REPO_ROOT / "docs" / "data"
    checked = 0
    for season, _s, _e in _season_config_rows(league_dir):
        slug = season_filename_slug(season)
        season_json = docs_data_dir / f"season_{slug}.json"
        pilots_json = docs_data_dir / f"pilots_{slug}.json"
        if not season_json.exists() or not pilots_json.exists():
            continue
        season_doc = _load_json(season_json)
        pilots_doc = _load_json(pilots_json)
        season_pilots_by_id = {p["id"]: p for p in season_doc["pilots"]}

        for row in alias_rows:
            loginid, pilot_id = row["loginid"], row["pilot_id"]
            raw_names = raw_names_by_loginid.get(loginid)
            if not raw_names:
                continue  # this alias has no rows in outputs/league/results -- nothing to check here
            key = f"id:{pilot_id}"

            season_pilot = season_pilots_by_id.get(key)
            if season_pilot is None:
                continue  # pilot_id didn't play in this season
            name = season_pilot["name"]
            expected = {n for n in raw_names if n != name}
            if not expected:
                continue  # this alias's only raw name(s) already equal the shown name -- nothing to prove
            checked += 1
            prior = season_pilot.get("priorNames") or []
            assert prior, (
                f"season_{slug}.json: pilot_id {pilot_id} has alias loginid {loginid} "
                f"(raw name(s) {raw_names}) but priorNames is EMPTY"
            )
            missing = expected - set(prior)
            assert not missing, (
                f"season_{slug}.json: pilot_id {pilot_id}'s priorNames {prior} is missing "
                f"alias loginid {loginid}'s raw name(s) {missing}"
            )

            pilots_pilot = pilots_doc["pilots"].get(key)
            assert pilots_pilot is not None, f"pilots_{slug}.json: no entry for key {key} (pilot_id {pilot_id})"
            prior2 = pilots_pilot.get("priorNames") or []
            assert prior2, (
                f"pilots_{slug}.json: pilot_id {pilot_id} has alias loginid {loginid} "
                f"(raw name(s) {raw_names}) but priorNames is EMPTY"
            )
            missing2 = expected - set(prior2)
            assert not missing2, (
                f"pilots_{slug}.json: pilot_id {pilot_id}'s priorNames {prior2} is missing "
                f"alias loginid {loginid}'s raw name(s) {missing2}"
            )

    if checked == 0:
        print("  (no alias/season/name combination needed proving -- nothing to check yet)")
    else:
        print(f"  checked {checked} alias/season combination(s)")


if __name__ == "__main__":
    test_missing_file_resolves_to_self()
    print("OK: test_missing_file_resolves_to_self")
    test_primary_alias_resolution_collapses_identity_key()
    print("OK: test_primary_alias_resolution_collapses_identity_key")
    test_validate_duplicate_loginid_triggers_a()
    print("OK: test_validate_duplicate_loginid_triggers_a")
    test_validate_alias_to_non_primary_triggers_b()
    print("OK: test_validate_alias_to_non_primary_triggers_b")
    test_validate_alias_chain_triggers_c()
    print("OK: test_validate_alias_chain_triggers_c")
    test_event_collision_blocks_write()
    print("OK: test_event_collision_blocks_write")
    test_zero_regression_no_identity_file()
    print("OK: test_zero_regression_no_identity_file")
    test_saved_json_priornames_cover_all_aliases()
    print("OK: test_saved_json_priornames_cover_all_aliases")
    print("All pilot identity tests passed.")
