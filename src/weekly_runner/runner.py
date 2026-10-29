"""metagame_week.py: the week runner (docs/runner.md). Stage DATA: preflight, DeckDB import, triage, exports, check
against mtgo.com, pipeline (input mode deckdb), flat folder weekly/<week>/ with run_report.md. Stage BUILD: the
scripts of build/ (maintained in the Claude project, never edited here) on the content files of the week folder.
Every error is a stop with a message and a non-zero exit code (common.EXIT_*), no fallback."""
from __future__ import annotations

import argparse
import csv
import importlib.metadata as md
import io
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import mtgo_check
from .common import (EXIT_ARGS, EXIT_BUILD, EXIT_EXPORT, EXIT_FOLDER, EXIT_IMPORT, EXIT_PIPELINE, EXIT_PREFLIGHT,
                     EXIT_TRIAGE, EXIT_UNEXPECTED, REPO, StepLog, Stop, backfill_and_import, check_network, git_out,
                     git_state, iso_label, last_complete_week, load_runner_config, parse_week, probe, run, sha256)

PIPELINE_PACKAGES = ("pandas", "numpy", "matplotlib", "openpyxl", "PyYAML", "requests")
DATA_OUTPUTS = ("data.xlsx", "notes.md", "run_report.md")  # plus *.png and mtgo_snapshot_*.html
BUILD_SCRIPTS = ("analysis.py", "build_deck.js", "gen_dossier.py", "build_docs.py", "qa.py", "README_workflow.md")
CONTENT_FILES = ("deck_w{n}.json", "dossier_w{n}.src.md", "reddit_w{n}.md", "youtube_w{n}.md", "x_w{n}.md")
CONFIG_REL = "docs/deckdb_config.csv"
RUNS_COLUMNS = ("data", "tydzien", "etap", "status", "sha256_eksportu", "wersja_regul", "eventy", "listy", "triage")


