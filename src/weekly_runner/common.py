"""Shared pieces of the week runner and the triage check: stop with a clear message, subprocess calls, git state,
sha256, ISO weeks, runner config (docs/runner_config.csv). Every error is a Stop with a non-zero code, no fallback."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
RUNNER_CONFIG = REPO / "docs" / "runner_config.csv"
CONFIG_KEYS = ("deckdb_dir", "deckdb_python", "pipeline_python", "trend_weeks")
PROBE = Path(__file__).resolve().parent / "deckdb_probe.py"

# exit codes of metagame_week.py (docs/runner.md lists them with what to do)
EXIT_ARGS, EXIT_PREFLIGHT, EXIT_IMPORT, EXIT_TRIAGE, EXIT_EXPORT, EXIT_MTGO, EXIT_PIPELINE, EXIT_FOLDER, EXIT_BUILD = (
    2, 3, 4, 5, 6, 7, 8, 9, 10)
EXIT_UNEXPECTED = 1  # an unexpected exception (traceback in the report)


class Stop(Exception):
    """A stop of the run: message for the user and the exit code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class StepLog:
    """Durations of the steps, warnings and facts collected for the report."""
    steps: List[tuple] = field(default_factory=list)  # (name, seconds, "OK" / "STOP")
    warnings: List[str] = field(default_factory=list)

    def run(self, name: str, fn, *a, **kw):
        t0 = time.monotonic()
        print(f"== {name}", flush=True)
        try:
            out = fn(*a, **kw)
        except Stop:
            self.steps.append((name, time.monotonic() - t0, "STOP"))
            raise
        self.steps.append((name, time.monotonic() - t0, "OK"))
        return out

    def warn(self, text: str) -> None:
        print(f"OSTRZEŻENIE: {text}", flush=True)
        self.warnings.append(text)


def load_runner_config(path: Path = RUNNER_CONFIG) -> Dict[str, str]:
    if not path.exists():
        raise Stop(EXIT_PREFLIGHT, f"brak pliku konfiguracji runnera {path}")
    with path.open(encoding="utf-8", newline="") as f:
        cfg = {r["key"].strip(): r["value"].strip() for r in csv.DictReader(f)}
    missing = [k for k in CONFIG_KEYS if not cfg.get(k)]
    if missing:
        raise Stop(EXIT_PREFLIGHT, f"{path.name}: brak kluczy {missing}")
    return cfg


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: List[str], cwd: Path, log_path: Path, code: int, what: str, env: Optional[dict] = None) -> str:
    """Runs *cmd*, writes stdout + stderr to *log_path*, returns stdout. Non-zero exit = Stop(code)."""
    full_env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    print(f"   $ {' '.join(str(c) for c in cmd)}", flush=True)
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=full_env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(str(c) for c in cmd)}\n(exit {p.returncode})\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}\n")
    if p.returncode != 0:
        tail = "\n".join((p.stderr or p.stdout).strip().splitlines()[-15:])
        raise Stop(code, f"{what}: kod wyjścia {p.returncode} (log {log_path})\n{tail}")
    return p.stdout


def probe(cfg: Dict[str, str], args: List[str], log_path: Path, code: int, what: str) -> dict:
    """deckdb_probe.py under the DeckDB interpreter (read-only on the DB), JSON on the last stdout line."""
    out = run([cfg["deckdb_python"], PROBE, "--deckdb-dir", cfg["deckdb_dir"], *args], Path(cfg["deckdb_dir"]),
              log_path, code, what)
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    if not lines:
        raise Stop(code, f"{what}: brak odpowiedzi deckdb_probe.py (log {log_path})")
    return json.loads(lines[-1])


def git_state(repo: Path) -> tuple[str, List[str]]:
    """(HEAD commit, porcelain lines of changed / untracked files)."""
    def g(*a):
        p = subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            raise Stop(EXIT_PREFLIGHT, f"git {' '.join(a)} w {repo}: {p.stderr.strip()}")
        return p.stdout
    return g("rev-parse", "HEAD").strip(), [ln for ln in g("status", "--porcelain").splitlines() if ln.strip()]


