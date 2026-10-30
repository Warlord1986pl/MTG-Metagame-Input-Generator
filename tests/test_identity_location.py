"""The private identity directory (src/identity.py, 2026-10-30): not configured or a missing file is an error, never a
silently empty overlay; the location file serves cron jobs without environment."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import identity  # noqa: E402


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.delenv(identity.PRIVATE_DIR_ENV, raising=False)
    monkeypatch.setattr(identity, "PRIVATE_DIR_FILE", tmp_path / ".pilot_identity_dir")
    monkeypatch.setattr(identity, "_identity_csv_path", None)
    monkeypatch.setattr(identity, "_merge_log_csv_path", None)
    identity.reset_cache()
    yield tmp_path
    identity.reset_cache()


def test_not_configured_is_an_error(fresh):
    with pytest.raises(identity.IdentityLocationError, match="not configured"):
        identity.load_identity()


def test_configured_directory_must_exist_and_hold_the_files(fresh, monkeypatch):
    monkeypatch.setenv(identity.PRIVATE_DIR_ENV, str(fresh / "nope"))
    with pytest.raises(identity.IdentityLocationError, match="does not exist"):
        identity.load_identity()
    monkeypatch.setenv(identity.PRIVATE_DIR_ENV, str(fresh))
    with pytest.raises(identity.IdentityLocationError, match="pilot_identity.csv is missing"):
        identity.load_identity()


def test_location_file_for_cron(fresh):
    d = fresh / "private"
    d.mkdir()
    (d / "pilot_identity.csv").write_text("loginid,pilot_id,role\n900000864,900000622,alias\n", encoding="utf-8")
    (d / "pilot_merge_log.csv").write_text("timestamp,action,pilot_id,loginid\n", encoding="utf-8")
    identity.PRIVATE_DIR_FILE.write_text(f"{d}\n", encoding="utf-8")
    assert identity.resolve("900000864") == "900000622"
    assert identity.merge_log_csv_path() == d / "pilot_merge_log.csv"
    assert identity.profile_csv_path() == identity.DEFAULT_DATA_DIR / "pilot_profile.csv"