class WeekRun:
    def __init__(self, week: str, weekly_root: Path, force: bool, test_monthly: bool, today: date):
        self.week = week
        self.mon, self.sun = parse_week(week)
        self.dir = weekly_root / week
        self.weekly_root = weekly_root
        self.work = REPO / "outputs" / "_working" / "runner" / week
        self.force, self.test_monthly, self.today = force, test_monthly, today
        self.log = StepLog()
        self.facts: Dict[str, object] = {}
        self.cfg: Dict[str, str] = {}

    # --- DATA ---------------------------------------------------------------------------------------------------

    def data_outputs(self) -> List[Path]:
        if not self.dir.exists():
            return []
        return sorted(p for p in self.dir.iterdir() if p.is_file() and (
            p.name in DATA_OUTPUTS or p.suffix == ".png" or p.name.startswith("mtgo_snapshot_")))

    def check_args(self) -> None:
        if self.sun >= self.today:
            raise Stop(EXIT_ARGS, f"tydzień {self.week} ({self.mon} .. {self.sun}) nie jest zakończony "
                                  f"(dziś {self.today}); runner pracuje na zakończonych tygodniach ISO")
        existing = self.data_outputs()
        if existing and not self.force:
            raise Stop(EXIT_ARGS, f"folder {self.dir} już zawiera materiały tygodnia ({len(existing)} plików, np. "
                                  f"{existing[0].name}); użyj --force, aby je zastąpić")
        first_next = self.sun.replace(day=1)
        if self.test_monthly and self.mon <= first_next <= self.sun:
            raise Stop(EXIT_ARGS, f"--test-monthly: tydzień {self.week} zawiera {first_next}, więc ma już materiał "
                                  "miesięczny za poprzedni miesiąc (reguła month_for); testowy materiał tu nie wchodzi")

    def preflight(self) -> None:
        if sys.version_info < (3, 10):
            raise Stop(EXIT_PREFLIGHT, f"Python {sys.version.split()[0]}: wymagany >= 3.10")
        versions = {"python (pipeline)": sys.version.split()[0]}
        for pkg in PIPELINE_PACKAGES:
            try:
                versions[pkg] = md.version(pkg)
            except md.PackageNotFoundError:
                raise Stop(EXIT_PREFLIGHT, f"brak pakietu {pkg} w {sys.executable} (pip install -r requirements.txt)"
                           ) from None
        self.cfg = load_runner_config()
        dd = Path(self.cfg["deckdb_dir"])
        for need in (dd, dd / "config.json", dd / "rules" / "format_events.yaml", dd / "scripts" / "weekly_triage.py",
                     Path(self.cfg["deckdb_python"]), Path(self.cfg["pipeline_python"])):
            if not need.exists():
                raise Stop(EXIT_PREFLIGHT, f"brak {need} (docs/runner_config.csv)")
        info = probe(self.cfg, ["info"], self.work / "logs" / "deckdb.log", EXIT_PREFLIGHT, "DeckDB (info)")
        if not Path(info["db_path"]).exists():
            raise Stop(EXIT_PREFLIGHT, f"brak bazy DeckDB {info['db_path']}")
        versions["python (DeckDB)"] = info["python"]
        versions["PyYAML (DeckDB)"] = info["yaml"]
        check_network(code=EXIT_PREFLIGHT)
        pipe_head, pipe_dirty = git_state(REPO)
        ddb_head, ddb_dirty = git_state(dd)
        if any(ln[3:].strip().strip('"') == CONFIG_REL for ln in pipe_dirty):
            raise Stop(EXIT_PREFLIGHT, f"{CONFIG_REL} ma niezacommitowane zmiany: zacommituj je albo cofnij "
                                       "(folder tygodnia nie może powstać na konfiguracji spoza historii)")
        for name, dirty in (("pipeline", pipe_dirty), ("DeckDB", ddb_dirty)):
            if dirty:
                self.log.warn(f"repo {name} nie jest czyste ({len(dirty)} plików): "
                              + ", ".join(ln[3:] for ln in dirty[:20]) + (" ..." if len(dirty) > 20 else ""))
        self.facts.update(versions=versions, pipeline_commit=pipe_head, deckdb_commit=ddb_head,
                          config_commit=git_out(REPO, "log", "-1", "--format=%H", "--", CONFIG_REL).strip(),
                          rules_version_before=info["rules_version"])

    def import_lists(self) -> None:
        self.facts["backfill"] = backfill_and_import(self.cfg, self.mon, self.sun, self.work, EXIT_IMPORT)

    def triage(self) -> None:
        out_dir = Path(self.cfg["deckdb_dir"]) / "data" / "exports"
        res = probe(self.cfg, ["triage", "--out-dir", str(out_dir)], self.work / "logs" / "deckdb.log", EXIT_TRIAGE,
                    "DeckDB weekly_triage.py")
        state = json.loads(Path(res["triage_json"]).read_text(encoding="utf-8"))
        self.facts["triage"] = state
        self.facts["triage_path"] = res["triage_json"]
        if state["last_challenge"] > self.sun.isoformat():
            self.log.warn(f"baza ma już Challenge do {state['last_challenge']}: triage opisuje stan bazy (tydzień "
                          f"{state['week']['iso_week']}), nie tydzień {self.week}")
        if state["status"] == "pending_blocking":
            raise Stop(EXIT_TRIAGE, blocking_message(state))

    def export(self) -> None:
        dd, py = Path(self.cfg["deckdb_dir"]), self.cfg["deckdb_python"]
        log = self.work / "logs" / "deckdb.log"
        info = probe(self.cfg, ["info"], log, EXIT_EXPORT, "DeckDB (info)")
        if info["data_version"] != self.facts["triage"]["data_version"]:
            raise Stop(EXIT_EXPORT, f"stan bazy zmienił się po triage'u ({self.facts['triage']['data_version']} -> "
                                    f"{info['data_version']}): uruchom runner ponownie")
        exports = dd / "data" / "exports"
        paths = {}
        for kind, sub in (("assignments", "export-assignments"), ("cards", "export-cards")):
            name = f"{kind}_{info['rules_version']}_{info['last_challenge']}.csv"
            tmp = self.work / "exports" / name
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.unlink(missing_ok=True)
            run([py, "-m", "deckdb", sub, "--out", tmp], dd, log, EXIT_EXPORT, f"deckdb {sub}")
            target = exports / name
            if target.exists():
                if sha256(target) != sha256(tmp):
                    raise Stop(EXIT_EXPORT, f"{target} już istnieje z inną zawartością niż eksport z bieżącego stanu "
                                            f"bazy (sha256 {sha256(target)[:12]} vs {sha256(tmp)[:12]}); nowy plik: "
                                            f"{tmp}. Sprawdź różnicę (scripts/export_diff.py w DeckDB), potem przenieś "
                                            "stary plik i uruchom ponownie")
                tmp.unlink()
            else:
                shutil.move(str(tmp), str(target))
            paths[kind] = target
        dvs = {}
        for kind, p in paths.items():
            vals = set(pd.read_csv(p, usecols=["data_version"], dtype=str)["data_version"].dropna())
            if len(vals) != 1:
                raise Stop(EXIT_EXPORT, f"{p.name}: data_version {sorted(vals)} (oczekiwana jedna wartość)")
            dvs[kind] = vals.pop()
        if not dvs["assignments"] == dvs["cards"] == info["data_version"]:
            raise Stop(EXIT_EXPORT, f"data_version różne: przypisania {dvs['assignments']}, karty {dvs['cards']}, "
                                    f"baza {info['data_version']}")
        rows = pd.read_csv(paths["assignments"], dtype=str)
        rv = set(rows["rules_version"])
        if rv != {info["rules_version"]}:
            raise Stop(EXIT_EXPORT, f"{paths['assignments'].name}: rules_version {sorted(rv)}")
        week = rows[(rows["event_date"] >= self.mon.isoformat()) & (rows["event_date"] <= self.sun.isoformat())]
        self.facts.update(export=paths, export_sha={k: sha256(p) for k, p in paths.items()},
                          rules_version=info["rules_version"], data_version=info["data_version"],
                          events=int(week["event_id"].nunique()), lists=len(week),
                          export_events=dict(zip(rows["event_id"], rows["event_date"])))
        if len(week) == 0:
            raise Stop(EXIT_EXPORT, f"eksport nie ma list z tygodnia {self.week} ({self.mon} .. {self.sun})")

    def verify_mtgo(self) -> None:
        snaps = self.work / "snapshots"
        if snaps.exists():
            shutil.rmtree(snaps)
        listed, months = mtgo_check.fetch_listings(self.mon, self.sun, snaps, self.today)
        v = mtgo_check.verify(self.mon, self.sun, listed, self.facts["export_events"])
        v.months = months
        for w in v.warnings:
            self.log.warn(w)
        self.facts["mtgo"] = v

    def pipeline(self) -> None:
        cfg_text = git_out(REPO, "show", f"{self.facts['config_commit']}:{CONFIG_REL}")
        cfg = {r["key"]: r["value"] for r in csv.DictReader(io.StringIO(cfg_text))}
        cfg["export_path"] = self.facts["export"]["assignments"].as_posix()  # the week's parameters, nothing else
        cfg["cards_path"] = self.facts["export"]["cards"].as_posix()
        cfg_path = self.work / "deckdb_config.csv"
        with cfg_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["key", "value"])
            w.writerows(cfg.items())
        base = self.work / "pipeline"
        if base.exists():
            shutil.rmtree(base)
        cmd = [self.cfg["pipeline_python"], REPO / "src" / "metagame_input_generator.py", "--input-mode", "deckdb",
               "--as-of", self.sun.isoformat(), "--trend-weeks", self.cfg["trend_weeks"], "--deckdb-config", cfg_path,
               "--outputs-base", base, "--deckdb-cache-dir", REPO / "outputs" / "cache" / "mtgo_json" / "modern"]
        if self.test_monthly:
            cmd += ["--presentation-month", self.sun.strftime("%Y-%m")]
        run(cmd, REPO, self.work / "logs" / "pipeline.log", EXIT_PIPELINE, "pipeline (input mode deckdb)")
        src = base / "presentation" / self.week
        if not (src / "data.xlsx").exists() or not (src / "notes.md").exists():
            raise Stop(EXIT_PIPELINE, f"pipeline nie zapisał folderu {src} (data.xlsx, notes.md)")
        self.facts["pipeline_cmd"] = " ".join(str(c) for c in cmd)
        self.facts["presentation_src"] = src
        notes = (src / "notes.md").read_text(encoding="utf-8")
        m = re.search(r"^- Monthly material: (.+)$", notes, re.M)
        self.facts["monthly"] = m.group(1) if m else None

    def folder(self) -> None:
        src: Path = self.facts["presentation_src"]
        self.dir.mkdir(parents=True, exist_ok=True)
        for p in self.data_outputs():  # --force (checked at the start): the previous data outputs go
            p.unlink()
        files = sorted(p for p in src.iterdir() if p.is_file())
        if any(p.is_dir() for p in src.iterdir()):
            raise Stop(EXIT_FOLDER, f"{src} ma podkatalogi; folder tygodnia jest płaski")
        for p in files + sorted((self.work / "snapshots").glob("mtgo_snapshot_*.html")):
            shutil.copy2(p, self.dir / p.name)
        bad = [p.name for p in files if sha256(p) != sha256(self.dir / p.name)]
        if bad:
            raise Stop(EXIT_FOLDER, f"kopia do {self.dir} różni się od {src}: {bad}")
        self.facts["files"] = [p.name for p in files]

    # --- BUILD --------------------------------------------------------------------------------------------------

    def build(self) -> None:
        """build/README_workflow.md with weekly/<week>/out/ as the working directory of the README (copies of
        data.xlsx and notes.md in src/, the PNGs, notes.md and the content files; results next to them). The scripts
        run from build/ as they are."""
        build = REPO / "build"
        n = self.mon.isocalendar()[1]
        missing = [f"build/{s}" for s in BUILD_SCRIPTS if not (build / s).exists()]
        if missing:
            raise Stop(EXIT_BUILD, f"brak skryptów buildu: {', '.join(missing)} (wgraj je z projektu Claude)")
        self.facts["build_sha"] = {p.name: sha256(p) for p in sorted(build.iterdir()) if p.is_file()}
        if not (self.dir / "data.xlsx").exists() or not (self.dir / "notes.md").exists():
            raise Stop(EXIT_BUILD, f"{self.dir} nie ma materiałów etapu DATA (najpierw --stage data)")
        missing = [f.format(n=n) for f in CONTENT_FILES if not (self.dir / f.format(n=n)).exists()]
        if missing:
            raise Stop(EXIT_BUILD, f"brak plików treści w {self.dir}: {', '.join(missing)}")
        m = re.search(r"^- Events: (\d+);", (self.dir / "notes.md").read_text(encoding="utf-8"), re.M)
        if not m:
            raise Stop(EXIT_BUILD, f"{self.dir / 'notes.md'}: brak linii '- Events: <n>;' (liczba eventów dla qa.py)")
        events = int(m.group(1))
        node_env = self._build_preflight(build)
        out = self.dir / "out"
        if out.exists():
            shutil.rmtree(out)
        (out / "src").mkdir(parents=True)
        for name in ("data.xlsx", "notes.md"):
            shutil.copy2(self.dir / name, out / "src" / name)
        for p in [self.dir / "notes.md", *sorted(self.dir.glob("*.png"))] + [self.dir / f.format(n=n)
                                                                           for f in CONTENT_FILES]:
            shutil.copy2(p, out / p.name)
        py = self.cfg["pipeline_python"]
        steps = [("analysis.py", [py, build / "analysis.py", "--week", n, "--src", "src", "--out", "."], None),
                 ("build_deck.js", [node_env["node"], build / "build_deck.js", n], node_env),
                 ("gen_dossier.py", [py, build / "gen_dossier.py", n], None),
                 ("build_docs.py", [py, build / "build_docs.py", "--week", n, "--src", ".", "--png", ".", "--out", "."],
                  None),
                 ("qa.py", [py, build / "qa.py", "--week", n, "--events", events, "--src", ".", "--dir", "."], None)]
        log = self.work / "logs" / "build.log"
        for name, cmd, env in steps:
            self.log.run(f"build: {name}", self._build_step, name, cmd, out, log, env)
            if name == "analysis.py" and not (out / "stats.json").exists():
                raise Stop(EXIT_BUILD, "analysis.py nie zapisał stats.json")
        self.facts["build_out"] = sorted(p.name for p in out.iterdir() if p.suffix in (".pptx", ".docx"))

    def _build_preflight(self, build: Path) -> dict:
        node, npm = shutil.which("node"), shutil.which("npm")
        if node is None or npm is None:
            raise Stop(EXIT_BUILD, "brak Node.js (node / npm) w PATH")
        root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
        env = {"NODE_PATH": root}
        ver = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        p = subprocess.run([node, "-e", "require('pptxgenjs')"], cwd=str(build), capture_output=True, text=True,
                           env={**__import__("os").environ, **env})
        if p.returncode != 0:
            raise Stop(EXIT_BUILD, f"pptxgenjs niedostępny dla node ({root}): npm install -g pptxgenjs")
        versions = {"node": ver}
        for pkg in ("python-docx", "python-pptx", "pillow", "defusedxml", "lxml"):
            try:
                versions[pkg] = md.version(pkg)
            except md.PackageNotFoundError:
                raise Stop(EXIT_BUILD, f"brak pakietu {pkg} (pip install {pkg})") from None
        # qa.py calls the pptx validator by the absolute path of the Claude project; on Windows "/mnt/..." resolves
        # on the drive of the working directory
        validator = (Path(self.dir.drive + "/") / "mnt" / "skills" / "public" / "pptx" / "scripts" / "office"
                     / "validate.py")
        if not validator.exists():
            raise Stop(EXIT_BUILD, f"brak {validator} (walidator pptx wywoływany przez qa.py): załóż junction do "
                                   "skilla pptx, docs/runner.md, sekcja 'Etap BUILD'")
        self.facts["build_versions"] = versions
        return {"node": node, **env}

    def _build_step(self, name: str, cmd: list, cwd: Path, log: Path, env: Optional[dict]) -> None:
        import os

        env = {k: v for k, v in (env or {}).items() if k != "node"}
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                           errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **env})
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(str(c) for c in cmd)}\n(exit {p.returncode})\n{p.stdout}\n{p.stderr}\n")
        print(p.stdout.rstrip(), flush=True)
        if name == "qa.py":
            lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
            self.facts["qa"] = lines
            fails = [ln for ln in lines if re.match(r"FAIL\b", ln)]
            if fails or p.returncode != 0:
                raise Stop(EXIT_BUILD, f"qa.py: {len(fails)} FAIL (kod {p.returncode})\n" + "\n".join(fails[:30])
                           + ("" if fails else "\n" + (p.stderr or p.stdout).strip()[-1500:]))
        elif p.returncode != 0:
            raise Stop(EXIT_BUILD, f"build/{name}: kod wyjścia {p.returncode} (log {log})\n"
                       + "\n".join((p.stdout + "\n" + p.stderr).strip().splitlines()[-20:]))


