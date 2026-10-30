"""Test-wide setup. Since 2026-10-30 pilot_identity.csv / pilot_merge_log.csv live in a private directory
($PILOT_IDENTITY_DIR, src/identity.py) and a missing configuration is an error; the tests get an empty overlay (header
only) unless the variable is already set. No test depends on the real recorded merges (163 passed with both)."""
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _empty_pilot_identity_dir(tmp_path_factory):
    if os.environ.get("PILOT_IDENTITY_DIR"):
        yield
        return
    d = tmp_path_factory.mktemp("pilot_identity")
    (d / "pilot_identity.csv").write_text(
        "loginid,pilot_id,role,added_on,source,evidence,evidence_type,confirmed_on,note\n", encoding="utf-8")
    (d / "pilot_merge_log.csv").write_text(
        "timestamp,action,pilot_id,loginid,source,evidence,operator_note\n", encoding="utf-8")
    os.environ["PILOT_IDENTITY_DIR"] = str(d)
    yield
    del os.environ["PILOT_IDENTITY_DIR"]