def git_out(repo: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise Stop(EXIT_PREFLIGHT, f"git {' '.join(a)} w {repo}: {p.stderr.strip()}")
    return p.stdout


def iso_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def parse_week(text: str) -> tuple[date, date]:
    """'2026-W43' -> (Monday, Sunday)."""
    try:
        y, w = text.upper().split("-W")
        mon = date.fromisocalendar(int(y), int(w), 1)
    except ValueError:
        raise Stop(EXIT_ARGS, f"--week {text!r}: nie jest tygodniem ISO w formacie RRRR-Wnn (np. 2026-W44)") from None
    return mon, mon + timedelta(days=6)


def last_complete_week(today: date) -> str:
    """The last ISO week whose Sunday is before *today* (on a Sunday the running week is not complete)."""
    return iso_label(today - timedelta(days=today.isoweekday()))


def months_between(start: date, end: date) -> List[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def check_network(url: str = "https://www.mtgo.com/decklists", code: int = EXIT_PREFLIGHT) -> None:
    import requests

    try:
        r = requests.get(url, timeout=30)
    except requests.RequestException as e:
        raise Stop(code, f"brak dostępu do sieci ({url}): {type(e).__name__}: {e}") from None
    if r.status_code != 200:
        raise Stop(code, f"brak dostępu do {url}: HTTP {r.status_code}")


def backfill_and_import(cfg: Dict[str, str], start: date, end: date, work: Path, code: int) -> dict:
    """DeckDB: backfill-raw + import --raw-window + derived columns, as in decisions.md entries 306 / 312. Every
    failure in the backfill report is a stop (also when backfill-raw itself exits 0)."""
    dd, py, log = Path(cfg["deckdb_dir"]), cfg["deckdb_python"], work / "logs" / "deckdb.log"
    report = work / f"backfill_{start}_{end}.json"
    report.unlink(missing_ok=True)
    run([py, "-m", "deckdb", "backfill-raw", "--start", start, "--end", end, "--report", report], dd, log, code,
        "deckdb backfill-raw")
    if not report.exists():
        raise Stop(code, f"deckdb backfill-raw nie zapisał raportu {report}")
    rep = json.loads(report.read_text(encoding="utf-8"))
    if rep.get("failures"):
        lines = [f"  {f['kind']} {f['key']}" + (f" ({f.get('date')})" if f.get("date") else "") + ": "
                 + "; ".join(str(a[1]) for a in f.get("attempts") or []) for f in rep["failures"]]
        raise Stop(code, "deckdb backfill-raw: nieudane pobrania (" + str(len(rep["failures"])) + "):\n"
                   + "\n".join(lines) + f"\nRaport: {report}. Uruchom ponownie później; eventy już zapisane nie są "
                   "pobierane drugi raz.")
    out = run([py, "-m", "deckdb", "import", "--raw-window", f"{start}:{end}"], dd, log, code, "deckdb import")
    check_import_output(out, code)
    for sub in ("recompute-spell-colours", "recompute-sb-splash", "recompute-packages"):
        run([py, "-m", "deckdb", sub], dd, log, code, f"deckdb {sub}")
    return {m: {k: v for k, v in r.items() if k != "listing"} for m, r in rep.get("months", {}).items()}


IMPORT_COUNTERS = ("rejected events", "refused writes", "rejected decklists")


def check_import_output(out: str, code: int) -> None:
    """deckdb import exits 0 also when it rejected an event or refused a write (a stored row differs from the
    incoming one, quarantined): every such line is a stop."""
    bad, block = [], None
    for ln in out.splitlines():
        head = next((c for c in IMPORT_COUNTERS if ln.startswith(c)), None)
        if head is not None:
            n = int(ln.rsplit(":", 1)[1])
            block = head if n else None
            if n:
                bad.append(ln)
        elif block and ln.startswith("  "):
            bad.append(ln)
        else:
            block = None
    if bad:
        raise Stop(code, "deckdb import: odrzucone eventy lub odmowy zapisu (dane różne od zapisanych, kwarantanna "
                         "DeckDB):\n" + "\n".join(bad[:40]) + "\nCo zrobić: rozstrzygnij w DeckDB (refusals.py, "
                         "data/quarantine), runner niczego nie nadpisuje.")


def toast(title: str, message: str) -> None:
    """Windows toast through Windows PowerShell 5.1 (WinRT), text passed in environment variables."""
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]"
        " > $null; "
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$x = $t.GetElementsByTagName('text'); "
        "$x.Item(0).AppendChild($t.CreateTextNode($env:TOAST_TITLE)) > $null; "
        "$x.Item(1).AppendChild($t.CreateTextNode($env:TOAST_TEXT)) > $null; "
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))")
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, env={**os.environ, "TOAST_TITLE": title, "TOAST_TEXT": message})
    if p.returncode != 0 or p.stderr.strip():
        raise Stop(3, f"powiadomienie Windows nie zostało wyświetlone: {p.stderr.strip() or p.returncode}")