def blocking_message(state: dict) -> str:
    lines = [f"triage DeckDB: pending_blocking, {state['pending_groups']} grup(a) czeka na decyzję, razem "
             f"{state['waiting_share_of_week']:.1%} z {state['week']['lists']} list tygodnia {state['week']['iso_week']}"
             f" (próg {state['blocking']['share_threshold']:.0%} albo grupa czekająca > "
             f"{state['blocking']['days_threshold']} dni):"]
    for g in state["waiting"]:
        lines.append(f"  {g['item_key']}: {g['lists']} list ({g['lists_in_week']} w tygodniu, {g['share_of_week']:.1%}),"
                     f" w kolejce od {g['added_to_queue']} ({g['days_waiting']} dni), {g['state']}")
    lines.append("Co zrobić: rozstrzygnij te grupy w aplikacji przeglądu DeckDB, zastosuj decyzje (cykl /tydzien: "
                 "log_check --pull, apply-decisions, freeze_cores, build-review-queue --revise-changed) i uruchom runner "
                 "ponownie. Folder tygodnia nie powstał.")
    return "\n".join(lines)


def _fmt_s(sec: float) -> str:
    return f"{sec:.0f} s" if sec < 120 else f"{sec / 60:.1f} min"


def report(r: WeekRun, stage: str, stop: Optional[Stop]) -> str:
    f = r.facts
    out = [f"# Raport runnera {r.week}", "",
           f"- Tydzień: {r.week}, okno {r.mon} .. {r.sun}; etap: {stage}; "
           f"wynik: {'OK' if stop is None else f'STOP (kod {stop.code})'}",
           f"- Uruchomienie: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    if stop is not None:
        out += ["", "## Błąd", "", "```", stop.message, "```"]
    if "pipeline_commit" in f:
        out += [f"- Commity: pipeline `{f['pipeline_commit'][:12]}`, DeckDB `{f['deckdb_commit'][:12]}`; "
                f"config `{CONFIG_REL}` z commitu `{f['config_commit'][:12]}`"]
    if "rules_version" in f:
        out += [f"- Wersja reguł DeckDB: `{f['rules_version']}`; data_version `{f['data_version']}`",
                f"- Eksport przypisań: `{f['export']['assignments'].name}`, sha256 `{f['export_sha']['assignments']}`",
                f"- Eksport kart: `{f['export']['cards'].name}`, sha256 `{f['export_sha']['cards']}`",
                f"- Eventy i listy tygodnia (eksport): {f['events']} eventów, {f['lists']} list Top 32"]
    if "triage" in f:
        t = f["triage"]
        out += [f"- Triage: {t['status']}, {t['pending_groups']} grup czekających "
                f"({t['waiting_share_of_week']:.1%} list {t['week']['iso_week']}); plik `{Path(f['triage_path']).name}`"]
    if "pipeline_cmd" in f:
        monthly = f["monthly"] or ""
        out += ["- Materiał miesięczny: " + ("brak (tydzień nie zawiera pierwszego dnia miesiąca)"
                                             if not monthly or monthly.startswith("not included")
                                             else f"tak, {monthly} (z notes.md)")]
    if "mtgo" in f:
        v = f["mtgo"]
        out += ["", "## Weryfikacja mtgo.com", "",
                f"Strony: {', '.join(f'mtgo_snapshot_{m}.html' for m in v.months)}. Ograniczenie: DeckDB importuje "
                "z tych samych stron mtgo.com, więc kontrola wyłapuje braki importu i eksportu, nie braki samego "
                "mtgo.com.", "",
                "| EventID | plik | mtgo.com | data DeckDB | data mtgo.com | rozmiar |", "|---|---|---|---|---|---|"]
        for row in v.rows:
            out.append(f"| {row['event_id']} | {'tak' if row['in_file'] else 'nie'} | "
                       f"{'tak' if row['on_mtgo'] else 'nie'} | {row['date_file'] or '-'} | "
                       f"{row['date_mtgo'] or '-'} | {row['size'] or '-'} |")
        out += ["", f"Zgodne ID: {sum(1 for x in v.rows if x['in_file'] and x['on_mtgo'])} z {len(v.rows)}."]
        for label, nb in (("Ostatni przed oknem", v.before), ("Pierwszy po oknie", v.after)):
            out.append(f"- {label}: " + (f"{nb['event_id']} (mtgo.com {nb['date_mtgo']}, Challenge {nb['size']}, "
                                         f"w eksporcie {nb['date_file'] or 'brak'}), "
                                         f"{'poza oknem' if nb['outside'] else 'W OKNIE'}" if nb
                                         else "brak na pobranych stronach"))
    if r.log.warnings:
        out += ["", "## Ostrzeżenia", ""] + [f"- {w}" for w in r.log.warnings]
    if "files" in f:
        out += ["", "## Pliki", "", ", ".join(f["files"])]
    if "build_sha" in f:
        out += ["", "## Skrypty build/ (sha256)", ""] + [f"- `{k}`: `{v}`" for k, v in f["build_sha"].items()]
    if "qa" in f:
        out += ["", "## qa.py", "", "```"] + f["qa"] + ["```"]
    if "build_out" in f:
        out += ["", f"Pliki w out/: {', '.join(f['build_out'])}"]
    if "build_versions" in f:
        out += ["", "Wersje buildu: " + ", ".join(f"{k} {v}" for k, v in f["build_versions"].items())]
    out += ["", "## Kroki", "", "| krok | czas | wynik |", "|---|---|---|"]
    out += [f"| {n} | {_fmt_s(s)} | {res} |" for n, s, res in r.log.steps]
    if "versions" in f:
        out += ["", "Wersje: " + ", ".join(f"{k} {v}" for k, v in f["versions"].items())]
    if "pipeline_cmd" in f:
        out += ["", f"Komenda pipeline'u: `{f['pipeline_cmd']}`"]
    return "\n".join(out) + "\n"


def append_run(r: WeekRun, stage: str, stop: Optional[Stop]) -> None:
    path = r.weekly_root / "runs.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    f = r.facts
    with path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(RUNS_COLUMNS)
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r.week, stage,
                    "OK" if stop is None else f"STOP {stop.code}", (f.get("export_sha") or {}).get("assignments", ""),
                    f.get("rules_version", ""), f.get("events", ""), f.get("lists", ""),
                    (f.get("triage") or {}).get("status", "")])


def main(argv: Optional[List[str]] = None, today: Optional[date] = None) -> int:
    today = today or date.today()
    ap = argparse.ArgumentParser(prog="metagame_week.py", description="Runner tygodnia (docs/runner.md)")
    ap.add_argument("--week", default=None, help="RRRR-Wnn, domyślnie ostatni zakończony tydzień ISO")
    ap.add_argument("--stage", choices=["data", "build", "all"], default="data")
    ap.add_argument("--force", action="store_true", help="zastąp materiały istniejącego folderu tygodnia")
    ap.add_argument("--test-monthly", action="store_true",
                    help="testowy materiał miesięczny za bieżący, niepełny miesiąc ('incomplete month')")
    ap.add_argument("--weekly-root", default=str(REPO / "weekly"), help=argparse.SUPPRESS)  # tests
    a = ap.parse_args(argv)
    week = a.week or last_complete_week(today)
    try:
        r = WeekRun(week.upper(), Path(a.weekly_root), a.force, a.test_monthly, today)
    except Stop as e:
        print(f"STOP [{e.code}]: {e.message}", file=sys.stderr)
        return e.code
    stop: Optional[Stop] = None
    try:
        if a.stage in ("data", "all"):
            r.log.run("argumenty i folder", r.check_args)
            r.log.run("preflight", r.preflight)
            r.log.run("pobranie i import (DeckDB)", r.import_lists)
            r.log.run("triage (DeckDB)", r.triage)
            r.log.run("eksport (DeckDB)", r.export)
            r.log.run("weryfikacja mtgo.com", r.verify_mtgo)
            r.log.run("pipeline", r.pipeline)
            r.log.run("folder tygodnia", r.folder)
        if a.stage in ("build", "all"):
            if not r.cfg:
                r.cfg = load_runner_config()
            r.log.run("build", r.build)
    except Stop as e:
        stop = e
        print(f"\nSTOP [{e.code}]: {e.message}", file=sys.stderr)
    except Exception:  # noqa: BLE001 -- unexpected: still a stop, with the traceback in the report
        import traceback

        stop = Stop(EXIT_UNEXPECTED, "nieoczekiwany błąd\n" + traceback.format_exc())
        print(f"\nSTOP [{stop.code}]: {stop.message}", file=sys.stderr)
    text = report(r, a.stage, stop)
    if stop is not None:
        dest = r.work / "run_report_STOP.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    elif a.stage == "build":  # the DATA report stays; the build section is appended to it
        dest = r.dir / "run_report.md"
        body = text.split("\n", 1)[1]
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(f"\n# Etap BUILD ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n{body}")
    else:
        dest = r.dir / "run_report.md"
        dest.write_text(text, encoding="utf-8")
    if stop is None or stop.code != EXIT_ARGS:
        append_run(r, a.stage, stop)
    print(f"\nRaport: {dest}")
    return 0 if stop is None else stop.code
