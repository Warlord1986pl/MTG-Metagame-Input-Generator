from __future__ import annotations

import csv
import json
import sys
import traceback
from argparse import Namespace
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import List, Optional
from urllib.error import URLError
from urllib.request import urlopen

import pandas as pd
from PySide6.QtCore import QDate, QObject, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

try:
    from metagame_input_generator import (
        GenerationRunResult,
        fetch_available_decks,
        normalize_name,
        parse_args,
        run_generation,
    )
except ModuleNotFoundError:
    from .metagame_input_generator import (
        GenerationRunResult,
        fetch_available_decks,
        normalize_name,
        parse_args,
        run_generation,
    )

try:
    from statistics_engine import StatisticsRunResult, rebuild_history_from_dirs, run_statistics
except ModuleNotFoundError:
    from .statistics_engine import StatisticsRunResult, rebuild_history_from_dirs, run_statistics

try:
    from challenge_history_engine import (
        load_challenge_history,
        rebuild_challenge_history_from_dirs,
        run_challenge_statistics,
    )
except ModuleNotFoundError:
    from .challenge_history_engine import (
        load_challenge_history,
        rebuild_challenge_history_from_dirs,
        run_challenge_statistics,
    )

try:
    from change_model import ChangeModel, ConfigPaths, load_archetype_catalog, remove_archetype, upsert_archetype_catalog
except ModuleNotFoundError:
    from .change_model import ChangeModel, ConfigPaths, load_archetype_catalog, remove_archetype, upsert_archetype_catalog


APP_NAME = "MTG Metagame Studio"
ORG_NAME = "GitHubCopilot"
SPECIAL_MY_DECK = "My Deck (force 50%)"
STATS_PALETTE_OPTIONS = ["classic", "warm", "neon", "colorblind"]
STATS_OUTPUT_PROFILES = ["full", "light"]
STATS_LEGEND_KEYS = [
    "Underplayed Winner",
    "Popular Trap",
    "Neutral",
    "Very High Prep Priority",
    "High Prep Priority",
    "Medium Prep Priority",
    "Low Prep Priority",
    "High My Deck WR",
    "Mid My Deck WR",
    "Low My Deck WR",
    "Low Record Chance",
    "Mid Record Chance",
    "High Record Chance",
    "Low Deck WR",
    "Mid Deck WR",
    "High Deck WR",
    "Rising Deck",
    "Falling Deck",
    "Stable",
    "Trend Box",
    "Custom Deck Override",
]
FORMAT_OPTIONS = [
    "Modern",
    "Standard",
    "Pioneer",
    "Legacy",
    "Vintage",
    "Pauper",
    "Commander",
    "Duel Commander",
    "Timeless",
    "Explorer",
    "Historic",
    "Brawl",
    "Alchemy",
]
RESULT_TABLE_HEADERS = ["Raw Name", "Deck", "Archetype", "Meta", "My WR"]
REVIEW_QUEUE_HEADERS = ["Reason", "Raw Name", "Deck", "Archetype", "Meta", "My WR"]


class GeneratorWorker(QObject):
    progress = Signal(str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, args: Namespace) -> None:
        super().__init__()
        self._args = args

    def run(self) -> None:
        try:
            results = run_generation(self._args, log=self.progress.emit)
        except Exception as err:
            details = "\n".join(
                [str(err).strip(), "", traceback.format_exc().strip()]
            ).strip()
            self.failed.emit(details)
            return
        self.finished.emit(results)


class DeckListWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, format_name: str, week_start: str, week_end: str, limit: int, backup_url: str = "") -> None:
        super().__init__()
        self._format_name = format_name
        self._week_start = week_start
        self._week_end = week_end
        self._limit = limit
        self._backup_url = backup_url

    def run(self) -> None:
        try:
            from metagame_input_generator import configure_backup_server_url
        except ImportError:
            from .metagame_input_generator import configure_backup_server_url
        configure_backup_server_url(self._backup_url)
        try:
            decks = fetch_available_decks(
                format_name=self._format_name,
                start_date=date.fromisoformat(self._week_start),
                end_date=date.fromisoformat(self._week_end),
                limit=self._limit,
            )
        except Exception as err:
            self.failed.emit(str(err).strip() or "Failed to load deck list.")
            return
        self.finished.emit(decks)


class StatisticsWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        input_excel: Path,
        output_dir: Path,
        history_csv: Path,
        total_players: int,
        rounds: int,
        min_encounter_pct: float,
        player_deck_name: str,
        player_winrate: float,
        weeks_back: int,
        output_profile: str,
        palette_name: str,
        deck_colors: dict[str, str],
        legend_colors: dict[str, str],
    ) -> None:
        super().__init__()
        self._input_excel = input_excel
        self._output_dir = output_dir
        self._history_csv = history_csv
        self._total_players = total_players
        self._rounds = rounds
        self._min_encounter_pct = min_encounter_pct
        self._player_deck_name = player_deck_name
        self._player_winrate = player_winrate
        self._weeks_back = weeks_back
        self._output_profile = output_profile
        self._palette_name = palette_name
        self._deck_colors = deck_colors
        self._legend_colors = legend_colors

    def run(self) -> None:
        try:
            result = run_statistics(
                input_excel=self._input_excel,
                output_dir=self._output_dir,
                history_csv=self._history_csv,
                total_players=self._total_players,
                rounds=self._rounds,
                min_encounter_pct=self._min_encounter_pct,
                player_deck_name=self._player_deck_name,
                player_winrate=self._player_winrate,
                weeks_back=self._weeks_back,
                output_profile=self._output_profile,
                palette_name=self._palette_name,
                deck_colors=self._deck_colors,
                legend_colors=self._legend_colors,
                log=self.progress.emit,
            )
        except Exception as err:
            details = "\n".join([
                str(err).strip(),
                "",
                traceback.format_exc().strip(),
            ]).strip()
            self.failed.emit(details)
            return
        self.finished.emit(result)


class ChallengeStatisticsWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        history_csv: Path,
        output_dir: Path,
        format_name: str,
        last_n_events: int,
        week_start,
        week_end,
    ) -> None:
        super().__init__()
        self._history_csv = history_csv
        self._output_dir = output_dir
        self._format_name = format_name
        self._last_n_events = last_n_events
        self._week_start = week_start
        self._week_end = week_end

    def run(self) -> None:
        try:
            completeness = None
            if self._week_start is not None and self._week_end is not None:
                # This is a "recompute stats only" path -- it never re-fetches Challenge
                # decklists, so it has no completeness signal of its own. Previously it called
                # run_challenge_statistics() without one at all, which silently defaulted to
                # complete=true (the exact bug this now guards against: a status.json claiming
                # success while having verified nothing). verify_registry_against_history() is
                # cheap -- one mtgo.com registry-index fetch, no per-event decklist fetch -- and
                # gives a real signal by diffing the registry against what's actually already in
                # history_csv for this window.
                try:
                    from challenge_mtgo_source import verify_registry_against_history
                except ModuleNotFoundError:
                    from .challenge_mtgo_source import verify_registry_against_history
                self.progress.emit("[challenge-stats] verifying window against mtgo.com registry...")
                completeness = verify_registry_against_history(
                    format_name=self._format_name,
                    start_date=self._week_start,
                    end_date=self._week_end,
                    history_csv=self._history_csv,
                    log=self.progress.emit,
                )
            result = run_challenge_statistics(
                history_csv=self._history_csv,
                output_dir=self._output_dir,
                format_name=self._format_name,
                last_n_events=self._last_n_events,
                week_start=self._week_start,
                week_end=self._week_end,
                log=self.progress.emit,
                completeness=completeness,
            )
        except Exception as err:
            details = "\n".join([str(err).strip(), "", traceback.format_exc().strip()]).strip()
            self.failed.emit(details)
            return
        self.finished.emit(result)


class ChallengeRescanWorker(QObject):
    """Scans both persisted history files for any Deck==NEEDS_MANUAL_REVIEW row, from any source
    (a normal run, a migration script, a manual edit) -- not just the most recently completed run.
    """
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        challenge_history_csv: Path,
        premier_history_csv: Optional[Path],
        cache_dir: Path,
        format_name: str,
        rules_csv: Path,
        challenge_mapping_csv: Path,
    ) -> None:
        super().__init__()
        self._challenge_history_csv = challenge_history_csv
        self._premier_history_csv = premier_history_csv
        self._cache_dir = cache_dir
        self._format_name = format_name
        self._rules_csv = rules_csv
        self._challenge_mapping_csv = challenge_mapping_csv

    def run(self) -> None:
        try:
            try:
                from challenge_mtgo_source import scan_all_history_for_review
                from metagame_input_generator import load_rules, load_user_deck_mappings
            except ModuleNotFoundError:
                from .challenge_mtgo_source import scan_all_history_for_review
                from .metagame_input_generator import load_rules, load_user_deck_mappings

            rules = load_rules(self._rules_csv)
            challenge_mappings = load_user_deck_mappings(self._challenge_mapping_csv)
            items = scan_all_history_for_review(
                self._challenge_history_csv,
                self._premier_history_csv,
                self._cache_dir,
                self._format_name,
                rules,
                challenge_mappings,
                log=self.progress.emit,
            )
            review_items = [
                {
                    "event_id": it.event_id, "date": it.date, "format": it.format_name, "kind": it.kind,
                    "tier": it.size, "place": it.place, "pilot": it.pilot, "reason": it.reason,
                    "nearest_label": it.nearest_label or "", "nearest_sim": it.nearest_sim,
                    "second_label": it.second_label or "", "second_sim": it.second_sim,
                    "key_cards": ", ".join(f"{name} x{qty}" for name, qty in it.key_cards),
                    "event_url": it.url,
                }
                for it in items
            ]
        except Exception as err:
            details = "\n".join([str(err).strip(), "", traceback.format_exc().strip()]).strip()
            self.failed.emit(details)
            return
        self.finished.emit(review_items)


class ReviewQueueDialog(QDialog):
    rowActivated = Signal(int)
    rowUpdated = Signal(int, str, str, str)
    saveRequested = Signal(object)

    def __init__(
        self,
        rows: List[dict[str, str]],
        deck_options: List[str],
        archetype_options: List[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._rows = rows
        self._deck_options = deck_options
        self._archetype_options = archetype_options
        self.setWindowTitle("Review Queue")
        self.resize(980, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel(
            "Rows flagged for quick review: short names, unknown/non-catalog archetypes, or entries that appear for the first time. "
            "Double-click a row or use the button below to jump to it in the main editor."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QTableWidget(0, len(REVIEW_QUEUE_HEADERS))
        self.table.setHorizontalHeaderLabels(REVIEW_QUEUE_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._emit_current_row)
        layout.addWidget(self.table, 1)

        button_row = QHBoxLayout()
        self.save_selected_button = QPushButton("Save Selected")
        self.save_selected_button.clicked.connect(self._emit_save_selected)
        self.save_all_button = QPushButton("Save All Visible")
        self.save_all_button.clicked.connect(self._emit_save_all)
        self.go_to_button = QPushButton("Go To Selected Row")
        self.go_to_button.clicked.connect(self._emit_current_row)
        button_row.addWidget(self.save_selected_button)
        button_row.addWidget(self.save_all_button)
        button_row.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        button_row.addWidget(self.go_to_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.set_rows(rows)

    def set_rows(self, rows: List[dict[str, str]]) -> None:
        self._rows = rows
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            reason_item = QTableWidgetItem(str(row.get("reason") or ""))
            raw_item = QTableWidgetItem(str(row.get("raw_deck") or ""))
            meta_item = QTableWidgetItem(str(row.get("meta") or ""))
            wr_item = QTableWidgetItem(str(row.get("my_wr") or ""))
            meta_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            wr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if "Unknown" in str(row.get("reason") or ""):
                for item in [reason_item, raw_item, meta_item, wr_item]:
                    item.setBackground(QColor("#fff6bf"))
            self.table.setItem(row_index, 0, reason_item)
            self.table.setItem(row_index, 1, raw_item)
            self.table.setItem(row_index, 4, meta_item)
            self.table.setItem(row_index, 5, wr_item)

            deck_combo = QComboBox()
            deck_combo.setEditable(True)
            deck_combo.addItems(self._deck_options)
            deck_combo.setCurrentText(str(row.get("deck") or ""))
            deck_combo.currentTextChanged.connect(
                lambda text, idx=row_index: self._on_value_changed(idx, deck=text)
            )
            self.table.setCellWidget(row_index, 2, deck_combo)

            archetype_combo = QComboBox()
            archetype_combo.setEditable(True)
            archetype_combo.addItems(self._archetype_options)
            archetype_combo.setCurrentText(str(row.get("archetype") or ""))
            archetype_combo.currentTextChanged.connect(
                lambda text, idx=row_index: self._on_value_changed(idx, archetype=text)
            )
            if str(row.get("archetype") or "").strip().lower() in {"unknown", "unkown"}:
                archetype_combo.setStyleSheet("QComboBox { background-color: #fff6bf; }")
            self.table.setCellWidget(row_index, 3, archetype_combo)
        if rows:
            self.table.selectRow(0)

    def set_options(self, deck_options: List[str], archetype_options: List[str]) -> None:
        self._deck_options = deck_options
        self._archetype_options = archetype_options

    def _current_row_payload(self, row_index: int) -> Optional[dict[str, str]]:
        if row_index < 0 or row_index >= len(self._rows):
            return None

        payload = dict(self._rows[row_index])
        deck_widget = self.table.cellWidget(row_index, 2)
        if isinstance(deck_widget, QComboBox):
            payload["deck"] = deck_widget.currentText().strip()

        archetype_widget = self.table.cellWidget(row_index, 3)
        if isinstance(archetype_widget, QComboBox):
            payload["archetype"] = archetype_widget.currentText().strip()

        self._rows[row_index].update(payload)
        return payload

    def _all_current_payloads(self) -> List[dict[str, str]]:
        payloads: List[dict[str, str]] = []
        for row_index in range(len(self._rows)):
            payload = self._current_row_payload(row_index)
            if payload is not None:
                payloads.append(payload)
        return payloads

    def _on_value_changed(self, row_index: int, deck: Optional[str] = None, archetype: Optional[str] = None) -> None:
        if row_index < 0 or row_index >= len(self._rows):
            return
        if deck is not None:
            self._rows[row_index]["deck"] = str(deck).strip()
        if archetype is not None:
            archetype_text = str(archetype).strip()
            self._rows[row_index]["archetype"] = archetype_text
            widget = self.table.cellWidget(row_index, 3)
            if isinstance(widget, QComboBox):
                if archetype_text.lower() in {"unknown", "unkown"}:
                    widget.setStyleSheet("QComboBox { background-color: #fff6bf; }")
                else:
                    widget.setStyleSheet("")
        self.rowUpdated.emit(
            int(self._rows[row_index].get("source_row", -1)),
            str(self._rows[row_index].get("raw_deck") or ""),
            str(self._rows[row_index].get("deck") or ""),
            str(self._rows[row_index].get("archetype") or ""),
        )

    def _emit_current_row(self, *_args) -> None:
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self._rows):
            return
        try:
            source_row = int(self._rows[row_index].get("source_row", -1))
        except Exception:
            source_row = -1
        if source_row >= 0:
            self.rowActivated.emit(source_row)

    def _selected_payload(self) -> Optional[dict[str, str]]:
        row_index = self.table.currentRow()
        return self._current_row_payload(row_index)

    def _emit_save_selected(self) -> None:
        payload = self._selected_payload()
        if payload is not None:
            self.saveRequested.emit(payload)

    def _emit_save_all(self) -> None:
        self.saveRequested.emit(self._all_current_payloads())


class ChallengeReviewDialog(QDialog):
    """Decks the mtgo.com maindeck-signature classifier could not confidently label (place-1
    winners always land here, plus anything below the similarity/neighbor-agreement thresholds).
    Fill in Deck per row and Save -- writes straight into challenge_history_<format>.csv, no CSV
    export/import round trip needed.
    """

    HEADERS = ["Kind", "Date", "Tier", "Place", "Pilot", "Reason", "Nearest Guess", "Key Cards", "Deck"]

    def __init__(
        self,
        review_items: List[dict],
        deck_options: List[str],
        history_csv: Path,
        premier_history_csv: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._review_items = list(review_items)
        self._deck_options = deck_options
        self._history_csv = history_csv
        self._premier_history_csv = premier_history_csv
        self.setWindowTitle("Challenge Review Queue")
        self.resize(1150, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel(
            "Decks the classifier couldn't confidently label. Use Key Cards / Nearest Guess to identify "
            "the deck, fill in the Deck field, then Save & Apply. Rows you leave blank stay flagged for next time."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        button_row = QHBoxLayout()
        self.save_button = QPushButton("Save && Apply")
        self.save_button.clicked.connect(self._apply)
        button_row.addWidget(self.save_button)
        button_row.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self._populate()

    def _populate(self) -> None:
        self.table.setRowCount(len(self._review_items))
        for i, item in enumerate(self._review_items):
            kind = str(item.get("kind") or "challenge")
            kind_item = QTableWidgetItem(kind)
            if kind == "premier":
                kind_item.setBackground(QColor("#e0e8ff"))
            self.table.setItem(i, 0, kind_item)
            self.table.setItem(i, 1, QTableWidgetItem(str(item.get("date") or "")))
            self.table.setItem(i, 2, QTableWidgetItem(str(item.get("tier") or "")))
            place_item = QTableWidgetItem(str(item.get("place") or ""))
            place_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 3, place_item)
            self.table.setItem(i, 4, QTableWidgetItem(str(item.get("pilot") or "")))
            reason_item = QTableWidgetItem(str(item.get("reason") or ""))
            if str(item.get("place") or "") == "1":
                reason_item.setBackground(QColor("#fff6bf"))
            self.table.setItem(i, 5, reason_item)

            nearest = str(item.get("nearest_label") or "")
            second = str(item.get("second_label") or "")
            hint = nearest if nearest and nearest == second else f"{nearest} / {second}".strip(" /")
            self.table.setItem(i, 6, QTableWidgetItem(hint))
            self.table.setItem(i, 7, QTableWidgetItem(str(item.get("key_cards") or "")))

            deck_combo = QComboBox()
            deck_combo.setEditable(True)
            deck_combo.addItem("")
            deck_combo.addItems(self._deck_options)
            # Only pre-fill when both neighbors agreed (the classifier declined purely on
            # similarity, not on disagreement) -- a reasonable starting guess, never for place 1.
            if nearest and nearest == second and str(item.get("place") or "") != "1":
                deck_combo.setCurrentText(nearest)
            else:
                deck_combo.setCurrentText("")
            self.table.setCellWidget(i, 8, deck_combo)
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(7, QHeaderView.Stretch)

    def _apply(self) -> None:
        try:
            from challenge_history_engine import apply_challenge_review_selections
        except ModuleNotFoundError:
            from .challenge_history_engine import apply_challenge_review_selections

        selections_by_kind: dict[str, List[dict]] = {"challenge": [], "premier": []}
        for i, item in enumerate(self._review_items):
            widget = self.table.cellWidget(i, 8)
            deck_text = widget.currentText().strip() if isinstance(widget, QComboBox) else ""
            if deck_text:
                kind = str(item.get("kind") or "challenge")
                selections_by_kind.setdefault(kind, []).append(
                    {"event_id": str(item.get("event_id") or ""), "pilot": str(item.get("pilot") or ""), "resolved_deck": deck_text}
                )

        if not any(selections_by_kind.values()):
            QMessageBox.information(self, "Challenge Review", "No Deck values filled in -- nothing to apply.")
            return

        targets = {"challenge": self._history_csv, "premier": self._premier_history_csv}
        applied_keys: set[str] = set()
        msg_lines: List[str] = []
        had_error = False

        for kind, selections in selections_by_kind.items():
            if not selections:
                continue
            target_csv = targets.get(kind)
            if target_csv is None:
                msg_lines.append(f"{len(selections)} {kind} row(s) skipped: no history file configured for this kind.")
                had_error = True
                continue
            if not target_csv.exists():
                msg_lines.append(f"{len(selections)} {kind} row(s) skipped: history file does not exist ({target_csv}).")
                had_error = True
                continue
            try:
                result = apply_challenge_review_selections(selections, target_csv, log=None)
            except Exception as exc:
                msg_lines.append(f"{len(selections)} {kind} row(s) FAILED to save: {type(exc).__name__}: {exc}")
                had_error = True
                continue

            failed_keys = set(result["not_found"])
            for amb in result["ambiguous"]:
                failed_keys.add(amb.split(" (")[0])
            applied_keys |= {f"event_id={s['event_id']} pilot={s['pilot']!r}" for s in selections} - failed_keys

            msg_lines.append(f"{kind}: applied {result['applied']} correction(s) to {target_csv.name}.")
            if result["not_found"]:
                msg_lines.append(f"  {len(result['not_found'])} {kind} row(s) had no matching history row.")
            if result["ambiguous"]:
                msg_lines.append(f"  {len(result['ambiguous'])} {kind} row(s) matched more than one history row.")

        msg_lines.append("Re-run Challenge Statistics to refresh the xlsx with these corrections.")
        (QMessageBox.warning if had_error else QMessageBox.information)(self, "Challenge Review", "\n".join(msg_lines))

        remaining = [
            item for item in self._review_items
            if f"event_id={item.get('event_id') or ''} pilot={item.get('pilot') or ''!r}" not in applied_keys
        ]
        self._review_items = remaining
        self._populate()
        if not remaining:
            QMessageBox.information(self, "Challenge Review", "All flagged decks resolved.")
            self.close()


class UnknownArchetypeDialog(QDialog):
    """Modal shown before stats when the input XLSX contains Unknown archetypes."""

    def __init__(
        self,
        unknown_rows: List[dict],
        archetype_options: List[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unknown Archetypes — Review Before Statistics")
        self.resize(720, 460)
        self._rows = [dict(r) for r in unknown_rows]
        self._archetype_options = archetype_options
        self._skip_requested = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel(
            f"<b>{len(unknown_rows)} deck(s) have Unknown archetype</b> and would appear as 'Unknown' in statistics charts.<br>"
            "Assign archetypes below, then click <b>Save &amp; Continue</b> — assignments are saved to config and applied to the data file."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self.table = QTableWidget(len(self._rows), 3)
        self.table.setHorizontalHeaderLabels(["Deck", "Meta %", "Archetype"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(32)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)

        for row_idx, row in enumerate(self._rows):
            deck_item = QTableWidgetItem(str(row.get("deck") or ""))
            meta_item = QTableWidgetItem(str(row.get("meta") or ""))
            meta_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row_idx, 0, deck_item)
            self.table.setItem(row_idx, 1, meta_item)

            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(self._archetype_options)
            combo.setCurrentText("Unknown")
            combo.setStyleSheet("QComboBox { background-color: #fff6bf; }")
            combo.currentTextChanged.connect(
                lambda text, idx=row_idx: self._on_archetype_changed(idx, text)
            )
            self.table.setCellWidget(row_idx, 2, combo)

        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save Assignments & Continue")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self.accept)
        skip_btn = QPushButton("Continue Without Saving")
        skip_btn.clicked.connect(self._do_skip)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(skip_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _do_skip(self) -> None:
        self._skip_requested = True
        self.accept()

    def _on_archetype_changed(self, row_idx: int, text: str) -> None:
        self._rows[row_idx]["archetype"] = str(text).strip()
        widget = self.table.cellWidget(row_idx, 2)
        if isinstance(widget, QComboBox):
            if str(text).strip().lower() in {"unknown", ""}:
                widget.setStyleSheet("QComboBox { background-color: #fff6bf; }")
            else:
                widget.setStyleSheet("")

    def get_assignments(self) -> List[dict]:
        result = []
        for row_idx in range(len(self._rows)):
            widget = self.table.cellWidget(row_idx, 2)
            archetype = self._rows[row_idx].get("archetype", "Unknown")
            if isinstance(widget, QComboBox):
                archetype = widget.currentText().strip()
            if archetype and archetype.lower() not in {"unknown", ""}:
                result.append({
                    "deck": str(self._rows[row_idx].get("deck") or ""),
                    "archetype": archetype,
                })
        return result

    @property
    def skip_requested(self) -> bool:
        return self._skip_requested


_ORIGINAL_API_PROBE = "https://api.videreproject.com"


class ApiStatusWorker(QObject):
    """Checks both the original API reachability and the backup server /health."""

    finished = Signal(dict)  # {"original_status": "online"|"degraded"|"offline", "backup": dict|None}

    def __init__(self, backup_url: str) -> None:
        super().__init__()
        self._backup_url = backup_url  # already stripped of trailing slash

    def _health_urls(self) -> List[str]:
        return _candidate_backup_health_urls(self._backup_url)

    def run(self) -> None:
        # 1. Probe original API
        #    2xx/3xx/4xx → online (server responds normally)
        #    5xx          → degraded (server up but erroring)
        #    URLError / timeout → offline (unreachable)
        original_status = "offline"
        try:
            from urllib.error import HTTPError as _HTTPError
            from urllib.request import Request as _Request
            req = _Request(
                _ORIGINAL_API_PROBE,
                headers={"User-Agent": "MTG-Status-Check/1.0"},
                method="HEAD",
            )
            try:
                with urlopen(req, timeout=5):
                    original_status = "online"
            except _HTTPError as e:
                if e.code >= 500:
                    original_status = "degraded"
                else:
                    # 4xx — server reachable and working normally
                    original_status = "online"
        except Exception:
            pass  # URLError / timeout — genuinely unreachable

        # 2. Fetch backup /health
        backup_data: Optional[dict] = None
        if self._backup_url:
            for url in self._health_urls():
                try:
                    with urlopen(url, timeout=5) as resp:
                        backup_data = json.loads(resp.read().decode("utf-8"))
                        break
                except Exception:
                    continue

        self.finished.emit({"original_status": original_status, "backup": backup_data})


def _candidate_backup_health_urls(backup_url: str) -> List[str]:
    base = str(backup_url or "").strip().rstrip("/")
    if not base:
        return []

    candidates = [f"{base}/health"]
    if base.lower().endswith("/api"):
        candidates.append(f"{base[:-4].rstrip('/')}/health")

    seen: set[str] = set()
    deduped: List[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


class GlobalSearchDialog(QDialog):
    """Search and edit across ALL CSV source files from the editor combo simultaneously.

    Table uses plain QTableWidgetItem (fast, no per-row widgets). Editing happens in a
    side panel that appears when a row is selected — one set of widgets, not thousands.
    """

    navigate_to = Signal(str)
    save_changes = Signal(list)  # list of {raw_deck, deck, archetype, csv_path, is_challenge}

    _MAX_DISPLAY = 500  # cap rows shown without a search query to avoid initial freeze

    def __init__(
        self,
        sources: List[tuple],
        archetype_options: List[str],
        deck_options: List[str],
        config_dir: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Szukaj i edytuj we wszystkich plikach historii")
        self.setMinimumSize(1100, 620)
        self._sources = sources
        self._archetype_options = archetype_options
        self._deck_options = deck_options
        self._config_dir = config_dir
        self._all_rows: List[tuple] = []  # (label, csv_path_str, row_dict)
        self._result_rows: List[tuple] = []
        # table_row_idx → {deck, archetype} overrides
        self._modifications: dict[int, dict] = {}
        self._load_all()
        self._build_ui()

    def _load_all(self) -> None:
        for label, csv_path_str in self._sources:
            path = Path(csv_path_str)
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8", newline="") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        self._all_rows.append((label, csv_path_str, {
                            "raw_deck": str(
                                row.get("Source Deck Names") or row.get("Raw Deck") or row.get("Deck") or ""
                            ).strip(),
                            "deck": str(row.get("Deck") or "").strip(),
                            "archetype": str(row.get("Archetype") or "").strip(),
                            "meta": str(row.get("Meta") or "").strip(),
                        }))
            except Exception:
                pass

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setSpacing(12)

        # ── Left: search + table ──────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(6)

        search_row = QHBoxLayout()
        search_lbl = QLabel("Szukaj:")
        search_lbl.setFixedWidth(52)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Deck, raw name, archetype…  (wpisz min. 1 znak)")
        self._search_input.textChanged.connect(self._do_search)
        clear_btn = QPushButton("✕")
        clear_btn.setFixedWidth(30)
        clear_btn.setToolTip("Wyczyść")
        clear_btn.clicked.connect(self._search_input.clear)
        self._match_label = QLabel("")
        self._match_label.setFixedWidth(100)
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._search_input, 1)
        search_row.addWidget(clear_btn)
        search_row.addWidget(self._match_label)
        left.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_lbl = QLabel("Archetype:")
        filter_lbl.setFixedWidth(52)
        self._archetype_filter_combo = QComboBox()
        self._archetype_filter_combo.addItem("(wszystkie)")
        self._archetype_filter_combo.addItems(sorted(self._archetype_options))
        self._archetype_filter_combo.setToolTip("Filtruj wyniki do jednego archetypu (działa też bez wpisanej frazy)")
        self._archetype_filter_combo.currentTextChanged.connect(lambda _text: self._do_search())
        filter_row.addWidget(filter_lbl)
        filter_row.addWidget(self._archetype_filter_combo, 1)
        left.addLayout(filter_row)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Raw Name", "Deck", "Archetype", "Meta %", "Źródło"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.setSortingEnabled(True)
        self._table.setColumnWidth(0, 200)
        self._table.setColumnWidth(1, 180)
        self._table.setColumnWidth(2, 150)
        self._table.itemSelectionChanged.connect(lambda: self._on_row_selected(self._table.currentRow()))
        left.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        self._modified_label = QLabel("")
        self._modified_label.setFixedWidth(130)
        save_btn = QPushButton("Zapisz zmiany")
        save_btn.setObjectName("primaryButton")
        save_btn.setToolTip("Zapisz wszystkie zmodyfikowane wiersze do plików config")
        save_btn.clicked.connect(self._on_save_changes)
        go_btn = QPushButton("Przejdź do źródła")
        go_btn.setToolTip("Załaduj plik źródłowy wybranego wiersza w edytorze głównym")
        go_btn.clicked.connect(self._on_navigate)
        close_btn = QPushButton("Zamknij")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(self._modified_label)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(go_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        left.addLayout(btn_row)

        root.addLayout(left, 3)

        # ── Right: edit panel ─────────────────────────────────────────────
        edit_box = QGroupBox("Edycja zaznaczonego wiersza")
        edit_box.setFixedWidth(300)
        edit_layout = QVBoxLayout(edit_box)
        edit_layout.setSpacing(10)

        self._edit_raw_label = QLabel("—")
        self._edit_raw_label.setWordWrap(True)
        self._edit_raw_label.setObjectName("muted")

        self._edit_deck_combo = QComboBox()
        self._edit_deck_combo.setEditable(True)
        self._edit_deck_combo.addItems(self._deck_options)

        self._edit_arch_combo = QComboBox()
        self._edit_arch_combo.setEditable(True)
        self._edit_arch_combo.addItems(self._archetype_options)
        self._edit_arch_combo.currentTextChanged.connect(self._on_edit_arch_changed)

        self._edit_source_label = QLabel("—")
        self._edit_source_label.setWordWrap(True)
        self._edit_source_label.setObjectName("muted")

        apply_btn = QPushButton("Zastosuj do zaznaczonego")
        apply_btn.setToolTip("Zapisz edycję do listy zmian (jeszcze nie do pliku)")
        apply_btn.clicked.connect(self._apply_edit_to_row)

        apply_visible_btn = QPushButton("Przypisz archetype do wszystkich widocznych")
        apply_visible_btn.setToolTip("Przypisze wybrany archetype do wszystkich wierszy w tabeli")
        apply_visible_btn.clicked.connect(self._apply_arch_to_all_visible)

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Raw Name:", self._edit_raw_label)
        form.addRow("Deck:", self._edit_deck_combo)
        form.addRow("Archetype:", self._edit_arch_combo)
        form.addRow("Źródło:", self._edit_source_label)
        edit_layout.addLayout(form)
        edit_layout.addWidget(apply_btn)
        edit_layout.addWidget(apply_visible_btn)
        edit_layout.addStretch()

        hint = QLabel("Zaznacz wiersz w tabeli, zmień pola, kliknij 'Zastosuj'. Potem 'Zapisz zmiany'.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        edit_layout.addWidget(hint)

        root.addWidget(edit_box, 0)

        self._do_search("")

    def _populate_table(self, matches: List[tuple]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(matches))
        for i, (label, path, row) in enumerate(matches):
            arch_val = row.get("archetype", "")
            mod = self._modifications.get(i, {})

            raw_item = QTableWidgetItem(row.get("raw_deck", ""))
            raw_item.setData(Qt.UserRole, path)
            self._table.setItem(i, 0, raw_item)

            deck_display = mod.get("deck", row.get("deck", ""))
            deck_item = QTableWidgetItem(deck_display)
            if "deck" in mod:
                deck_item.setForeground(QColor("#0055cc"))
            self._table.setItem(i, 1, deck_item)

            arch_display = mod.get("archetype", arch_val)
            arch_item = QTableWidgetItem(arch_display)
            if arch_display.strip().lower() in ("unknown", ""):
                arch_item.setBackground(QColor("#fff3cd"))
            if "archetype" in mod:
                arch_item.setForeground(QColor("#0055cc"))
            self._table.setItem(i, 2, arch_item)

            self._table.setItem(i, 3, QTableWidgetItem(row.get("meta", "")))
            self._table.setItem(i, 4, QTableWidgetItem(label))

        self._table.setSortingEnabled(True)

    def _do_search(self, query: str = "") -> None:
        q = (query or self._search_input.text()).strip().lower()
        archetype_filter = self._archetype_filter_combo.currentText()
        if archetype_filter == "(wszystkie)":
            archetype_filter = ""
        self._modifications.clear()
        self._modified_label.setText("")

        if not q and not archetype_filter:
            # Show nothing until user types or picks an archetype — avoids rendering thousands of rows
            self._result_rows = []
            self._match_label.setText(f"Wpisz frazę ({len(self._all_rows)} wierszy łącznie)")
            self._table.setRowCount(0)
            self._clear_edit_panel()
            return

        matches = [
            (label, path, row) for label, path, row in self._all_rows
            if (
                not q
                or q in row.get("deck", "").lower()
                or q in row.get("raw_deck", "").lower()
                or q in row.get("archetype", "").lower()
            )
            and (
                not archetype_filter
                or row.get("archetype", "").strip().lower() == archetype_filter.strip().lower()
            )
        ]
        capped = matches[: self._MAX_DISPLAY]
        self._result_rows = capped

        total = len(matches)
        shown = len(capped)
        if total > shown:
            self._match_label.setText(f"{shown}/{total} (cap {self._MAX_DISPLAY})")
        else:
            self._match_label.setText(f"{shown}/{len(self._all_rows)}")

        self._populate_table(capped)
        self._clear_edit_panel()

    def _clear_edit_panel(self) -> None:
        self._edit_raw_label.setText("—")
        self._edit_deck_combo.blockSignals(True)
        self._edit_deck_combo.setCurrentText("")
        self._edit_deck_combo.blockSignals(False)
        self._edit_arch_combo.blockSignals(True)
        self._edit_arch_combo.setCurrentText("")
        self._edit_arch_combo.blockSignals(False)
        self._edit_source_label.setText("—")

    def _on_row_selected(self, row_idx: int) -> None:
        if row_idx < 0 or row_idx >= len(self._result_rows):
            self._clear_edit_panel()
            return
        _, path, row = self._result_rows[row_idx]
        mod = self._modifications.get(row_idx, {})
        self._edit_raw_label.setText(row.get("raw_deck", "—"))
        self._edit_deck_combo.blockSignals(True)
        self._edit_deck_combo.setCurrentText(mod.get("deck", row.get("deck", "")))
        self._edit_deck_combo.blockSignals(False)
        self._edit_arch_combo.blockSignals(True)
        self._edit_arch_combo.setCurrentText(mod.get("archetype", row.get("archetype", "")))
        self._edit_arch_combo.blockSignals(False)
        self._on_edit_arch_changed(self._edit_arch_combo.currentText())
        self._edit_source_label.setText(Path(path).name)

    def _on_edit_arch_changed(self, text: str) -> None:
        if text.strip().lower() in ("unknown", ""):
            self._edit_arch_combo.setStyleSheet("background-color: #fff3cd;")
        else:
            self._edit_arch_combo.setStyleSheet("")

    def _apply_edit_to_row(self) -> None:
        row_idx = self._table.currentRow()
        if row_idx < 0 or row_idx >= len(self._result_rows):
            QMessageBox.information(self, "Global Search", "Zaznacz najpierw wiersz w tabeli.")
            return
        _, _, orig = self._result_rows[row_idx]
        new_deck = self._edit_deck_combo.currentText().strip()
        new_arch = self._edit_arch_combo.currentText().strip()

        changed = {}
        if new_deck and new_deck != orig.get("deck", ""):
            changed["deck"] = new_deck
        if new_arch and new_arch != orig.get("archetype", ""):
            changed["archetype"] = new_arch

        if changed:
            self._modifications[row_idx] = changed
        elif row_idx in self._modifications:
            del self._modifications[row_idx]

        # Refresh just this row in the table
        _, path, row = self._result_rows[row_idx]
        mod = self._modifications.get(row_idx, {})
        deck_display = mod.get("deck", row.get("deck", ""))
        deck_item = QTableWidgetItem(deck_display)
        if "deck" in mod:
            deck_item.setForeground(QColor("#0055cc"))
        self._table.setSortingEnabled(False)
        self._table.setItem(row_idx, 1, deck_item)
        arch_display = mod.get("archetype", row.get("archetype", ""))
        arch_item = QTableWidgetItem(arch_display)
        if arch_display.strip().lower() in ("unknown", ""):
            arch_item.setBackground(QColor("#fff3cd"))
        if "archetype" in mod:
            arch_item.setForeground(QColor("#0055cc"))
        self._table.setItem(row_idx, 2, arch_item)
        self._table.setSortingEnabled(True)
        self._update_modified_label()

    def _apply_arch_to_all_visible(self) -> None:
        new_arch = self._edit_arch_combo.currentText().strip()
        if not new_arch:
            QMessageBox.warning(self, "Global Search", "Wybierz najpierw archetype w panelu po prawej.")
            return
        count = 0
        self._table.setSortingEnabled(False)
        for i in range(len(self._result_rows)):
            _, _, orig = self._result_rows[i]
            if new_arch != orig.get("archetype", ""):
                if i not in self._modifications:
                    self._modifications[i] = {}
                self._modifications[i]["archetype"] = new_arch
                arch_item = QTableWidgetItem(new_arch)
                arch_item.setForeground(QColor("#0055cc"))
                self._table.setItem(i, 2, arch_item)
                count += 1
        self._table.setSortingEnabled(True)
        self._update_modified_label()
        if count:
            QMessageBox.information(self, "Global Search", f"Przypisano archetype '{new_arch}' do {count} wierszy. Kliknij 'Zapisz zmiany' aby zapisać.")

    def _update_modified_label(self) -> None:
        n = len(self._modifications)
        self._modified_label.setText(f"Zmieniono: {n}" if n else "")

    def _on_save_changes(self) -> None:
        if not self._modifications:
            QMessageBox.information(self, "Global Search", "Brak zmian do zapisania.")
            return
        changes = []
        for row_idx, mods in self._modifications.items():
            if row_idx >= len(self._result_rows):
                continue
            label, csv_path_str, orig = self._result_rows[row_idx]
            raw_deck = orig.get("raw_deck", "").strip()
            if not raw_deck:
                continue
            new_deck = mods.get("deck", orig.get("deck", "")).strip()
            new_arch = mods.get("archetype", orig.get("archetype", "")).strip()
            is_challenge = Path(csv_path_str).name.startswith("challenge_")
            changes.append({
                "raw_deck": raw_deck,
                "deck": new_deck,
                "archetype": new_arch,
                "csv_path": csv_path_str,
                "is_challenge": is_challenge,
            })
        if changes:
            self.save_changes.emit(changes)

    def _on_navigate(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if not item:
            return
        path = item.data(Qt.UserRole)
        if path:
            self.navigate_to.emit(str(path))
            self.close()


class StudioWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[GeneratorWorker] = None
        self.deck_thread: Optional[QThread] = None
        self.deck_worker: Optional[DeckListWorker] = None
        self.stats_thread: Optional[QThread] = None
        self.stats_worker: Optional[StatisticsWorker] = None
        self.challenge_stats_thread: Optional[QThread] = None
        self.challenge_stats_worker: Optional[ChallengeStatisticsWorker] = None
        self.rescan_thread: Optional[QThread] = None
        self.rescan_worker: Optional[ChallengeRescanWorker] = None
        self.last_results: List[GenerationRunResult] = []
        self.last_stats_result: Optional[StatisticsRunResult] = None
        self.deck_refresh_pending = False
        self.repo_root = Path(__file__).resolve().parents[1]
        self.config_dir = self.repo_root / "docs"
        self.editor_rows: List[dict[str, str]] = []
        self.current_editor_source: Optional[Path] = None
        self._deck_catalog_cache: List[str] = []
        self.review_queue_dialog: Optional[ReviewQueueDialog] = None
        self.challenge_review_dialog: Optional[ChallengeReviewDialog] = None

        self._api_status_thread: Optional[QThread] = None
        self._api_status_worker: Optional[ApiStatusWorker] = None

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self._build_ui()
        self._restore_state()

        # Refresh API status every 5 minutes; first check after 2s
        self._api_status_timer = QTimer(self)
        self._api_status_timer.timeout.connect(self._refresh_api_status)
        self._api_status_timer.start(5 * 60 * 1000)
        QTimer.singleShot(2000, self._refresh_api_status)

    def _on_anchor_date_changed(self, anchor: "QDate") -> None:
        self.week_end_edit.setDate(anchor)
        self.week_start_edit.setDate(anchor.addDays(-13))
        self.refresh_deck_list()

    def _on_history_points_changed(self, value: int) -> None:
        single = value == 1
        self.anchor_date_edit.setEnabled(single)
        if value > 1:
            self.stats_weeks_back_spin.setValue(value)
        if single:
            self.generate_button.setText("Generate Snapshot")
        else:
            self.generate_button.setText(f"Build History ({value} weeks)")

    def _build_generator_args(self) -> Namespace:
        args = parse_args([])
        args.format_name = self.format_combo.currentText()
        selected_deck = self.my_deck_combo.currentData(Qt.UserRole)
        args.my_deck = str(selected_deck or self.my_deck_combo.currentText() or "Domain Zoo").strip()
        history_points = self.history_points_spin.value()
        args.history_points = history_points
        if history_points > 1:
            args.week_start = None
            args.week_end = None
        else:
            args.week_start = self.week_start_edit.date().toString("yyyy-MM-dd")
            args.week_end = self.week_end_edit.date().toString("yyyy-MM-dd")
        args.my_window_days = self.my_window_spin.value()
        args.my_fallback_window_days = self.my_fallback_spin.value()
        args.rogue_threshold = self.rogue_spin.value()
        args.metagame_limit = self.metagame_limit_spin.value()
        args.matchup_limit = self.matchup_limit_spin.value()
        outputs_dir = self.repo_root / "outputs"
        args.rules_file = str(self.config_dir / "archetype_rules.csv")
        args.aliases_file = str(self.config_dir / "deck_aliases.csv")
        args.user_mapping_file = str(self.config_dir / "user_deck_mapping.csv")
        args.challenge_mapping_file = str(self.config_dir / "challenge_deck_mapping.csv")
        args.outputs_base = str(outputs_dir)
        args.output_profile = "analysis"
        args.include_challenge_decklist = self.include_challenge_check.isChecked()
        args.challenge_history_events = self.challenge_history_events_spin.value()
        args.skip_challenge_stats = True  # challenge stats are generated from Statistics tab
        args.backup_url = self._backup_url()
        return args

    def _backup_url(self) -> str:
        return self.backup_url_edit.text().strip().rstrip("/")

    def _set_backup_feedback(self, text: str, *, error: bool = False) -> None:
        self.backup_feedback_label.setText(text)
        self.backup_feedback_label.setStyleSheet(
            "color: #9f3f2d;" if error else "color: #4f6b3c;"
        )

    def _commit_backup_url(self) -> None:
        normalized = self._backup_url()
        if self.backup_url_edit.text() != normalized:
            self.backup_url_edit.setText(normalized)
        self.settings.setValue("backup_url", normalized)
        if normalized:
            self._set_backup_feedback("Backup URL saved.")
        else:
            self._set_backup_feedback("Backup URL cleared.")
        self._refresh_api_status()

    def _autotest_backup_url(self) -> None:
        """Silent background health check on startup – no popup dialogs."""
        backup_url = self._backup_url()
        if not backup_url:
            return
        for url in _candidate_backup_health_urls(backup_url):
            try:
                with urlopen(url, timeout=5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                status = str(payload.get("status") or "ok")
                self._set_backup_feedback(f"Backup OK: {status}")
                self._refresh_api_status()
                return
            except Exception:
                pass
        self._set_backup_feedback("Backup URL saved but server unreachable.", error=True)

    def _test_backup_url(self) -> None:
        backup_url = self._backup_url()
        if not backup_url:
            self._set_backup_feedback("Enter Backup URL first.", error=True)
            QMessageBox.warning(self, APP_NAME, "Enter Backup URL first.")
            return

        last_error = "No response from backup server."
        for url in _candidate_backup_health_urls(backup_url):
            try:
                with urlopen(url, timeout=5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                status = str(payload.get("status") or "ok")
                uptime = payload.get("api_uptime_pct")
                last_fetch = payload.get("last_fetch")
                details: List[str] = [f"Health URL: {url}", f"Status: {status}"]
                if uptime is not None:
                    details.append(f"Original API uptime: {uptime:.1f}%")
                if last_fetch:
                    details.append(f"Last backup: {last_fetch}")
                self._set_backup_feedback(f"Backup test OK: {status}")
                self._refresh_api_status()
                QMessageBox.information(self, APP_NAME, "\n".join(details))
                return
            except Exception as err:
                last_error = f"{url}: {err}"

        self._set_backup_feedback("Backup test failed.", error=True)
        QMessageBox.warning(self, APP_NAME, f"Backup test failed.\n\n{last_error}")

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(4)

        title = QLabel("Metagame Input Studio")
        title_font = QFont("Georgia", 18)
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel(
            "Desktop runner for weekly metagame snapshots, matchup enrichment, and editable deck normalization."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("subtitle")

        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)

        self.api_status_label = QLabel("API status: checking...")
        self.api_status_label.setObjectName("muted")
        hero_layout.addWidget(self.api_status_label)

        backup_row = QWidget()
        backup_row_layout = QHBoxLayout(backup_row)
        backup_row_layout.setContentsMargins(0, 8, 0, 0)
        backup_row_layout.setSpacing(8)

        backup_label = QLabel("Backup URL")
        backup_label.setMinimumWidth(84)

        self.backup_url_edit = QLineEdit()
        self.backup_url_edit.setPlaceholderText("https://twoj-backup.example.com")
        self.backup_url_edit.editingFinished.connect(self._commit_backup_url)

        self.save_backup_url_button = QPushButton("Save Backup URL")
        self.save_backup_url_button.clicked.connect(self._commit_backup_url)

        self.test_backup_url_button = QPushButton("Test Backup")
        self.test_backup_url_button.clicked.connect(self._test_backup_url)

        self.backup_feedback_label = QLabel("Backup URL not configured.")
        self.backup_feedback_label.setObjectName("muted")

        backup_row_layout.addWidget(backup_label)
        backup_row_layout.addWidget(self.backup_url_edit, 1)
        backup_row_layout.addWidget(self.save_backup_url_button)
        backup_row_layout.addWidget(self.test_backup_url_button)
        hero_layout.addWidget(backup_row)
        hero_layout.addWidget(self.backup_feedback_label)

        root.addWidget(hero)

        tabs = QTabWidget()
        tabs.addTab(self._build_generator_tab(), "Generator")
        tabs.addTab(self._build_editor_tab(), "Editor")
        tabs.addTab(self._build_statistics_tab(), "Statistics")
        root.addWidget(tabs, 1)

        self.setCentralWidget(central)
        self._apply_styles()

    def _build_generator_tab(self) -> QWidget:
        page = QWidget()
        content = QGridLayout(page)
        content.setContentsMargins(0, 0, 0, 0)
        content.setHorizontalSpacing(14)
        content.setVerticalSpacing(14)

        controls_box = QGroupBox("Run Setup")
        controls_box.setObjectName("panel")
        controls_layout = QVBoxLayout(controls_box)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(14)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS)
        self.format_combo.currentTextChanged.connect(self.refresh_deck_list)

        self.my_deck_combo = QComboBox()
        self.my_deck_combo.setMinimumWidth(260)
        self.my_deck_combo.setEnabled(False)
        self.my_deck_combo.currentIndexChanged.connect(self._update_deck_mode_status)

        self.refresh_decks_button = QPushButton("Refresh Decks")
        self.refresh_decks_button.clicked.connect(self.refresh_deck_list)

        deck_row = QWidget()
        deck_row_layout = QHBoxLayout(deck_row)
        deck_row_layout.setContentsMargins(0, 0, 0, 0)
        deck_row_layout.setSpacing(8)
        deck_row_layout.addWidget(self.my_deck_combo, 1)
        deck_row_layout.addWidget(self.refresh_decks_button)

        self.deck_mode_label = QLabel("Deck mode: loading API list...")
        self.deck_mode_label.setObjectName("muted")
        self.deck_mode_label.setWordWrap(True)

        self.week_start_edit = QDateEdit()
        self.week_start_edit.setCalendarPopup(True)
        self.week_start_edit.setDisplayFormat("yyyy-MM-dd")

        self.week_end_edit = QDateEdit()
        self.week_end_edit.setCalendarPopup(True)
        self.week_end_edit.setDisplayFormat("yyyy-MM-dd")

        self.anchor_date_edit = QDateEdit()
        self.anchor_date_edit.setCalendarPopup(True)
        self.anchor_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.anchor_date_edit.setToolTip(
            "Anchor date (Sunday) — Week Start is auto-set to 13 days before.\n"
            "Ignored when History Points > 1 (dates computed automatically)."
        )
        self.anchor_date_edit.dateChanged.connect(self._on_anchor_date_changed)

        self.my_window_spin = QSpinBox()
        self.my_window_spin.setRange(1, 365)

        self.my_fallback_spin = QSpinBox()
        self.my_fallback_spin.setRange(1, 730)

        self.rogue_spin = QDoubleSpinBox()
        self.rogue_spin.setRange(0.0, 100.0)
        self.rogue_spin.setDecimals(2)
        self.rogue_spin.setSingleStep(0.1)
        self.rogue_spin.setSuffix(" %")

        self.metagame_limit_spin = QSpinBox()
        self.metagame_limit_spin.setRange(1, 1000)
        self.metagame_limit_spin.valueChanged.connect(self.refresh_deck_list)

        self.matchup_limit_spin = QSpinBox()
        self.matchup_limit_spin.setRange(1, 2000)

        self.history_points_spin = QSpinBox()
        self.history_points_spin.setRange(1, 52)
        self.history_points_spin.setValue(1)
        self.history_points_spin.setToolTip(
            "1 = single snapshot (use date fields below)\n"
            ">1 = build N weekly windows back from latest Sunday (date fields ignored)"
        )
        self.history_points_spin.valueChanged.connect(self._on_history_points_changed)

        self.include_challenge_check = QCheckBox("Include Challenge data (C32 + C64)")
        self.include_challenge_check.setChecked(True)

        self.challenge_history_events_spin = QSpinBox()
        self.challenge_history_events_spin.setRange(2, 52)
        self.challenge_history_events_spin.setValue(8)
        self.challenge_history_events_spin.setToolTip(
            "Controls Challenge trend/stat smoothing window (number of recent events).\n"
            "It does not limit fetching for the selected Week Start/Week End window."
        )

        # Dodaj przycisk resetowania domyślnych wartości
        self.reset_defaults_button = QPushButton("Przywróć domyślne")
        self.reset_defaults_button.setToolTip("Przywróć domyślne wartości wszystkich pól")
        self.reset_defaults_button.clicked.connect(self._reset_to_defaults)

        # Dodaj przycisk do osobnego wiersza
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        reset_row.addWidget(self.reset_defaults_button)

        form.addRow("Format", self.format_combo)
        form.addRow("My Deck", deck_row)
        form.addRow("Deck Mode", self.deck_mode_label)
        form.addRow("History Points", self.history_points_spin)
        form.addRow("Anchor Date", self.anchor_date_edit)
        form.addRow("My WR Window", self.my_window_spin)
        form.addRow("Fallback Window", self.my_fallback_spin)

        form.addRow("Rogue Threshold", self.rogue_spin)
        form.addRow("Metagame Limit", self.metagame_limit_spin)
        form.addRow("Matchup Limit", self.matchup_limit_spin)
        form.addRow("Challenge Window", self.include_challenge_check)
        form.addRow("Challenge Stats Events", self.challenge_history_events_spin)
        form.addRow("", reset_row)
        controls_layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.generate_button = QPushButton("Generate Snapshot")
        self.generate_button.clicked.connect(self.start_generation)
        self.generate_button.setObjectName("primaryButton")

        self.open_output_button = QPushButton("Open Output Folder")
        self.open_output_button.clicked.connect(self.open_last_output_dir)
        self.open_output_button.setEnabled(False)

        self.open_main_file_button = QPushButton("Open Main XLSX")
        self.open_main_file_button.clicked.connect(self.open_main_output)
        self.open_main_file_button.setEnabled(False)

        button_row.addWidget(self.generate_button)
        button_row.addWidget(self.open_output_button)
        button_row.addWidget(self.open_main_file_button)
        controls_layout.addLayout(button_row)

        self.auto_stats_after_generate_check = QCheckBox("Auto-run Statistics after generation")
        controls_layout.addWidget(self.auto_stats_after_generate_check)

        hint = QLabel(
            "Deck list is loaded from API for the chosen format and date range. Selecting 'My Deck' forces 50% matchup values. Backup URL is configured in the top header."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        controls_layout.addWidget(hint)

        content.addWidget(controls_box, 0, 0, 2, 1)

        summary_box = QGroupBox("Run Summary")
        summary_box.setObjectName("panel")
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(16, 16, 16, 16)
        summary_layout.setSpacing(10)

        self.summary_label = QLabel("No run yet.")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.files_list = QListWidget()
        self.files_list.itemDoubleClicked.connect(self.open_result_item)

        summary_layout.addWidget(self.summary_label)
        summary_layout.addWidget(self.files_list, 1)

        content.addWidget(summary_box, 0, 1)

        log_box = QGroupBox("Execution Log")
        log_box.setObjectName("panel")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.setSpacing(10)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFontFamily("Consolas")
        self.log_output.setPlaceholderText("Generator logs will appear here.")

        log_layout.addWidget(self.log_output)
        content.addWidget(log_box, 1, 1)

        content.setColumnStretch(0, 3)
        content.setColumnStretch(1, 4)
        content.setRowStretch(1, 1)

        return page

    def _reset_to_defaults(self):
        """Ustawia wszystkie pola generatora na wartości domyślne."""
        try:
            from metagame_input_generator import parse_args
        except ModuleNotFoundError:
            from .metagame_input_generator import parse_args
        defaults = parse_args([])
        # Anchor date: suggest based on latest history, fallback = last Sunday
        anchor_date = self._suggest_anchor_date()
        self.format_combo.setCurrentIndex(self.format_combo.findText(defaults.format_name) if self.format_combo.findText(defaults.format_name) >= 0 else 0)
        self.anchor_date_edit.setDate(anchor_date)
        self.my_window_spin.setValue(getattr(defaults, "my_window_days", 90))
        self.my_fallback_spin.setValue(getattr(defaults, "my_fallback_window_days", 180))
        self.rogue_spin.setValue(getattr(defaults, "rogue_threshold", 0.5))
        self.metagame_limit_spin.setValue(getattr(defaults, "metagame_limit", 64))
        self.matchup_limit_spin.setValue(getattr(defaults, "matchup_limit", 300))
        self.history_points_spin.setValue(getattr(defaults, "history_points", 1))
        self.include_challenge_check.setChecked(bool(getattr(defaults, "include_challenge_decklist", True)))
        self.challenge_history_events_spin.setValue(int(getattr(defaults, "challenge_history_events", 8)))
        # My Deck domyślny
        idx = self.my_deck_combo.findText(getattr(defaults, "my_deck", "Domain Zoo"))
        if idx >= 0:
            self.my_deck_combo.setCurrentIndex(idx)
        else:
            self.my_deck_combo.setCurrentIndex(0)
        # Odśwież listę decków i inne zależne rzeczy
        self.refresh_deck_list()

    def _build_editor_tab(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(14)

        left_box = QGroupBox("Result Editor")
        left_box.setObjectName("panel")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(10)

        source_row = QHBoxLayout()
        source_label = QLabel("Source:")
        source_label.setFixedWidth(54)

        self.editor_source_combo = QComboBox()
        self.editor_source_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.editor_source_combo.setToolTip("Select which generated CSV source to edit (Meta or Challenge)")
        self.editor_source_combo.currentIndexChanged.connect(self._on_editor_source_combo_changed)

        self.load_latest_button = QPushButton("Refresh List")
        self.load_latest_button.clicked.connect(self._refresh_editor_source_combo)

        source_row.addWidget(source_label)
        source_row.addWidget(self.editor_source_combo, 1)
        source_row.addWidget(self.load_latest_button)
        left_layout.addLayout(source_row)

        self.editor_source_label = QLabel("Source: not loaded")
        self.editor_source_label.setWordWrap(True)
        self.editor_source_label.setObjectName("muted")
        left_layout.addWidget(self.editor_source_label)

        # Search / filter bar
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        search_lbl = QLabel("Szukaj:")
        search_lbl.setFixedWidth(52)
        self.editor_search_input = QLineEdit()
        self.editor_search_input.setPlaceholderText("Filtruj po nazwie decku lub archetypie…")
        self.editor_search_input.textChanged.connect(self._filter_editor_table)
        search_clear_btn = QPushButton("✕")
        search_clear_btn.setFixedWidth(30)
        search_clear_btn.setToolTip("Wyczyść filtr")
        search_clear_btn.clicked.connect(self.editor_search_input.clear)
        self.editor_match_label = QLabel("")
        self.editor_match_label.setObjectName("muted")
        self.editor_match_label.setFixedWidth(80)
        search_row.addWidget(search_lbl)
        search_row.addWidget(self.editor_search_input, 1)
        search_row.addWidget(search_clear_btn)
        search_row.addWidget(self.editor_match_label)
        global_search_btn = QPushButton("We wszystkich")
        global_search_btn.setToolTip("Przeszukaj wszystkie pliki historii naraz (otwiera osobne okno)")
        global_search_btn.clicked.connect(self._open_global_search)
        search_row.addWidget(global_search_btn)
        left_layout.addLayout(search_row)

        self.editor_table = QTableWidget(0, len(RESULT_TABLE_HEADERS))
        self.editor_table.setHorizontalHeaderLabels(RESULT_TABLE_HEADERS)
        self.editor_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.editor_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.editor_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.editor_table.verticalHeader().setVisible(False)
        self.editor_table.verticalHeader().setDefaultSectionSize(36)
        header = self.editor_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.editor_table.itemSelectionChanged.connect(self._handle_editor_row_selected)
        left_layout.addWidget(self.editor_table, 1)

        right_box = QGroupBox("Mapping Editor")
        right_box.setObjectName("panel")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        edit_form = QFormLayout()
        edit_form.setHorizontalSpacing(12)
        edit_form.setVerticalSpacing(10)

        self.raw_name_value = QLabel("-")
        self.raw_name_value.setWordWrap(True)
        self.raw_name_value.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.editor_canonical_input = QLineEdit()
        self.editor_archetype_combo = QComboBox()
        self.editor_archetype_combo.setEditable(True)
        self.editor_archetype_combo.setInsertPolicy(QComboBox.NoInsert)
        self.editor_archetype_combo.currentTextChanged.connect(self._on_editor_archetype_text_changed)

        custom_row = QWidget()
        custom_layout = QHBoxLayout(custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(8)
        self.custom_archetype_input = QLineEdit()
        self.custom_archetype_input.setPlaceholderText("Add custom archetype")
        self.add_archetype_button = QPushButton("Add Archetype")
        self.add_archetype_button.clicked.connect(self._add_custom_archetype)
        self.delete_archetype_button = QPushButton("Delete Archetype")
        self.delete_archetype_button.clicked.connect(self._delete_selected_archetype)
        custom_layout.addWidget(self.custom_archetype_input, 1)
        custom_layout.addWidget(self.add_archetype_button)
        custom_layout.addWidget(self.delete_archetype_button)

        bulk_apply_row = QHBoxLayout()
        bulk_apply_row.setSpacing(6)
        self.bulk_apply_button = QPushButton("Przypisz do wszystkich widocznych")
        self.bulk_apply_button.setToolTip(
            "Zastosuj wybrany Archetype do wszystkich wierszy pasujących do aktualnego filtru wyszukiwania.\n"
            "Następnie użyj 'Save All' aby zapisać do plików config."
        )
        self.bulk_apply_button.clicked.connect(self._apply_archetype_to_visible)
        self.bulk_apply_and_save_button = QPushButton("Przypisz i zapisz")
        self.bulk_apply_and_save_button.setToolTip(
            "Zastosuj wybrany Archetype do wszystkich widocznych wierszy i natychmiast zapisz do config."
        )
        self.bulk_apply_and_save_button.clicked.connect(self._apply_archetype_to_visible_and_save)
        bulk_apply_row.addWidget(self.bulk_apply_button)
        bulk_apply_row.addWidget(self.bulk_apply_and_save_button)

        edit_form.addRow("Raw Name", self.raw_name_value)
        edit_form.addRow("Canonical Deck", self.editor_canonical_input)
        edit_form.addRow("Archetype", self.editor_archetype_combo)
        edit_form.addRow("Bulk Assign", bulk_apply_row)
        edit_form.addRow("New Archetype", custom_row)
        right_layout.addLayout(edit_form)

        self.editor_status_label = QLabel("Select a row from the table to edit mapping and archetype.")
        self.editor_status_label.setWordWrap(True)
        self.editor_status_label.setObjectName("muted")
        right_layout.addWidget(self.editor_status_label)

        # Row 1: saving and deck management
        button_row1 = QHBoxLayout()
        button_row1.setSpacing(8)

        self.save_mapping_button = QPushButton("Save Selected")
        self.save_mapping_button.setObjectName("primaryButton")
        self.save_mapping_button.setToolTip("Save the selected row's mapping to config files (user_deck_mapping, deck_aliases, archetype_rules).")
        self.save_mapping_button.clicked.connect(self._save_editor_changes)

        self.save_all_mappings_button = QPushButton("Save All")
        self.save_all_mappings_button.setToolTip("Save all editor rows to config files.")
        self.save_all_mappings_button.clicked.connect(self._save_all_editor_changes)

        self.add_mapping_button = QPushButton("Add Mapping")
        self.add_mapping_button.setToolTip("Manually add a new deck → canonical name + archetype mapping to config files.")
        self.add_mapping_button.clicked.connect(self._add_deck_mapping)

        self.remove_mapping_button = QPushButton("Remove Mapping")
        self.remove_mapping_button.setToolTip("Remove the selected row's mapping from config files (user_deck_mapping, deck_aliases, archetype_rules).")
        self.remove_mapping_button.clicked.connect(self._remove_deck_mapping)

        button_row1.addWidget(self.save_mapping_button)
        button_row1.addWidget(self.save_all_mappings_button)
        button_row1.addWidget(self.add_mapping_button)
        button_row1.addWidget(self.remove_mapping_button)

        # Row 2: review, regenerate, utilities
        button_row2 = QHBoxLayout()
        button_row2.setSpacing(8)

        self.review_queue_button = QPushButton("Review Queue")
        self.review_queue_button.setToolTip("Open the Review Queue — shows rows with Unknown archetypes, short names, or first-seen decks.")
        self.review_queue_button.clicked.connect(self._open_review_queue)

        self.challenge_rescan_button = QPushButton("Challenge Review Queue")
        self.challenge_rescan_button.setToolTip(
            "Scans challenge_history_<format>.csv and premier_history_<format>.csv for ANY "
            "NEEDS_MANUAL_REVIEW row (from any run or migration, not just the last one) and "
            "reclassifies against the full history as reference."
        )
        self.challenge_rescan_button.clicked.connect(self._start_challenge_rescan)

        self.review_all_runs_check = QCheckBox("All runs")
        self.review_all_runs_check.setToolTip("If enabled, Review Queue scans all run directories of the same source type.")

        self.reload_archetypes_button = QPushButton("Reload Archetypes")
        self.reload_archetypes_button.clicked.connect(self._refresh_archetype_combo)

        self.regenerate_grouped_button = QPushButton("Re-Generate (re-fetch API)")
        self.regenerate_grouped_button.setToolTip(
            "Runs a full re-generation for the current date range:\n"
            "• Re-fetches metagame data from the Videre API\n"
            "• Re-applies all saved mapping rules\n"
            "• Regenerates all output files\n\n"
            "Save your mappings first (Save Selected / Save All), then use this\n"
            "to see the effect in the output files."
        )
        self.regenerate_grouped_button.clicked.connect(self._regenerate_from_editor)

        self.open_configs_button = QPushButton("Open Config Folder")
        self.open_configs_button.clicked.connect(lambda: self._open_path(self.config_dir))

        button_row2.addWidget(self.review_queue_button)
        button_row2.addWidget(self.challenge_rescan_button)
        button_row2.addWidget(self.review_all_runs_check)
        button_row2.addWidget(self.reload_archetypes_button)
        button_row2.addWidget(self.regenerate_grouped_button)
        button_row2.addWidget(self.open_configs_button)

        right_layout.addLayout(button_row1)
        right_layout.addLayout(button_row2)
        right_layout.addStretch(1)

        layout.addWidget(left_box, 0, 0)
        layout.addWidget(right_box, 0, 1)
        layout.setColumnStretch(0, 5)
        layout.setColumnStretch(1, 3)
        return page

    def _build_statistics_tab(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(14)

        controls_box = QGroupBox("Analysis Setup")
        controls_box.setObjectName("panel")
        controls_layout = QVBoxLayout(controls_box)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(12)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        input_row = QWidget()
        input_row_layout = QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)
        input_row_layout.setSpacing(8)
        self.stats_input_edit = QLineEdit()
        self.stats_input_edit.setPlaceholderText("Path to grouped XLSX")
        self.stats_pick_input_button = QPushButton("Browse")
        self.stats_pick_input_button.clicked.connect(self._pick_statistics_input)
        self.stats_use_latest_button = QPushButton("Use Latest Grouped")
        self.stats_use_latest_button.clicked.connect(self._set_statistics_input_from_latest)
        input_row_layout.addWidget(self.stats_input_edit, 1)
        input_row_layout.addWidget(self.stats_pick_input_button)
        input_row_layout.addWidget(self.stats_use_latest_button)

        history_row = QWidget()
        history_row_layout = QHBoxLayout(history_row)
        history_row_layout.setContentsMargins(0, 0, 0, 0)
        history_row_layout.setSpacing(8)
        self.stats_history_edit = QLineEdit()
        self.stats_history_edit.setPlaceholderText("Path to persistent history CSV")
        self.stats_pick_history_button = QPushButton("History File")
        self.stats_pick_history_button.clicked.connect(self._pick_statistics_history)
        history_row_layout.addWidget(self.stats_history_edit, 1)
        history_row_layout.addWidget(self.stats_pick_history_button)

        self.stats_total_players_spin = QSpinBox()
        self.stats_total_players_spin.setRange(10, 100000)

        self.stats_rounds_spin = QSpinBox()
        self.stats_rounds_spin.setRange(1, 64)

        self.stats_min_encounter_spin = QDoubleSpinBox()
        self.stats_min_encounter_spin.setRange(0.0, 100.0)
        self.stats_min_encounter_spin.setDecimals(2)
        self.stats_min_encounter_spin.setSingleStep(0.5)
        self.stats_min_encounter_spin.setSuffix(" %")

        self.stats_player_deck_edit = QLineEdit()

        self.stats_player_wr_spin = QDoubleSpinBox()
        self.stats_player_wr_spin.setRange(0.0, 100.0)
        self.stats_player_wr_spin.setDecimals(2)
        self.stats_player_wr_spin.setSingleStep(0.5)
        self.stats_player_wr_spin.setSuffix(" %")

        self.stats_weeks_back_spin = QSpinBox()
        self.stats_weeks_back_spin.setRange(2, 52)

        self.stats_output_profile_combo = QComboBox()
        self.stats_output_profile_combo.addItems(STATS_OUTPUT_PROFILES)

        self.stats_palette_combo = QComboBox()
        self.stats_palette_combo.addItems(STATS_PALETTE_OPTIONS)
        self.stats_palette_combo.currentTextChanged.connect(self._on_stats_palette_changed)

        self.stats_deck_colors_text = QTextEdit()
        self.stats_deck_colors_text.setPlaceholderText(
            "Deck-specific colors (one per line):\n"
            "Prowess=#ff4d6d\n"
            "Boros Energy=#f77f00"
        )
        self.stats_deck_colors_text.setMinimumHeight(110)
        self.stats_deck_colors_text.textChanged.connect(self._update_deck_color_preview)
        self.stats_deck_colors_text.setVisible(False)
        deck_colors_widget = QWidget()
        deck_colors_layout = QVBoxLayout(deck_colors_widget)
        deck_colors_layout.setContentsMargins(0, 0, 0, 0)
        deck_colors_layout.setSpacing(8)
        deck_colors_layout.addWidget(self.stats_deck_colors_text)
        deck_color_picker_row = QHBoxLayout()
        deck_color_picker_row.setSpacing(8)
        self.stats_deck_color_name_edit = QLineEdit()
        self.stats_deck_color_name_edit.setPlaceholderText("Deck name for picker")
        self.stats_pick_deck_color_button = QPushButton("Pick Deck Color")
        self.stats_pick_deck_color_button.clicked.connect(self._pick_deck_color_rule)
        deck_color_picker_row.addWidget(self.stats_deck_color_name_edit, 1)
        deck_color_picker_row.addWidget(self.stats_pick_deck_color_button)
        deck_colors_layout.addLayout(deck_color_picker_row)
        self.stats_deck_preview_label = QLabel()
        self.stats_deck_preview_label.setWordWrap(True)
        self.stats_deck_preview_label.setObjectName("muted")
        deck_colors_layout.addWidget(self.stats_deck_preview_label)
        deck_colors_widget.setVisible(False)

        self.stats_legend_colors_text = QTextEdit()
        self.stats_legend_colors_text.setPlaceholderText(
            "Legend colors (one per line):\n"
            "Underplayed Winner=#2a9d8f\n"
            "Popular Trap=#e76f51\n"
            "Rising Deck=#43aa8b\n"
            "Falling Deck=#f94144"
        )
        self.stats_legend_colors_text.setMinimumHeight(110)
        self.stats_legend_colors_text.textChanged.connect(self._update_legend_color_preview)
        self.stats_legend_colors_text.setVisible(False)
        legend_colors_widget = QWidget()
        legend_colors_layout = QVBoxLayout(legend_colors_widget)
        legend_colors_layout.setContentsMargins(0, 0, 0, 0)
        legend_colors_layout.setSpacing(8)
        legend_colors_layout.addWidget(self.stats_legend_colors_text)
        legend_picker_row = QHBoxLayout()
        legend_picker_row.setSpacing(8)
        self.stats_legend_key_combo = QComboBox()
        self.stats_legend_key_combo.addItems(STATS_LEGEND_KEYS)
        self.stats_pick_legend_color_button = QPushButton("Pick Legend Color")
        self.stats_pick_legend_color_button.clicked.connect(self._pick_legend_color_rule)
        self.stats_custom_legend_button = QPushButton("Custom Label")
        self.stats_custom_legend_button.clicked.connect(self._pick_custom_legend_color_rule)
        legend_picker_row.addWidget(self.stats_legend_key_combo, 1)
        legend_picker_row.addWidget(self.stats_pick_legend_color_button)
        legend_picker_row.addWidget(self.stats_custom_legend_button)
        legend_colors_layout.addLayout(legend_picker_row)
        self.stats_legend_preview_label = QLabel()
        self.stats_legend_preview_label.setWordWrap(True)
        self.stats_legend_preview_label.setObjectName("muted")
        legend_colors_layout.addWidget(self.stats_legend_preview_label)
        legend_colors_widget.setVisible(False)

        self.stats_color_tabs = QTabWidget()

        deck_tab = QWidget()
        deck_tab_layout = QVBoxLayout(deck_tab)
        deck_tab_layout.setContentsMargins(0, 0, 0, 0)
        deck_tab_layout.setSpacing(8)
        self.stats_deck_color_table = QTableWidget(0, 2)
        self.stats_deck_color_table.setHorizontalHeaderLabels(["Deck/Archetype", "Color"])
        self.stats_deck_color_table.verticalHeader().setVisible(False)
        self.stats_deck_color_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.stats_deck_color_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.stats_deck_color_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stats_deck_color_table.cellDoubleClicked.connect(self._on_deck_color_table_double_clicked)

        deck_btn_row = QHBoxLayout()
        self.stats_load_color_targets_button = QPushButton("Load Decks from Input")
        self.stats_load_color_targets_button.clicked.connect(self._load_deck_color_targets_from_input)
        self.stats_add_deck_color_row_button = QPushButton("Add Row")
        self.stats_add_deck_color_row_button.clicked.connect(self._add_deck_color_row)
        self.stats_remove_deck_color_row_button = QPushButton("Remove Row")
        self.stats_remove_deck_color_row_button.clicked.connect(self._remove_deck_color_row)
        deck_btn_row.addWidget(self.stats_load_color_targets_button)
        deck_btn_row.addWidget(self.stats_add_deck_color_row_button)
        deck_btn_row.addWidget(self.stats_remove_deck_color_row_button)
        deck_btn_row.addStretch(1)

        deck_hint = QLabel("Double-click color cell to choose color. This list supports both decks and archetypes.")
        deck_hint.setObjectName("muted")
        deck_hint.setWordWrap(True)

        deck_tab_layout.addWidget(self.stats_deck_color_table, 1)
        deck_tab_layout.addLayout(deck_btn_row)
        deck_tab_layout.addWidget(deck_hint)

        legend_tab = QWidget()
        legend_tab_layout = QVBoxLayout(legend_tab)
        legend_tab_layout.setContentsMargins(0, 0, 0, 0)
        legend_tab_layout.setSpacing(8)
        self.stats_legend_color_table = QTableWidget(0, 2)
        self.stats_legend_color_table.setHorizontalHeaderLabels(["Legend Item", "Color"])
        self.stats_legend_color_table.verticalHeader().setVisible(False)
        self.stats_legend_color_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.stats_legend_color_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.stats_legend_color_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stats_legend_color_table.cellDoubleClicked.connect(self._on_legend_color_table_double_clicked)

        legend_btn_row = QHBoxLayout()
        self.stats_add_legend_color_row_button = QPushButton("Add Legend Row")
        self.stats_add_legend_color_row_button.clicked.connect(self._add_legend_color_row)
        self.stats_remove_legend_color_row_button = QPushButton("Remove Row")
        self.stats_remove_legend_color_row_button.clicked.connect(self._remove_legend_color_row)
        legend_btn_row.addWidget(self.stats_add_legend_color_row_button)
        legend_btn_row.addWidget(self.stats_remove_legend_color_row_button)
        legend_btn_row.addStretch(1)

        legend_hint = QLabel("Double-click color cell to choose color. Palette above controls defaults; table sets explicit overrides.")
        legend_hint.setObjectName("muted")
        legend_hint.setWordWrap(True)

        legend_tab_layout.addWidget(self.stats_legend_color_table, 1)
        legend_tab_layout.addLayout(legend_btn_row)
        legend_tab_layout.addWidget(legend_hint)

        self.stats_color_tabs.addTab(deck_tab, "Decks & Archetypes")
        self.stats_color_tabs.addTab(legend_tab, "Legend")

        self.stats_type_combo = QComboBox()
        self.stats_type_combo.addItems(["Liga", "Challenge", "Oba"])
        self.stats_type_combo.setToolTip(
            "Liga — statystyki metagame z XLSX\n"
            "Challenge — statystyki turniejowe z challenge_history_<format>.csv\n"
            "Oba — najpierw Liga, potem Challenge"
        )
        self.stats_type_combo.currentTextChanged.connect(self._on_stats_type_changed)

        form.addRow("Tryb statystyk", self.stats_type_combo)
        form.addRow("Input Grouped XLSX", input_row)
        form.addRow("History CSV", history_row)
        form.addRow("Players (N)", self.stats_total_players_spin)
        form.addRow("Rounds", self.stats_rounds_spin)
        form.addRow("Min Encounter", self.stats_min_encounter_spin)
        form.addRow("Deck Label", self.stats_player_deck_edit)
        form.addRow("Overall Winrate", self.stats_player_wr_spin)
        form.addRow("Trend Weeks", self.stats_weeks_back_spin)
        form.addRow("Output Profile", self.stats_output_profile_combo)
        form.addRow("Palette", self.stats_palette_combo)
        controls_layout.addLayout(form)

        # Keep hidden text-based config editors in the widget tree for persistence/compatibility.
        controls_layout.addWidget(deck_colors_widget)
        controls_layout.addWidget(legend_colors_widget)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.stats_run_button = QPushButton("Run Statistics")
        self.stats_run_button.setObjectName("primaryButton")
        self.stats_run_button.clicked.connect(self.start_statistics)
        self.stats_open_folder_button = QPushButton("Open Statistics Folder")
        self.stats_open_folder_button.clicked.connect(self.open_statistics_output_dir)
        self.stats_open_folder_button.setEnabled(False)
        self.stats_rebuild_history_button = QPushButton("Rebuild History")
        self.stats_rebuild_history_button.setToolTip(
            "Scan all run directories under outputs/YYYY/MM/ and rebuild metagame_history.csv from scratch."
        )
        self.stats_rebuild_history_button.clicked.connect(self._rebuild_history)
        self.stats_rebuild_challenge_history_button = QPushButton("Rebuild Challenge History")
        self.stats_rebuild_challenge_history_button.setToolTip(
            "Scan all run directories and rebuild challenge_history_<format>.csv from challenge_*_decklist.csv files."
        )
        self.stats_rebuild_challenge_history_button.clicked.connect(self._rebuild_challenge_history)
        button_row.addWidget(self.stats_run_button)
        button_row.addWidget(self.stats_rebuild_history_button)
        button_row.addWidget(self.stats_rebuild_challenge_history_button)
        button_row.addWidget(self.stats_open_folder_button)
        controls_layout.addLayout(button_row)

        self.stats_summary_label = QLabel("No statistics run yet.")
        self.stats_summary_label.setWordWrap(True)
        controls_layout.addWidget(self.stats_summary_label)

        outputs_box = QGroupBox("Statistics Outputs")
        outputs_box.setObjectName("panel")
        outputs_layout = QVBoxLayout(outputs_box)
        outputs_layout.setContentsMargins(10, 10, 10, 10)
        outputs_layout.setSpacing(8)

        self.stats_files_list = QListWidget()
        self.stats_files_list.itemDoubleClicked.connect(self.open_statistics_result_item)

        self.stats_log_output = QTextEdit()
        self.stats_log_output.setReadOnly(True)
        self.stats_log_output.setFontFamily("Consolas")
        self.stats_log_output.setPlaceholderText("Statistics logs will appear here.")

        outputs_layout.addWidget(self.stats_files_list, 1)
        outputs_layout.addWidget(self.stats_log_output, 1)
        controls_layout.addWidget(outputs_box, 1)

        right_box = QGroupBox("Color Studio")
        right_box.setObjectName("panel")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)
        right_layout.addWidget(self.stats_color_tabs, 1)

        layout.addWidget(controls_box, 0, 0)
        layout.addWidget(right_box, 0, 1)
        layout.setColumnStretch(0, 5)
        layout.setColumnStretch(1, 4)
        return page

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f4efe4;
                color: #1f1a17;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QMainWindow {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #efe7d5,
                    stop: 0.55 #f6f1e8,
                    stop: 1 #e2d6be
                );
            }
            QFrame#hero, QGroupBox#panel {
                background: rgba(255, 251, 245, 0.86);
                border: 1px solid #d3c4a6;
                border-radius: 16px;
            }
            QGroupBox {
                font-family: 'Georgia';
                font-size: 14px;
                font-weight: 700;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QLabel#subtitle {
                color: #5d4c3f;
                font-size: 13px;
            }
            QLabel#muted {
                color: #746150;
            }
            QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget {
                background: #fffdf8;
                border: 1px solid #ccb892;
                border-radius: 10px;
                padding: 7px 10px;
            }
            QComboBox {
                padding-right: 28px;
                min-height: 24px;
            }
            QTableWidget QComboBox {
                min-height: 28px;
                padding-top: 2px;
                padding-bottom: 2px;
            }
            QTableWidget {
                background: #fffdf8;
                border: 1px solid #ccb892;
                border-radius: 10px;
                gridline-color: #e6d8bd;
            }
            QHeaderView::section {
                background: #efe0c3;
                color: #1f1a17;
                border: 0;
                border-right: 1px solid #d6c09b;
                padding: 8px;
                font-weight: 700;
            }
            QPushButton {
                background: #ede2cb;
                border: 1px solid #c4ab7f;
                border-radius: 11px;
                padding: 9px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #e6d6b5;
            }
            QPushButton:disabled {
                background: #dfd8cc;
                color: #7d776e;
                border-color: #cbc2b4;
            }
            QPushButton#primaryButton {
                background: #9f3f2d;
                color: #fffaf4;
                border-color: #7d2f21;
            }
            QPushButton#primaryButton:hover {
                background: #b34934;
            }
            QListWidget::item {
                padding: 6px;
                border-radius: 8px;
            }
            QListWidget::item:selected {
                background: #dcc7a2;
                color: #1f1a17;
            }
            """
        )

    # ------------------------------------------------------------------
    # API status helpers
    # ------------------------------------------------------------------
    def _refresh_api_status(self) -> None:
        backup_url = self._backup_url()

        # Avoid launching a second thread while one is running
        try:
            if self._api_status_thread and self._api_status_thread.isRunning():
                return
        except RuntimeError:
            self._api_status_thread = None
            self._api_status_worker = None

        self._api_status_thread = QThread()
        self._api_status_worker = ApiStatusWorker(backup_url)
        self._api_status_worker.moveToThread(self._api_status_thread)
        self._api_status_thread.started.connect(self._api_status_worker.run)
        self._api_status_worker.finished.connect(self._apply_api_status)
        self._api_status_worker.finished.connect(self._api_status_thread.quit)
        self._api_status_worker.finished.connect(self._api_status_worker.deleteLater)
        self._api_status_thread.finished.connect(self._api_status_thread.deleteLater)
        self._api_status_thread.start()

    def _apply_api_status(self, result: dict) -> None:
        parts: list[str] = []

        # Original API
        orig_status = result.get("original_status", "offline")
        orig_icon = {"online": "\u2705", "degraded": "\u26a0\ufe0f", "offline": "\u274c"}[orig_status]
        orig_label = {"online": "Online", "degraded": "Degraded (5xx)", "offline": "Offline"}[orig_status]
        parts.append(f"Original API: {orig_icon} {orig_label}")

        # Original API uptime (tracked by backup bot)
        backup = result.get("backup") or {}
        uptime = backup.get("api_uptime_pct")
        if uptime is not None:
            parts.append(f"Original API Uptime: {uptime:.1f}%")
        else:
            parts.append("Original API Uptime: N/A")

        # Backup server
        backup_url = self._backup_url()
        if not backup_url:
            parts.append("Backup Server: not configured")
        elif backup:
            bk_status = backup.get("status", "?")
            bk_icon = "\u2705" if bk_status == "ok" else "\u274c"
            parts.append(f"Backup Server: {bk_icon} {bk_status}")

            last_ok = backup.get("last_success")
            parts.append(f"Last OK: {last_ok}" if last_ok else "Last OK: never")

            last_fetch = backup.get("last_fetch")
            parts.append(f"Last backup: {last_fetch}" if last_fetch else "Last backup: never")

            failures = backup.get("consecutive_failures")
            if failures is not None and failures > 0:
                parts.append(f"Failures: {failures}")

            cached = backup.get("cached_files")
            if cached is not None:
                parts.append(f"Cached files: {cached}")
        else:
            parts.append("Backup Server: \u274c no response")

        self.api_status_label.setText("  |  ".join(parts))

    # ------------------------------------------------------------------

    def _restore_state(self) -> None:
        # Wymusza domyślne wartości generatora niezależnie od QSettings
        self._reset_to_defaults()
        # Pozostałe pola (statystyki, edytor) mogą korzystać z QSettings jak dotychczas
        defaults = parse_args([])
        default_history = str(self._default_statistics_history_path())
        self.stats_input_edit.setText(str(self.settings.value("stats_input", "") or ""))
        self.stats_history_edit.setText(str(self.settings.value("stats_history", default_history) or default_history))
        self.stats_total_players_spin.setValue(int(self.settings.value("stats_total_players", 1000)))
        self.stats_rounds_spin.setValue(int(self.settings.value("stats_rounds", 5)))
        self.stats_min_encounter_spin.setValue(float(self.settings.value("stats_min_encounter", 5.0)))
        self.stats_player_deck_edit.setText(str(self.settings.value("stats_player_deck", "My Deck") or "My Deck"))
        self.stats_player_wr_spin.setValue(float(self.settings.value("stats_player_wr", 50.0)))
        self.stats_weeks_back_spin.setValue(int(self.settings.value("stats_weeks_back", 4)))
        saved_profile = str(self.settings.value("stats_output_profile", "full") or "full").lower()
        if self._backup_url():
            self._set_backup_feedback("Backup URL loaded, checking…")
            QTimer.singleShot(500, self._autotest_backup_url)
        else:
            self._set_backup_feedback("Backup URL not configured.", error=True)
        saved_palette = str(self.settings.value("stats_palette", "classic") or "classic").lower()
        self.stats_output_profile_combo.setCurrentText(saved_profile if saved_profile in STATS_OUTPUT_PROFILES else "full")
        self.stats_palette_combo.setCurrentText(saved_palette if saved_palette in STATS_PALETTE_OPTIONS else "classic")
        self.stats_deck_colors_text.setPlainText(str(self.settings.value("stats_deck_colors", "") or ""))
        self.stats_legend_colors_text.setPlainText(str(self.settings.value("stats_legend_colors", "") or ""))
        self.include_challenge_check.setChecked(bool(self.settings.value("include_challenge_decklist", True, type=bool)))
        self.challenge_history_events_spin.setValue(int(self.settings.value("challenge_history_events", 8)))
        self.backup_url_edit.setText(str(self.settings.value("backup_url", "") or "").strip().rstrip("/"))
        self._sync_color_tables_from_text()
        self._update_deck_color_preview()
        self._update_legend_color_preview()

        self._refresh_archetype_combo()
        self.refresh_deck_list()
        self.load_latest_output_for_editor()
        if not self.stats_input_edit.text().strip():
            self._set_statistics_input_from_latest()

    def _load_date(self, key: str, fallback: QDate) -> QDate:
        value = self.settings.value(key)
        if not value:
            return fallback
        parsed = QDate.fromString(str(value), "yyyy-MM-dd")
        return parsed if parsed.isValid() else fallback

    def _save_state(self) -> None:
        self._sync_text_from_color_tables()
        self.settings.setValue("format_name", self.format_combo.currentText())
        self.settings.setValue("my_deck", self.my_deck_combo.currentText().strip())
        self.settings.setValue("week_start", self.week_start_edit.date().toString("yyyy-MM-dd"))
        self.settings.setValue("week_end", self.week_end_edit.date().toString("yyyy-MM-dd"))
        self.settings.setValue("my_window_days", self.my_window_spin.value())
        self.settings.setValue("my_fallback_window_days", self.my_fallback_spin.value())
        self.settings.setValue("rogue_threshold", self.rogue_spin.value())
        self.settings.setValue("metagame_limit", self.metagame_limit_spin.value())
        self.settings.setValue("matchup_limit", self.matchup_limit_spin.value())
        self.settings.setValue("include_challenge_decklist", self.include_challenge_check.isChecked())
        self.settings.setValue("challenge_history_events", self.challenge_history_events_spin.value())
        self.settings.setValue("backup_url", self._backup_url())
        self.settings.setValue("auto_stats_after_generate", self.auto_stats_after_generate_check.isChecked())
        self.settings.setValue("stats_input", self.stats_input_edit.text().strip())
        self.settings.setValue("stats_history", self.stats_history_edit.text().strip())
        self.settings.setValue("stats_total_players", self.stats_total_players_spin.value())
        self.settings.setValue("stats_rounds", self.stats_rounds_spin.value())
        self.settings.setValue("stats_min_encounter", self.stats_min_encounter_spin.value())
        self.settings.setValue("stats_player_deck", self.stats_player_deck_edit.text().strip())
        self.settings.setValue("stats_player_wr", self.stats_player_wr_spin.value())
        self.settings.setValue("stats_weeks_back", self.stats_weeks_back_spin.value())
        self.settings.setValue("stats_output_profile", self.stats_output_profile_combo.currentText().strip().lower())
        self.settings.setValue("stats_palette", self.stats_palette_combo.currentText().strip().lower())
        self.settings.setValue("stats_deck_colors", self.stats_deck_colors_text.toPlainText())
        self.settings.setValue("stats_legend_colors", self.stats_legend_colors_text.toPlainText())

    def _default_statistics_history_path(self) -> Path:
        format_name = self.format_combo.currentText().strip() or "Modern"
        slug = "-".join(format_name.lower().split())
        return self.repo_root / "outputs" / f"metagame_history_{slug}.csv"

    def _default_challenge_history_path(self) -> Path:
        format_name = self.format_combo.currentText().strip() or "Modern"
        slug = "-".join(format_name.lower().split())
        return self.repo_root / "outputs" / f"challenge_history_{slug}.csv"

    def _default_premier_history_path(self) -> Path:
        format_name = self.format_combo.currentText().strip() or "Modern"
        slug = "-".join(format_name.lower().split())
        return self.repo_root / "outputs" / f"premier_history_{slug}.csv"

    def _current_stats_run_dir(self) -> Optional[Path]:
        """Weekly run dir that Challenge statistics should be written under.

        Prefers the run dir behind the currently selected Liga input file (keeps
        Liga + Challenge aligned to the same week in 'Oba' mode), else falls back
        to the newest run dir found under outputs/.
        """
        input_text = self.stats_input_edit.text().strip()
        if input_text:
            candidate = Path(input_text)
            if candidate.exists():
                return candidate.parent

        from metagame_input_generator import scan_run_dirs

        runs = scan_run_dirs(self.repo_root / "outputs")
        if not runs:
            return None
        return sorted(runs, key=lambda x: x[0])[-1][2]

    def _challenge_stats_output_dir(self) -> Path:
        run_dir = self._current_stats_run_dir()
        if run_dir is not None:
            return run_dir / "statistics" / "challenges"
        format_name = self.format_combo.currentText().strip() or "Modern"
        slug = "-".join(format_name.lower().split())
        return self.repo_root / "outputs" / "challenge_statistics" / slug

    def _challenge_stats_window(self) -> tuple[Optional[date], Optional[date]]:
        """WeekStart/WeekEnd for the Challenge statistics run -- same single source of truth as
        the output dir (_challenge_stats_output_dir): the week encoded in the currently selected
        run dir's folder name (Wnn_YYYY-MM-DD_to_YYYY-MM-DD), falling back to the app's own
        week_start_edit/week_end_edit pickers if no run dir is resolved.

        Passing (None, None) into run_challenge_statistics() makes it fall back to a "last N*2
        distinct event dates" trim over the *entire* history file, unrelated to any selected week
        -- that mismatch (output path says one week, data was an arbitrary trailing slice) was the
        root cause of Challenge stats over-counting events from an adjacent week. Always resolve
        an explicit window here instead.
        """
        run_dir = self._current_stats_run_dir()
        if run_dir is not None:
            import re as _re

            match = _re.search(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})", run_dir.name)
            if match:
                try:
                    return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
                except ValueError:
                    pass
        if self.week_end_edit.date() < self.week_start_edit.date():
            return None, None
        return (
            date.fromisoformat(self.week_start_edit.date().toString("yyyy-MM-dd")),
            date.fromisoformat(self.week_end_edit.date().toString("yyyy-MM-dd")),
        )

    def _on_stats_type_changed(self, mode: str) -> None:
        is_liga = mode in ("Liga", "Oba")
        self.stats_input_edit.setEnabled(is_liga)
        self.stats_pick_input_button.setEnabled(is_liga)
        self.stats_use_latest_button.setEnabled(is_liga)
        self.stats_history_edit.setEnabled(is_liga)
        self.stats_pick_history_button.setEnabled(is_liga)

    def _find_latest_grouped_input(self) -> Optional[Path]:
        # Always scan disk for the newest run dir — do NOT rely on self.last_results
        # which may point to an older run from the current session.
        from metagame_input_generator import scan_run_dirs

        runs = scan_run_dirs(self.repo_root / "outputs")
        candidates = [run_dir for _ws, _we, run_dir in reversed(runs)]

        # Also include legacy flat dirs
        outputs_base = self.repo_root / "outputs"
        if outputs_base.exists():
            import re as _re
            flat_re = _re.compile(r"^(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})$")
            from datetime import date as _date
            flat_dirs: list[tuple] = []
            for d in outputs_base.iterdir():
                if not d.is_dir():
                    continue
                m = flat_re.match(d.name)
                if not m:
                    continue
                try:
                    we = _date.fromisoformat(m.group(2))
                    flat_dirs.append((we, d))
                except ValueError:
                    continue
            for _we, d in sorted(flat_dirs, reverse=True):
                candidates.append(d)

        for run_dir in candidates:
            grouped = run_dir / "metagame_input_grouped.xlsx"
            if grouped.exists():
                return grouped
            legacy = run_dir / "metagame_input_rogue_grouped.xlsx"
            if legacy.exists():
                return legacy
        return None

    def _set_statistics_input_from_latest(self) -> None:
        latest = self._find_latest_grouped_input()
        if latest is None:
            self.stats_summary_label.setText("No grouped XLSX found in latest output folder.")
            return
        self.stats_input_edit.setText(str(latest))
        if self.last_results:
            self.stats_summary_label.setText(f"Ready for analysis from: {latest.name}")

    def _pick_statistics_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select grouped metagame file",
            str(self.repo_root / "outputs"),
            "Excel files (*.xlsx *.xls);;All files (*.*)",
        )
        if path:
            self.stats_input_edit.setText(path)

    def _pick_statistics_history(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select history CSV",
            str(self._default_statistics_history_path()),
            "CSV files (*.csv);;All files (*.*)",
        )
        if path:
            self.stats_history_edit.setText(path)

    def _statistics_output_dir_for_input(self, input_path: Path) -> Path:
        parent = input_path.parent
        return parent / "statistics" / "metagame"

    def _rebuild_history(self) -> None:
        history_text = self.stats_history_edit.text().strip()
        history_path = Path(history_text) if history_text else self._default_statistics_history_path()
        outputs_base = self.repo_root / "outputs"

        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Rebuild History",
            f"This will delete and rebuild:\n{history_path}\n\n"
            f"Scanning all run dirs in:\n{outputs_base}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.stats_rebuild_history_button.setEnabled(False)
        self.stats_run_button.setEnabled(False)
        self.stats_log_output.clear()
        self.stats_summary_label.setText("Rebuilding history...")

        def _log(msg: str) -> None:
            self.stats_log_output.append(msg)
            QApplication.processEvents()

        try:
            count = rebuild_history_from_dirs(
                outputs_base=outputs_base,
                history_csv=history_path,
                total_players=self.stats_total_players_spin.value(),
                rounds=self.stats_rounds_spin.value(),
                min_encounter_pct=self.stats_min_encounter_spin.value(),
                player_deck_name=self.stats_player_deck_edit.text().strip() or "My Deck",
                player_winrate=self.stats_player_wr_spin.value() / 100.0
                if self.stats_player_wr_spin.value() > 1
                else self.stats_player_wr_spin.value(),
                weeks_back=self.stats_weeks_back_spin.value(),
                output_profile=self.stats_output_profile_combo.currentText(),
                palette_name=self.stats_palette_combo.currentText(),
                deck_colors=self._parse_stats_deck_colors() or None,
                legend_colors=self._parse_stats_legend_colors() or None,
                log=_log,
            )
            self.stats_history_edit.setText(str(history_path))
            self.stats_summary_label.setText(
                f"Rebuild complete. {count} week(s) processed. History: {history_path.name}"
            )
            self.stats_open_folder_button.setEnabled(True)
        except Exception as exc:
            self.stats_summary_label.setText(f"Rebuild failed: {exc}")
            _log(f"[rebuild][fatal] {exc}")
        finally:
            self.stats_rebuild_history_button.setEnabled(True)
            self.stats_run_button.setEnabled(True)

    def _rebuild_challenge_history(self) -> None:
        outputs_base = self.repo_root / "outputs"
        format_name = self.format_combo.currentText().strip() or "Modern"
        format_slug = "-".join(format_name.lower().split())
        history_path = outputs_base / f"challenge_history_{format_slug}.csv"

        reply = QMessageBox.question(
            self,
            "Rebuild Challenge History",
            f"This will rebuild:\n{history_path}\n\n"
            f"Scanning run dirs in:\n{outputs_base}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.stats_rebuild_challenge_history_button.setEnabled(False)
        self.stats_log_output.clear()
        self.stats_summary_label.setText("Rebuilding challenge history...")

        def _log(msg: str) -> None:
            self.stats_log_output.append(msg)
            QApplication.processEvents()

        try:
            event_count = rebuild_challenge_history_from_dirs(
                outputs_base=outputs_base,
                history_csv=history_path,
                format_name=format_name,
                challenge_mapping_csv=self.config_dir / "challenge_deck_mapping.csv",
                aliases_csv=self.config_dir / "deck_aliases.csv",
                rules_csv=self.config_dir / "archetype_rules.csv",
                log=_log,
            )
            self.stats_summary_label.setText(
                f"Challenge rebuild complete. events={event_count} | history={history_path.name}"
            )
            self.stats_open_folder_button.setEnabled(True)
        except Exception as exc:
            self.stats_summary_label.setText(f"Challenge rebuild failed: {exc}")
            _log(f"[challenge-rebuild][fatal] {exc}")
        finally:
            self.stats_rebuild_challenge_history_button.setEnabled(True)

    def _parse_stats_deck_colors(self) -> dict[str, str]:
        out: dict[str, str] = {}
        raw = self.stats_deck_colors_text.toPlainText().strip()
        if not raw:
            return out

        for line in raw.splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if "=" in text:
                name, color = text.split("=", 1)
            elif ":" in text:
                name, color = text.split(":", 1)
            else:
                continue

            deck_name = str(name).strip()
            color_text = str(color).strip()
            if not deck_name or not color_text:
                continue
            if not color_text.startswith("#"):
                color_text = f"#{color_text}"
            if len(color_text) != 7:
                continue
            hex_part = color_text[1:]
            if any(ch not in "0123456789abcdefABCDEF" for ch in hex_part):
                continue
            out[deck_name] = f"#{hex_part.lower()}"
        return out

    def _color_mapping_to_text(self, mapping: dict[str, str]) -> str:
        if not mapping:
            return ""
        lines = [f"{name}={color}" for name, color in sorted(mapping.items(), key=lambda x: x[0].lower())]
        return "\n".join(lines)

    def _default_legend_colors_for_palette(self, palette_name: str) -> dict[str, str]:
        performance_map = {
            "classic": {
                "Underplayed Winner": "#b9f03a",
                "Popular Trap": "#f07431",
                "Neutral": "#000000",
            },
            "warm": {
                "Underplayed Winner": "#f2c14e",
                "Popular Trap": "#e76f51",
                "Neutral": "#3d405b",
            },
            "neon": {
                "Underplayed Winner": "#39ff14",
                "Popular Trap": "#ff5400",
                "Neutral": "#2b2d42",
            },
            "colorblind": {
                "Underplayed Winner": "#0072b2",
                "Popular Trap": "#d55e00",
                "Neutral": "#4d4d4d",
            },
        }
        trend_map = {
            "classic": {"Rising Deck": "#2ecc71", "Falling Deck": "#e74c3c", "Stable": "#95a5a6"},
            "warm": {"Rising Deck": "#e9c46a", "Falling Deck": "#d62828", "Stable": "#6c757d"},
            "neon": {"Rising Deck": "#00f5d4", "Falling Deck": "#ff006e", "Stable": "#8d99ae"},
            "colorblind": {"Rising Deck": "#009e73", "Falling Deck": "#d55e00", "Stable": "#999999"},
        }
        selected = str(palette_name or "classic").strip().lower()
        if selected not in performance_map:
            selected = "classic"

        defaults: dict[str, str] = {}
        defaults.update(performance_map[selected])
        defaults.update(trend_map[selected])
        defaults.update(
            {
                "Very High Prep Priority": "#ff0000",
                "High Prep Priority": "#ffa500",
                "Medium Prep Priority": "#0000ff",
                "Low Prep Priority": "#008000",
                "High My Deck WR": "#2ca25f",
                "Mid My Deck WR": "#f1c40f",
                "Low My Deck WR": "#d62728",
                "Low Record Chance": "#d62728",
                "Mid Record Chance": "#f1c40f",
                "High Record Chance": "#2ca25f",
                "Low Deck WR": "#d62728",
                "Mid Deck WR": "#f1c40f",
                "High Deck WR": "#2ca25f",
                "Trend Box": "#f5deb3",
                "Custom Deck Override": "#7f8c8d",
            }
        )
        return defaults

    def _auto_deck_preview_color(self, name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return "#bdbdbd"
        seed = sum((i + 1) * ord(ch) for i, ch in enumerate(text))
        hue = seed % 360
        color = QColor()
        color.setHsv(hue, 80, 235)
        return str(color.name()).lower()

    def _mapping_from_table(self, table: QTableWidget) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            color_item = table.item(row, 1)
            if name_item is None or color_item is None:
                continue
            name = name_item.text().strip()
            color_text = color_item.text().strip().lower()
            if not name or not color_text.startswith("#") or len(color_text) != 7:
                continue
            if any(ch not in "0123456789abcdef" for ch in color_text[1:]):
                continue
            out[name] = color_text
        return out

    def _set_color_cell(self, table: QTableWidget, row: int, color_hex: str) -> None:
        item = table.item(row, 1)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 1, item)
        color = str(color_hex).strip().lower()
        if not color.startswith("#"):
            color = f"#{color}"
        item.setText(color)
        qcolor = QColor(color)
        if qcolor.isValid():
            item.setBackground(qcolor)
            brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
            item.setForeground(QColor("#111111") if brightness > 150 else QColor("#f5f5f5"))

    def _set_auto_color_cell(self, table: QTableWidget, row: int, preview_hex: str) -> None:
        item = table.item(row, 1)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 1, item)
        item.setText("auto")
        qcolor = QColor(str(preview_hex or "#bdbdbd"))
        if not qcolor.isValid():
            qcolor = QColor("#bdbdbd")
        item.setBackground(qcolor)
        brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
        item.setForeground(QColor("#111111") if brightness > 150 else QColor("#f5f5f5"))

    def _populate_color_table(self, table: QTableWidget, mapping: dict[str, str], fixed_order: Optional[list[str]] = None) -> None:
        table.blockSignals(True)
        table.setRowCount(0)
        names = fixed_order if fixed_order is not None else sorted(mapping.keys(), key=lambda x: x.lower())
        legend_defaults = self._default_legend_colors_for_palette(self.stats_palette_combo.currentText().strip().lower())
        for name in names:
            color = mapping.get(name, "")
            row = table.rowCount()
            table.insertRow(row)
            name_item = QTableWidgetItem(name)
            table.setItem(row, 0, name_item)
            if color:
                self._set_color_cell(table, row, color)
            else:
                if table is self.stats_legend_color_table:
                    self._set_auto_color_cell(table, row, legend_defaults.get(name, "#bdbdbd"))
                else:
                    self._set_auto_color_cell(table, row, self._auto_deck_preview_color(name))
        table.blockSignals(False)

    def _sync_color_tables_from_text(self) -> None:
        deck_map = self._parse_stats_deck_colors()
        legend_map = self._parse_stats_legend_colors()
        self._populate_color_table(self.stats_deck_color_table, deck_map)
        self._populate_color_table(self.stats_legend_color_table, legend_map, fixed_order=STATS_LEGEND_KEYS)
        for key, value in sorted(legend_map.items(), key=lambda x: x[0].lower()):
            if key in STATS_LEGEND_KEYS:
                continue
            row = self.stats_legend_color_table.rowCount()
            self.stats_legend_color_table.insertRow(row)
            self.stats_legend_color_table.setItem(row, 0, QTableWidgetItem(key))
            if value:
                self._set_color_cell(self.stats_legend_color_table, row, value)
            else:
                self._set_auto_color_cell(self.stats_legend_color_table, row, "#bdbdbd")

    def _sync_text_from_color_tables(self) -> None:
        deck_map = self._mapping_from_table(self.stats_deck_color_table)
        legend_map = self._mapping_from_table(self.stats_legend_color_table)
        self.stats_deck_colors_text.setPlainText(self._color_mapping_to_text(deck_map))
        self.stats_legend_colors_text.setPlainText(self._color_mapping_to_text(legend_map))

    def _pick_color_for_table_row(self, table: QTableWidget, row: int) -> None:
        if row < 0 or row >= table.rowCount():
            return
        name_item = table.item(row, 0)
        if name_item is None:
            return
        label = name_item.text().strip() or "Item"
        color_hex = self._pick_color_hex(f"Color: {label}")
        if not color_hex:
            return
        self._set_color_cell(table, row, color_hex)
        self._sync_text_from_color_tables()

    def _on_deck_color_table_double_clicked(self, row: int, column: int) -> None:
        if column == 1:
            self._pick_color_for_table_row(self.stats_deck_color_table, row)

    def _on_legend_color_table_double_clicked(self, row: int, column: int) -> None:
        if column == 1:
            self._pick_color_for_table_row(self.stats_legend_color_table, row)

    def _on_stats_palette_changed(self, _text: str) -> None:
        self._sync_color_tables_from_text()

    def _add_deck_color_row(self) -> None:
        row = self.stats_deck_color_table.rowCount()
        self.stats_deck_color_table.insertRow(row)
        self.stats_deck_color_table.setItem(row, 0, QTableWidgetItem(""))
        self._set_auto_color_cell(self.stats_deck_color_table, row, "#bdbdbd")
        self._sync_text_from_color_tables()

    def _remove_deck_color_row(self) -> None:
        row = self.stats_deck_color_table.currentRow()
        if row >= 0:
            self.stats_deck_color_table.removeRow(row)
            self._sync_text_from_color_tables()

    def _add_legend_color_row(self) -> None:
        label, ok = QInputDialog.getText(self, APP_NAME, "Legend item name:")
        if not ok:
            return
        legend_name = str(label).strip()
        if not legend_name:
            return
        row = self.stats_legend_color_table.rowCount()
        self.stats_legend_color_table.insertRow(row)
        self.stats_legend_color_table.setItem(row, 0, QTableWidgetItem(legend_name))
        defaults = self._default_legend_colors_for_palette(self.stats_palette_combo.currentText().strip().lower())
        self._set_auto_color_cell(self.stats_legend_color_table, row, defaults.get(legend_name, "#bdbdbd"))
        self._sync_text_from_color_tables()

    def _remove_legend_color_row(self) -> None:
        row = self.stats_legend_color_table.currentRow()
        if row >= 0:
            self.stats_legend_color_table.removeRow(row)
            self._sync_text_from_color_tables()

    def _load_deck_color_targets_from_input(self) -> None:
        input_path = Path(self.stats_input_edit.text().strip())
        if not input_path.exists():
            QMessageBox.information(self, APP_NAME, "Choose an input grouped XLSX first.")
            return
        try:
            df = pd.read_excel(input_path)
        except Exception as err:
            QMessageBox.warning(self, APP_NAME, f"Failed to read input file: {err}")
            return

        names: list[str] = []
        if "Deck" in df.columns:
            names.extend([str(v).strip() for v in df["Deck"].dropna().tolist() if str(v).strip()])
        if "Archetype" in df.columns:
            names.extend([str(v).strip() for v in df["Archetype"].dropna().tolist() if str(v).strip()])

        unique_names = sorted(set(names), key=lambda x: x.lower())
        if not unique_names:
            QMessageBox.information(self, APP_NAME, "No Deck/Archetype names found in input.")
            return

        current = self._mapping_from_table(self.stats_deck_color_table)
        for name in unique_names:
            current.setdefault(name, "")
        self._populate_color_table(self.stats_deck_color_table, current)
        self._sync_text_from_color_tables()

    def _upsert_color_line(self, text_edit: QTextEdit, key: str, color_hex: str) -> None:
        target = str(key).strip()
        color_text = str(color_hex).strip().lower()
        if not target or not color_text.startswith("#"):
            return

        lines = text_edit.toPlainText().splitlines()
        replaced = False
        updated: list[str] = []
        for line in lines:
            text = line.strip()
            if not text:
                updated.append(line)
                continue
            if "=" in text:
                name, _ = text.split("=", 1)
            elif ":" in text:
                name, _ = text.split(":", 1)
            else:
                updated.append(line)
                continue

            if str(name).strip().lower() == target.lower():
                updated.append(f"{target}={color_text}")
                replaced = True
            else:
                updated.append(line)

        if not replaced:
            if updated and str(updated[-1]).strip():
                updated.append("")
            updated.append(f"{target}={color_text}")

        text_edit.setPlainText("\n".join(updated).strip())

    def _color_preview_html(self, mapping: dict[str, str], empty_label: str) -> str:
        if not mapping:
            return empty_label
        chips: list[str] = []
        for name, color in sorted(mapping.items(), key=lambda x: x[0].lower()):
            chips.append(
                "<span style='display:inline-block; margin:2px 6px 2px 0; padding:2px 8px; "
                f"border:1px solid #b8a68a; border-radius:9px; background:{escape(color)};'>"
                f"<span style='font-weight:600; color:#1f1a17;'>{escape(name)}</span>"
                f" <span style='color:#1f1a17;'>{escape(color)}</span></span>"
            )
        return " ".join(chips)

    def _update_deck_color_preview(self) -> None:
        mapping = self._parse_stats_deck_colors()
        self.stats_deck_preview_label.setText(
            self._color_preview_html(mapping, "No deck color overrides.")
        )

    def _update_legend_color_preview(self) -> None:
        mapping = self._parse_stats_legend_colors()
        self.stats_legend_preview_label.setText(
            self._color_preview_html(mapping, "No legend color overrides.")
        )

    def _pick_color_hex(self, title: str) -> Optional[str]:
        color = QColorDialog.getColor(parent=self, title=title)
        if not color.isValid():
            return None
        return str(color.name()).lower()

    def _pick_deck_color_rule(self) -> None:
        deck_name = self.stats_deck_color_name_edit.text().strip()
        if not deck_name:
            deck_name, ok = QInputDialog.getText(self, APP_NAME, "Deck name:")
            if not ok:
                return
            deck_name = str(deck_name).strip()
        if not deck_name:
            return

        color_hex = self._pick_color_hex(f"Deck color: {deck_name}")
        if not color_hex:
            return
        self._upsert_color_line(self.stats_deck_colors_text, deck_name, color_hex)
        self.stats_deck_color_name_edit.setText(deck_name)

    def _pick_legend_color_rule(self) -> None:
        label = self.stats_legend_key_combo.currentText().strip()
        if not label:
            return
        color_hex = self._pick_color_hex(f"Legend color: {label}")
        if not color_hex:
            return
        self._upsert_color_line(self.stats_legend_colors_text, label, color_hex)

    def _pick_custom_legend_color_rule(self) -> None:
        label, ok = QInputDialog.getText(self, APP_NAME, "Legend label:")
        if not ok:
            return
        legend_label = str(label).strip()
        if not legend_label:
            return
        color_hex = self._pick_color_hex(f"Legend color: {legend_label}")
        if not color_hex:
            return
        self._upsert_color_line(self.stats_legend_colors_text, legend_label, color_hex)

    def _parse_stats_legend_colors(self) -> dict[str, str]:
        out: dict[str, str] = {}
        raw = self.stats_legend_colors_text.toPlainText().strip()
        if not raw:
            return out

        for line in raw.splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if "=" in text:
                name, color = text.split("=", 1)
            elif ":" in text:
                name, color = text.split(":", 1)
            else:
                continue

            label_name = str(name).strip()
            color_text = str(color).strip()
            if not label_name or not color_text:
                continue
            if not color_text.startswith("#"):
                color_text = f"#{color_text}"
            if len(color_text) != 7:
                continue
            hex_part = color_text[1:]
            if any(ch not in "0123456789abcdefABCDEF" for ch in hex_part):
                continue
            out[label_name] = f"#{hex_part.lower()}"
        return out

    # ------------------------------------------------------------------
    # Unknown archetype pre-stats check
    # ------------------------------------------------------------------

    def _find_unknown_archetypes_in_input(self, input_path: Path) -> List[dict]:
        """Read grouped XLSX and return rows where Archetype is Unknown."""
        try:
            df = pd.read_excel(input_path)
        except Exception:
            return []
        if "Archetype" not in df.columns or "Deck" not in df.columns:
            return []
        rows = []
        for _, row in df.iterrows():
            arch = str(row.get("Archetype") or "").strip().lower()
            if arch in {"unknown", "unkown", ""}:
                meta_val = row.get("Meta")
                try:
                    meta_str = f"{float(meta_val):.2f}"
                except Exception:
                    meta_str = str(meta_val or "")
                rows.append({
                    "deck": str(row.get("Deck") or "").strip(),
                    "meta": meta_str,
                    "archetype": "Unknown",
                })
        return rows

    def _save_archetype_assignments(self, assignments: List[dict], input_path: Path) -> int:
        """Save archetype assignments to archetype_rules.csv and patch the XLSX in place."""
        if not assignments:
            return 0

        model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
        for item in assignments:
            deck = str(item.get("deck") or "").strip()
            archetype = str(item.get("archetype") or "").strip()
            if not deck or not archetype or archetype.lower() == "unknown":
                continue
            upsert_archetype_catalog(self.config_dir, archetype)
            model.queue_archetype_rule(deck, archetype, match_type="exact", priority=1)

        saved = 0
        if model.has_changes():
            model.apply(create_backup=True)
            saved = len(assignments)

        # Patch the XLSX in place so the stats run immediately uses clean data
        try:
            df = pd.read_excel(input_path)
            assignment_map = {
                str(item["deck"]).strip().lower(): str(item["archetype"]).strip()
                for item in assignments
                if str(item.get("archetype") or "").strip().lower() not in {"", "unknown"}
            }
            if assignment_map and "Archetype" in df.columns and "Deck" in df.columns:
                def _remap(row):
                    key = str(row["Deck"] or "").strip().lower()
                    return assignment_map.get(key, str(row["Archetype"] or "").strip())
                df["Archetype"] = df.apply(_remap, axis=1)
                df.to_excel(input_path, index=False, engine="openpyxl")
        except Exception as err:
            self._append_stats_log(f"[WARN] Could not patch XLSX in place: {err}")

        return saved

    def _find_unknown_archetypes_in_challenge_history(
        self, history_csv: Path, format_name: str, last_n_events: int
    ) -> List[dict]:
        """Return decks with Unknown archetype within the same event window that
        run_challenge_statistics will actually chart (mirrors its _trim() logic:
        last_n_events*2 most recent event dates, across both challenge sizes)."""
        try:
            hist = load_challenge_history(history_csv)
        except Exception:
            return []
        if hist.empty or "Deck" not in hist.columns or "Archetype" not in hist.columns:
            return []

        fmt_norm = normalize_name(format_name)
        hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm].copy()
        if hist.empty:
            return []

        hist["EventDate"] = hist["EventDate"].astype(str).str.strip()
        dates = sorted(hist["EventDate"].unique(), reverse=True)[: max(1, last_n_events * 2)]
        hist = hist[hist["EventDate"].isin(dates)]
        total = len(hist)
        if total == 0:
            return []

        hist["Archetype"] = hist["Archetype"].astype(str).str.strip().replace("", "Unknown")
        unknown = hist[hist["Archetype"].str.lower().isin(["unknown", "unkown"])]
        if unknown.empty:
            return []

        counts = unknown.groupby("Deck").size().sort_values(ascending=False)
        return [
            {"deck": str(deck), "meta": f"{(count / total * 100):.2f}", "archetype": "Unknown"}
            for deck, count in counts.items()
        ]

    def _save_challenge_archetype_assignments(self, assignments: List[dict], history_csv: Path) -> int:
        """Save archetype assignments for Challenge decks: adds an archetype_rules.csv
        rule (so future fetches classify correctly) and patches the persistent
        challenge history CSV in place (so the stats run about to happen is clean)."""
        if not assignments:
            return 0

        model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
        assignment_map: dict[str, str] = {}
        for item in assignments:
            deck = str(item.get("deck") or "").strip()
            archetype = str(item.get("archetype") or "").strip()
            if not deck or not archetype or archetype.lower() in ("unknown", "unkown"):
                continue
            upsert_archetype_catalog(self.config_dir, archetype)
            model.queue_archetype_rule(deck, archetype, match_type="exact", priority=1)
            assignment_map[deck.lower()] = archetype

        if model.has_changes():
            model.apply(create_backup=True)

        if assignment_map and history_csv.exists():
            try:
                df = pd.read_csv(history_csv)
                if "Deck" in df.columns and "Archetype" in df.columns:
                    def _remap(row):
                        key = str(row["Deck"] or "").strip().lower()
                        return assignment_map.get(key, row["Archetype"])
                    df["Archetype"] = df.apply(_remap, axis=1)
                    df.to_csv(history_csv, index=False)
            except Exception as err:
                self._append_stats_log(f"[WARN] Could not patch challenge history CSV in place: {err}")

        return len(assignment_map)

    # ------------------------------------------------------------------
    # Add / Remove mapping
    # ------------------------------------------------------------------

    def _add_deck_mapping(self) -> None:
        """Open a dialog to manually add a deck mapping to config files."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Deck Mapping")
        dialog.resize(440, 180)
        form = QFormLayout(dialog)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)

        raw_input = QLineEdit()
        raw_input.setPlaceholderText("Raw deck name (as returned by API)")
        canonical_input = QLineEdit()
        canonical_input.setPlaceholderText("Canonical deck name")
        archetype_combo = QComboBox()
        archetype_combo.setEditable(True)
        archetype_combo.addItems(load_archetype_catalog(self.config_dir))

        form.addRow("Raw Name", raw_input)
        form.addRow("Canonical Name", canonical_input)
        form.addRow("Archetype", archetype_combo)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Add")
        ok_btn.setObjectName("primaryButton")
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_widget = QWidget()
        btn_widget.setLayout(btn_row)
        form.addRow("", btn_widget)

        if dialog.exec() != QDialog.Accepted:
            return

        raw_name = raw_input.text().strip()
        canonical_name = canonical_input.text().strip()
        archetype = archetype_combo.currentText().strip() or "Unknown"

        if not raw_name or not canonical_name:
            QMessageBox.warning(self, APP_NAME, "Raw name and canonical name are required.")
            return

        upsert_archetype_catalog(self.config_dir, archetype)
        model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
        model.queue_mapping(raw_name, canonical_name, archetype)
        model.queue_alias(raw_name, canonical_name, match_type="exact", priority=1)
        model.queue_archetype_rule(raw_name, archetype, match_type="exact", priority=1)
        model.queue_archetype_rule(canonical_name, archetype, match_type="exact", priority=1)
        if model.has_changes():
            model.apply(create_backup=True)

        # Insert at top of editor table for immediate visibility
        self.editor_rows.insert(0, {
            "raw_deck": raw_name,
            "deck": canonical_name,
            "archetype": archetype,
            "meta": "",
            "my_wr": "",
        })
        self._populate_editor_table()
        self.editor_table.selectRow(0)
        self._refresh_archetype_combo()
        self.editor_status_label.setText(
            f"Added mapping: '{raw_name}' → '{canonical_name}' ({archetype})"
        )
        self._append_log(f"[OK] Added mapping: '{raw_name}' → '{canonical_name}', archetype='{archetype}'")

    def _remove_deck_mapping(self) -> None:
        """Remove the selected editor row's mapping from config files."""
        row_index = self.editor_table.currentRow()
        if row_index < 0 or row_index >= len(self.editor_rows):
            QMessageBox.warning(self, APP_NAME, "Select a row in the editor table first.")
            return

        row = self.editor_rows[row_index]
        raw_name = str(row.get("raw_deck") or "").strip()
        deck_name = str(row.get("deck") or "").strip()
        if not raw_name:
            QMessageBox.warning(self, APP_NAME, "Selected row has no raw name — nothing to remove from config.")
            return

        confirm = QMessageBox.question(
            self,
            APP_NAME,
            f"Remove mapping for '{raw_name}' → '{deck_name}' from config files?\n\n"
            "This removes the entry from user_deck_mapping.csv, deck_aliases.csv, and archetype_rules.csv.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        # Multiple raw names can be pipe-separated in grouped rows — remove each
        raw_names = self._split_raw_names(raw_name) or [raw_name]
        total_removed = 0
        for rn in raw_names:
            total_removed += self._remove_from_mapping_csv(self.config_dir / "user_deck_mapping.csv", "raw_name", rn)
            self._remove_from_mapping_csv(self.config_dir / "deck_aliases.csv", "pattern", rn)
            self._remove_from_mapping_csv(self.config_dir / "archetype_rules.csv", "pattern", rn)

        self.editor_rows.pop(row_index)
        self._populate_editor_table()
        new_idx = min(row_index, len(self.editor_rows) - 1)
        if new_idx >= 0:
            self.editor_table.selectRow(new_idx)

        self.editor_status_label.setText(
            f"Removed mapping for '{raw_name}' from config files (entries removed: {total_removed})."
        )
        self._append_log(f"[OK] Removed mapping: '{raw_name}' from config CSVs.")

    def _remove_from_mapping_csv(self, path: Path, key_column: str, key_value: str) -> int:
        """Remove rows where key_column matches key_value from a CSV. Returns count removed."""
        if not path.exists():
            return 0
        key_norm = key_value.strip().lower()
        rows_keep = []
        removed = 0
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            for row in reader:
                if str(row.get(key_column) or "").strip().lower() == key_norm:
                    removed += 1
                else:
                    rows_keep.append(row)
        if removed > 0:
            import shutil as _shutil
            backup = path.with_suffix(f".bak{path.suffix}")
            _shutil.copy2(path, backup)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows_keep)
        return removed

    # ------------------------------------------------------------------
    # Search / filter
    # ------------------------------------------------------------------

    def _filter_editor_table(self) -> None:
        """Show only rows matching the search query (deck name, raw name, or archetype)."""
        query = self.editor_search_input.text().strip().lower()
        visible = 0
        total = self.editor_table.rowCount()
        for row_idx in range(total):
            if not query:
                self.editor_table.setRowHidden(row_idx, False)
                visible += 1
                continue
            row = self.editor_rows[row_idx] if row_idx < len(self.editor_rows) else {}
            deck = str(row.get("deck") or "").lower()
            raw = str(row.get("raw_deck") or "").lower()
            archetype = str(row.get("archetype") or "").lower()
            match = query in deck or query in raw or query in archetype
            self.editor_table.setRowHidden(row_idx, not match)
            if match:
                visible += 1
        if query:
            self.editor_match_label.setText(f"{visible}/{total}")
        else:
            self.editor_match_label.setText("")

    def _visible_row_indices(self) -> List[int]:
        """Return indices of editor_rows that are currently visible (not hidden by filter)."""
        return [
            idx for idx in range(min(self.editor_table.rowCount(), len(self.editor_rows)))
            if not self.editor_table.isRowHidden(idx)
        ]

    def _open_global_search(self) -> None:
        """Open GlobalSearchDialog with all CSV sources from the editor combo."""
        sources: List[tuple] = []
        for i in range(self.editor_source_combo.count()):
            label = self.editor_source_combo.itemText(i)
            data = self.editor_source_combo.itemData(i)
            if data:
                sources.append((label, str(data)))
        if not sources:
            QMessageBox.information(self, APP_NAME, "Brak plików źródłowych. Wygeneruj najpierw dane.")
            return
        dlg = GlobalSearchDialog(
            sources=sources,
            archetype_options=load_archetype_catalog(self.config_dir),
            deck_options=self._load_deck_catalog(),
            config_dir=self.config_dir,
            parent=self,
        )
        dlg.navigate_to.connect(self._navigate_to_global_result)
        dlg.save_changes.connect(self._handle_global_search_save)
        dlg.exec()

    def _navigate_to_global_result(self, csv_path_str: str) -> None:
        """Select the matching source file in the combo and load it in the editor."""
        for i in range(self.editor_source_combo.count()):
            if str(self.editor_source_combo.itemData(i)) == csv_path_str:
                self.editor_source_combo.setCurrentIndex(i)
                return
        QMessageBox.warning(self, APP_NAME, f"Nie znaleziono wpisu dla:\n{csv_path_str}")

    def _handle_global_search_save(self, changes: list) -> None:
        """Save edits from GlobalSearchDialog to config CSVs, then propagate them into
        already-generated grouped XLSX / history CSV / challenge decklists across every
        week — otherwise the config change never reaches the files stats charts read."""
        meta_changes = [c for c in changes if not c["is_challenge"]]
        challenge_changes = [c for c in changes if c["is_challenge"]]

        saved_meta = 0
        saved_challenge = 0
        meta_payloads: List[tuple[List[str], str, str]] = []
        challenge_payloads: List[tuple[List[str], str, str]] = []

        if meta_changes:
            model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
            for c in meta_changes:
                raw = c["raw_deck"]
                deck = c["deck"]
                arch = c["archetype"]
                if not raw or not deck or not arch:
                    continue
                model.queue_mapping(raw, deck, arch)
                model.queue_alias(raw, deck, match_type="exact", priority=1)
                model.queue_archetype_rule(raw, arch, match_type="exact", priority=1)
                model.queue_archetype_rule(deck, arch, match_type="exact", priority=1)
                meta_payloads.append(([raw], deck, arch))
                saved_meta += 1
            if model.has_changes():
                model.apply(create_backup=True)

        if challenge_changes:
            path = self._challenge_mapping_csv_path()
            existing: dict[str, dict] = {}
            if path.exists():
                with path.open("r", encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        k = str(row.get("raw_name") or "").strip().lower()
                        if k:
                            existing[k] = {"raw_name": row.get("raw_name", ""), "canonical_name": row.get("canonical_name", ""), "archetype": row.get("archetype", "")}
            for c in challenge_changes:
                raw = c["raw_deck"]
                deck = c["deck"]
                arch = c["archetype"]
                if not raw or not deck or not arch:
                    continue
                existing[raw.lower()] = {"raw_name": raw, "canonical_name": deck, "archetype": arch}
                challenge_payloads.append(([raw], deck, arch))
                saved_challenge += 1
            if existing:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=["raw_name", "canonical_name", "archetype"])
                    writer.writeheader()
                    writer.writerows(existing.values())

        backfill_files = 0
        backfill_rows = 0
        if meta_payloads:
            f, r = self._propagate_mappings_to_history(meta_payloads, None)
            backfill_files += f
            backfill_rows += r
        if challenge_payloads:
            f, r = self._propagate_mappings_to_challenge_decklists(challenge_payloads, None)
            backfill_files += f
            backfill_rows += r

        self._refresh_archetype_combo()
        if saved_meta or saved_challenge:
            self._append_log(
                f"[OK] Global Search — zapisano: meta={saved_meta}, challenge={saved_challenge} wpisów do plików config. "
                f"Zaktualizowano {backfill_files} plik(ów) danych ({backfill_rows} wiersz(y)) we wszystkich tygodniach."
            )
        else:
            self._append_log("[WARN] Global Search — brak prawidłowych zmian do zapisania (brak raw/deck/archetype).")

    def _apply_archetype_to_visible(self) -> None:
        """Apply current archetype selection to all rows visible in the filtered table."""
        archetype = self.editor_archetype_combo.currentText().strip()
        if not archetype:
            QMessageBox.warning(self, APP_NAME, "Wybierz najpierw archetype w polu 'Archetype'.")
            return
        indices = self._visible_row_indices()
        if not indices:
            QMessageBox.information(self, APP_NAME, "Brak widocznych wierszy pasujących do filtru.")
            return
        for idx in indices:
            self.editor_rows[idx]["archetype"] = archetype
            widget = self.editor_table.cellWidget(idx, 2)
            if isinstance(widget, QComboBox):
                widget.blockSignals(True)
                widget.setCurrentText(archetype)
                widget.blockSignals(False)
                self._apply_unknown_archetype_style(widget, archetype)
        # Update right-panel form if current row is one of the affected ones
        current = self.editor_table.currentRow()
        if current in indices:
            self._select_archetype(archetype)
            self._apply_unknown_archetype_style(self.editor_archetype_combo, archetype)
        self.editor_status_label.setText(
            f"Przypisano archetype '{archetype}' do {len(indices)} wiersz(y). "
            "Użyj 'Save All' aby zapisać do plików config."
        )

    def _apply_archetype_to_visible_and_save(self) -> None:
        """Apply archetype to all visible rows and immediately save to config files."""
        archetype = self.editor_archetype_combo.currentText().strip()
        if not archetype:
            QMessageBox.warning(self, APP_NAME, "Wybierz najpierw archetype w polu 'Archetype'.")
            return
        indices = self._visible_row_indices()
        if not indices:
            QMessageBox.information(self, APP_NAME, "Brak widocznych wierszy pasujących do filtru.")
            return

        # Apply in-memory
        self._apply_archetype_to_visible()

        # Save all affected rows to config
        if self._is_current_source_challenge():
            payloads = self._collect_mapping_payloads(indices)
            upserts = self._upsert_challenge_mapping_payloads(payloads)
            self._save_rows_to_config(indices)
            backfill_files, backfill_rows = self._propagate_mappings_to_challenge_decklists(
                payloads, self._current_editor_week_end()
            )
            self.editor_status_label.setText(
                f"Zapisano archetype '{archetype}' dla {upserts} wiersz(y) Challenge. "
                f"Zaktualizowano {backfill_files} plik(ów) challenge, {backfill_rows} wiersz(y)."
            )
        else:
            processed, source_names, verified, summary, skipped = self._save_rows_to_config(indices)
            payloads = self._collect_mapping_payloads(indices)
            backfill_files, backfill_rows = self._propagate_mappings_to_history(
                payloads, self._current_editor_week_end()
            )
            self.editor_status_label.setText(
                f"Zapisano archetype '{archetype}' dla {processed} wiersz(y). "
                f"Zaktualizowano {backfill_files} plik(ów) historii, {backfill_rows} wiersz(y)."
            )
        self._refresh_archetype_combo()

    def start_statistics(self) -> None:
        if self.stats_thread is not None or self.challenge_stats_thread is not None:
            QMessageBox.information(self, APP_NAME, "Statistics analysis is already running.")
            return

        mode = self.stats_type_combo.currentText()

        self.stats_run_button.setEnabled(False)
        self.stats_open_folder_button.setEnabled(False)
        self.stats_files_list.clear()
        self.stats_log_output.clear()
        self.last_stats_result = None

        if mode in ("Liga", "Oba"):
            self._start_liga_statistics(run_challenge_after=(mode == "Oba"))
        else:
            self._start_challenge_statistics()

    def _start_liga_statistics(self, run_challenge_after: bool = False) -> None:
        self._sync_text_from_color_tables()

        input_path = Path(self.stats_input_edit.text().strip())
        if not input_path.exists():
            QMessageBox.warning(self, APP_NAME, "Wybierz plik grouped XLSX dla trybu Liga.")
            self.stats_run_button.setEnabled(True)
            return

        # --- Pre-stats unknown archetype check ---
        unknown_rows = self._find_unknown_archetypes_in_input(input_path)
        if unknown_rows:
            dlg = UnknownArchetypeDialog(
                unknown_rows,
                archetype_options=load_archetype_catalog(self.config_dir),
                parent=self,
            )
            result = dlg.exec()
            if result == QDialog.Rejected:
                self.stats_run_button.setEnabled(True)
                return
            if not dlg.skip_requested:
                assignments = dlg.get_assignments()
                if assignments:
                    saved = self._save_archetype_assignments(assignments, input_path)
                    self._append_stats_log(
                        f"[OK] Saved {saved} archetype assignment(s) — config and XLSX updated."
                    )
                    self._refresh_archetype_combo()
        # --- end pre-check ---

        history_text = self.stats_history_edit.text().strip()
        history_path = Path(history_text) if history_text else self._default_statistics_history_path()
        output_dir = self._statistics_output_dir_for_input(input_path)
        deck_colors = self._parse_stats_deck_colors()
        legend_colors = self._parse_stats_legend_colors()
        output_profile = self.stats_output_profile_combo.currentText().strip().lower() or "full"
        palette_name = self.stats_palette_combo.currentText().strip().lower() or "classic"

        self.stats_summary_label.setText("Generowanie statystyk: Liga…")
        self._append_stats_log(f"[liga] Analiza: {input_path}")

        self.stats_thread = QThread(self)
        self.stats_worker = StatisticsWorker(
            input_excel=input_path,
            output_dir=output_dir,
            history_csv=history_path,
            total_players=self.stats_total_players_spin.value(),
            rounds=self.stats_rounds_spin.value(),
            min_encounter_pct=self.stats_min_encounter_spin.value(),
            player_deck_name=self.stats_player_deck_edit.text().strip() or "My Deck",
            player_winrate=self.stats_player_wr_spin.value() / 100.0,
            weeks_back=self.stats_weeks_back_spin.value(),
            output_profile=output_profile,
            palette_name=palette_name,
            deck_colors=deck_colors,
            legend_colors=legend_colors,
        )
        self.stats_worker.moveToThread(self.stats_thread)
        self.stats_thread.started.connect(self.stats_worker.run)
        self.stats_worker.progress.connect(self._append_stats_log)
        self.stats_worker.finished.connect(self._handle_statistics_success)
        self.stats_worker.failed.connect(self._handle_statistics_failure)
        self.stats_worker.finished.connect(self.stats_thread.quit)
        self.stats_worker.failed.connect(self.stats_thread.quit)
        if run_challenge_after:
            self.stats_worker.finished.connect(self._on_liga_finished_run_challenge)
            self.stats_worker.failed.connect(self._on_liga_failed_after_oba)
        self.stats_thread.finished.connect(self._cleanup_statistics_worker)
        self.stats_thread.start()

    def _on_liga_finished_run_challenge(self, _result) -> None:
        """'Oba' mode: chain into Challenge stats after Liga finishes.

        Must be a bound method (not a lambda) — self.stats_worker lives in a
        background QThread, and PySide can only auto-queue a cross-thread signal
        onto the GUI thread when the receiver is a QObject's bound method. A bare
        lambda has no thread affinity, so it would run _start_challenge_statistics()
        (which builds/execs a QDialog) directly on the worker thread and freeze the app.
        """
        self._start_challenge_statistics()

    def _on_liga_failed_after_oba(self, _details: str) -> None:
        self.stats_run_button.setEnabled(True)

    def _start_challenge_statistics(self) -> None:
        history_path = self._default_challenge_history_path()
        if not history_path.exists():
            self._append_stats_log(
                f"[challenge] Brak pliku historii: {history_path}\n"
                "Wygeneruj dane z włączoną opcją 'Challenge Window' lub kliknij 'Rebuild Challenge History'."
            )
            self.stats_run_button.setEnabled(True)
            self.stats_summary_label.setText("Brak danych challenge. Wygeneruj najpierw historię challenge.")
            return

        format_name = self.format_combo.currentText().strip() or "Modern"
        last_n = self.challenge_history_events_spin.value()

        # --- Pre-stats unknown archetype check (mirrors Liga flow) ---
        unknown_rows = self._find_unknown_archetypes_in_challenge_history(history_path, format_name, last_n)
        if unknown_rows:
            dlg = UnknownArchetypeDialog(
                unknown_rows,
                archetype_options=load_archetype_catalog(self.config_dir),
                parent=self,
            )
            result = dlg.exec()
            if result == QDialog.Rejected:
                self.stats_run_button.setEnabled(True)
                return
            if not dlg.skip_requested:
                assignments = dlg.get_assignments()
                if assignments:
                    saved = self._save_challenge_archetype_assignments(assignments, history_path)
                    self._append_stats_log(
                        f"[OK] Saved {saved} archetype assignment(s) for Challenge — "
                        "config and history CSV updated."
                    )
                    self._refresh_archetype_combo()
        # --- end pre-check ---

        output_dir = self._challenge_stats_output_dir()
        week_start, week_end = self._challenge_stats_window()

        self.stats_summary_label.setText("Generowanie statystyk: Challenge…")
        if week_start is not None and week_end is not None:
            self._append_stats_log(
                f"[challenge] Historia: {history_path}, last_n={last_n}, "
                f"window={week_start.isoformat()}..{week_end.isoformat()}"
            )
        else:
            self._append_stats_log(
                f"[challenge] Historia: {history_path}, last_n={last_n} "
                "(no week window resolved -- falling back to last-N-dates trim; this can pull in "
                "extra events from an adjacent week, see _challenge_stats_window)"
            )

        self.challenge_stats_thread = QThread(self)
        self.challenge_stats_worker = ChallengeStatisticsWorker(
            history_csv=history_path,
            output_dir=output_dir,
            format_name=format_name,
            last_n_events=last_n,
            week_start=week_start,
            week_end=week_end,
        )
        self.challenge_stats_worker.moveToThread(self.challenge_stats_thread)
        self.challenge_stats_thread.started.connect(self.challenge_stats_worker.run)
        self.challenge_stats_worker.progress.connect(self._append_stats_log)
        self.challenge_stats_worker.finished.connect(self._handle_challenge_statistics_success)
        self.challenge_stats_worker.failed.connect(self._handle_challenge_statistics_failure)
        self.challenge_stats_worker.finished.connect(self.challenge_stats_thread.quit)
        self.challenge_stats_worker.failed.connect(self.challenge_stats_thread.quit)
        self.challenge_stats_thread.finished.connect(self._cleanup_challenge_statistics_worker)
        self.challenge_stats_thread.start()

    def _append_stats_log(self, line: str) -> None:
        self.stats_log_output.append(line)

    def _handle_statistics_success(self, result) -> None:
        self.last_stats_result = result
        mode = self.stats_type_combo.currentText()
        if mode != "Oba":
            self.stats_run_button.setEnabled(True)
        self.stats_open_folder_button.setEnabled(True)

        self.stats_history_edit.setText(str(result.history_csv))
        self.stats_summary_label.setText(
            "\n".join([
                f"Liga — Week index: {result.week_index}",
                f"Deck rows: {result.deck_rows}",
                f"Archetype rows: {result.archetype_rows}",
                f"Output: {result.output_dir}",
            ])
        )

        for path in result.files:
            item = QListWidgetItem(f"[Liga] {path.name}")
            item.setData(Qt.UserRole, str(path))
            self.stats_files_list.addItem(item)

        self._append_stats_log("[liga] Analiza zakończona.")

    def _handle_statistics_failure(self, details: str) -> None:
        mode = self.stats_type_combo.currentText()
        if mode != "Oba":
            self.stats_run_button.setEnabled(True)
        self.stats_open_folder_button.setEnabled(False)
        self.stats_summary_label.setText("Statystyki Liga — błąd.")
        self._append_stats_log("[liga][error] Analiza nieudana.")
        self._append_stats_log(details)
        QMessageBox.critical(self, APP_NAME, details.splitlines()[0] if details else "Statystyki Liga nieudane.")

    def _handle_challenge_statistics_success(self, result) -> None:
        self.stats_run_button.setEnabled(True)
        self.stats_open_folder_button.setEnabled(True)
        self.stats_summary_label.setText(
            self.stats_summary_label.text().rstrip() + f"\nChallenge — pliki: {result.output_dir}"
        )
        for path in getattr(result, "files", []):
            item = QListWidgetItem(f"[Challenge] {path.name}")
            item.setData(Qt.UserRole, str(path))
            self.stats_files_list.addItem(item)
        excel = getattr(result, "excel_path", None)
        if excel and Path(excel).exists():
            item = QListWidgetItem(f"[Challenge] {Path(excel).name}")
            item.setData(Qt.UserRole, str(excel))
            self.stats_files_list.addItem(item)
        self._append_stats_log("[challenge] Analiza zakończona.")

    def _handle_challenge_statistics_failure(self, details: str) -> None:
        self.stats_run_button.setEnabled(True)
        self.stats_summary_label.setText(self.stats_summary_label.text() + "\nChallenge — błąd.")
        self._append_stats_log("[challenge][error] Analiza nieudana.")
        self._append_stats_log(details)
        QMessageBox.critical(self, APP_NAME, details.splitlines()[0] if details else "Statystyki Challenge nieudane.")

    def _cleanup_statistics_worker(self) -> None:
        if self.stats_worker is not None:
            self.stats_worker.deleteLater()
        if self.stats_thread is not None:
            self.stats_thread.deleteLater()
        self.stats_worker = None
        self.stats_thread = None

    def _cleanup_challenge_statistics_worker(self) -> None:
        if self.challenge_stats_worker is not None:
            self.challenge_stats_worker.deleteLater()
        if self.challenge_stats_thread is not None:
            self.challenge_stats_thread.deleteLater()
        self.challenge_stats_worker = None
        self.challenge_stats_thread = None

    def _build_args(self) -> Namespace:
        args = self._build_generator_args()
        return args

    def start_generation(self) -> None:
        if self.week_end_edit.date() < self.week_start_edit.date():
            QMessageBox.warning(self, APP_NAME, "Week end must be greater than or equal to week start.")
            return

        self._save_state()
        args = self._build_args()
        self.log_output.clear()
        self.files_list.clear()
        self.summary_label.setText("Running...")
        self._append_log(
            f"Starting generation for {args.format_name} | {args.week_start} -> {args.week_end} | deck={args.my_deck}"
        )

        self.generate_button.setEnabled(False)
        self.open_output_button.setEnabled(False)
        self.open_main_file_button.setEnabled(False)
        self.last_results = []

        self.worker_thread = QThread(self)
        self.worker = GeneratorWorker(args)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._append_log)
        self.worker.finished.connect(self._handle_success)
        self.worker.failed.connect(self._handle_failure)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()

    def refresh_deck_list(self) -> None:
        if self.week_end_edit.date() < self.week_start_edit.date():
            return
        if self.deck_thread is not None:
            self.deck_refresh_pending = True
            return

        self.deck_refresh_pending = False
        format_name = self.format_combo.currentText()
        week_start = self.week_start_edit.date().toString("yyyy-MM-dd")
        week_end = self.week_end_edit.date().toString("yyyy-MM-dd")
        limit = self.metagame_limit_spin.value()

        self.my_deck_combo.clear()
        self.my_deck_combo.addItem("Loading deck list...")
        self.my_deck_combo.setEnabled(False)
        self.deck_mode_label.setText("Deck mode: loading API deck list for the selected format and date range...")
        self.refresh_decks_button.setEnabled(False)

        self.deck_thread = QThread(self)
        self.deck_worker = DeckListWorker(format_name, week_start, week_end, limit, backup_url=self._backup_url())
        self.deck_worker.moveToThread(self.deck_thread)
        self.deck_thread.started.connect(self.deck_worker.run)
        self.deck_worker.finished.connect(self._handle_deck_list_success)
        self.deck_worker.failed.connect(self._handle_deck_list_failure)
        self.deck_worker.finished.connect(self.deck_thread.quit)
        self.deck_worker.failed.connect(self.deck_thread.quit)
        self.deck_thread.finished.connect(self._cleanup_deck_worker)
        self.deck_thread.start()

    def _handle_deck_list_success(self, decks: List[str]) -> None:
        saved_deck = str(self.settings.value("my_deck", "Domain Zoo") or "Domain Zoo")
        current_data = self.my_deck_combo.currentData(Qt.UserRole)
        current_text = self.my_deck_combo.currentText()
        preferred = str(current_data or current_text or saved_deck)

        options = [SPECIAL_MY_DECK] + decks
        if not decks:
            options = [SPECIAL_MY_DECK]

        self.my_deck_combo.clear()
        for name in options:
            value = "My Deck" if name == SPECIAL_MY_DECK else name
            self.my_deck_combo.addItem(name, userData=value)

        selected_index = 0
        for idx in range(self.my_deck_combo.count()):
            candidate = str(self.my_deck_combo.itemData(idx, Qt.UserRole) or "")
            if candidate == preferred or self.my_deck_combo.itemText(idx) == saved_deck:
                selected_index = idx
                break
        self.my_deck_combo.setCurrentIndex(selected_index)
        self.my_deck_combo.setEnabled(True)
        self.refresh_decks_button.setEnabled(True)
        self._update_deck_mode_status()

    def _handle_deck_list_failure(self, message: str) -> None:
        saved_deck = str(self.settings.value("my_deck", "Domain Zoo") or "Domain Zoo")
        self.my_deck_combo.clear()
        self.my_deck_combo.addItem(SPECIAL_MY_DECK, userData="My Deck")
        self.my_deck_combo.addItem(saved_deck, userData=saved_deck)
        self.my_deck_combo.setCurrentIndex(1)
        self.my_deck_combo.setEnabled(True)
        self.refresh_decks_button.setEnabled(True)
        self._update_deck_mode_status()
        self._append_log(f"[WARN] Could not load deck list from API: {message}")

    def _update_deck_mode_status(self) -> None:
        selected_value = str(self.my_deck_combo.currentData(Qt.UserRole) or "").strip()
        selected_text = self.my_deck_combo.currentText().strip()

        if not selected_text or selected_text == "Loading deck list...":
            self.deck_mode_label.setText("Deck mode: loading API deck list for the selected format and date range...")
            return

        if selected_value == "My Deck":
            self.deck_mode_label.setText("Deck mode: force 50% fallback. My Deck Winrate will be imputed to 50% for all rows.")
            return

        self.deck_mode_label.setText(f"Deck mode: API deck selection. Matchups will be looked up for '{selected_value}'.")

    def _cleanup_deck_worker(self) -> None:
        if self.deck_worker is not None:
            self.deck_worker.deleteLater()
        if self.deck_thread is not None:
            self.deck_thread.deleteLater()
        self.deck_worker = None
        self.deck_thread = None
        if self.deck_refresh_pending:
            self.refresh_deck_list()

    def _append_log(self, line: str) -> None:
        self.log_output.append(line)

    def _handle_success(self, results: List[GenerationRunResult]) -> None:
        self.last_results = results
        if not results:
            self.summary_label.setText("Generation finished, but no result metadata was returned.")
            return

        latest = results[-1]
        self.summary_label.setText(
            "\n".join(
                [
                    f"Range: {latest.week_start.isoformat()} to {latest.week_end.isoformat()}",
                    f"Rows: {latest.row_count}",
                    f"User mappings: {latest.mapped_from_user}",
                    f"My WR coverage: primary {latest.primary_count}, fallback {latest.fallback_count}, imputed {latest.imputed_count}",
                    f"Challenge events fetched: {latest.challenge_events_fetched}",
                    f"Output: {latest.run_dir}",
                ]
            )
        )
        self._populate_files(latest)
        self.generate_button.setEnabled(True)
        self.open_output_button.setEnabled(True)
        self.open_main_file_button.setEnabled(latest.xlsx_path is not None)
        self._append_log("[OK] GUI run finished.")
        self.load_latest_output_for_editor()
        self._set_statistics_input_from_latest()
        if self.auto_stats_after_generate_check.isChecked() and self.stats_thread is None:
            self._append_log("[INFO] Auto-run Statistics is enabled. Starting statistics analysis...")
            self.start_statistics()

        all_review_items = [item for r in results for item in getattr(r, "challenge_review_items", [])]
        if all_review_items and latest.challenge_history_path is not None:
            self._append_log(
                f"[WARN] {len(all_review_items)} Challenge deck(s) need manual review before they count "
                "toward the statistics."
            )
            answer = QMessageBox.question(
                self,
                APP_NAME,
                f"{len(all_review_items)} Challenge deck(s) could not be confidently labeled "
                "(place-1 winners, and any borderline archetype matches).\n\n"
                "Open the Challenge Review Queue now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self._open_challenge_review_queue(
                    all_review_items, latest.challenge_history_path, latest.premier_history_path
                )

    def _open_challenge_review_queue(
        self, review_items: List[dict], history_csv: Path, premier_history_csv: Optional[Path] = None
    ) -> None:
        if self.challenge_review_dialog is None:
            self.challenge_review_dialog = ChallengeReviewDialog(
                review_items,
                deck_options=self._load_deck_catalog(),
                history_csv=history_csv,
                premier_history_csv=premier_history_csv,
                parent=self,
            )
        else:
            self.challenge_review_dialog._review_items = list(review_items)
            self.challenge_review_dialog._history_csv = history_csv
            self.challenge_review_dialog._premier_history_csv = premier_history_csv
            self.challenge_review_dialog._deck_options = self._load_deck_catalog()
            self.challenge_review_dialog._populate()
        self.challenge_review_dialog.show()
        self.challenge_review_dialog.raise_()
        self.challenge_review_dialog.activateWindow()

    def _start_challenge_rescan(self) -> None:
        if self.rescan_thread is not None:
            QMessageBox.information(self, APP_NAME, "A rescan is already running.")
            return

        challenge_history_csv = self._default_challenge_history_path()
        premier_history_csv = self._default_premier_history_path()
        if not challenge_history_csv.exists():
            QMessageBox.information(
                self, APP_NAME, f"No Challenge history file yet:\n{challenge_history_csv}"
            )
            return

        format_name = self.format_combo.currentText().strip() or "Modern"
        cache_dir = self.repo_root / "outputs" / "cache" / "mtgo_json" / "-".join(format_name.lower().split())
        rules_csv = self.config_dir / "archetype_rules.csv"
        challenge_mapping_csv = self.config_dir / "challenge_deck_mapping.csv"

        self.challenge_rescan_button.setEnabled(False)
        self._append_log(f"[rescan] Scanning {challenge_history_csv.name} and {premier_history_csv.name} for unresolved decks...")

        self.rescan_thread = QThread(self)
        self.rescan_worker = ChallengeRescanWorker(
            challenge_history_csv=challenge_history_csv,
            premier_history_csv=premier_history_csv if premier_history_csv.exists() else None,
            cache_dir=cache_dir,
            format_name=format_name,
            rules_csv=rules_csv,
            challenge_mapping_csv=challenge_mapping_csv,
        )
        self.rescan_worker.moveToThread(self.rescan_thread)
        self.rescan_thread.started.connect(self.rescan_worker.run)
        self.rescan_worker.progress.connect(self._append_log)
        self.rescan_worker.finished.connect(self._handle_rescan_success)
        self.rescan_worker.failed.connect(self._handle_rescan_failure)
        self.rescan_worker.finished.connect(self.rescan_thread.quit)
        self.rescan_worker.failed.connect(self.rescan_thread.quit)
        self.rescan_thread.finished.connect(self._cleanup_rescan_worker)
        self.rescan_thread.start()

    def _handle_rescan_success(self, review_items: List[dict]) -> None:
        self.challenge_rescan_button.setEnabled(True)
        self._append_log(f"[rescan] Done. {len(review_items)} deck(s) still need manual review.")
        if not review_items:
            QMessageBox.information(self, APP_NAME, "No unresolved decks found across the persisted history files.")
            return
        self._open_challenge_review_queue(
            review_items, self._default_challenge_history_path(), self._default_premier_history_path()
        )

    def _handle_rescan_failure(self, details: str) -> None:
        self.challenge_rescan_button.setEnabled(True)
        self._append_log("[rescan] FAILED.")
        self._append_log(details)
        QMessageBox.critical(self, APP_NAME, details.splitlines()[0] if details else "Rescan failed.")

    def _cleanup_rescan_worker(self) -> None:
        if self.rescan_worker is not None:
            self.rescan_worker.deleteLater()
        if self.rescan_thread is not None:
            self.rescan_thread.deleteLater()
        self.rescan_worker = None
        self.rescan_thread = None

    def _handle_failure(self, details: str) -> None:
        self.summary_label.setText("Generation failed.")
        self.generate_button.setEnabled(True)
        self.open_output_button.setEnabled(bool(self.last_results))
        self.open_main_file_button.setEnabled(False)
        self._append_log("[ERROR] Run failed.")
        self._append_log(details)
        QMessageBox.critical(self, APP_NAME, details.splitlines()[0] if details else "Generation failed.")

    def _cleanup_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None

    def _populate_files(self, result: GenerationRunResult) -> None:
        self.files_list.clear()
        paths: list[Optional[Path]] = [
            result.xlsx_path,
            result.csv_path,
            result.rogue_xml_path,
            result.rogue_xlsx_path,
            result.rogue_csv_path,
            result.unknown_output_path,
            result.alias_suggestions_path,
            result.challenge_history_path,
            result.challenge_stats_path,
        ]

        # Include per-event challenge csvs/charts from run directory.
        if result.run_dir.exists():
            for extra in sorted(result.run_dir.glob("challenge_*_decklist.csv")):
                paths.append(extra)
            for extra in sorted(result.run_dir.glob("challenge_*_vs_metagame.csv")):
                paths.append(extra)
            for extra in sorted(result.run_dir.glob("challenge_*.png")):
                paths.append(extra)
            for extra in sorted(result.run_dir.glob("challenge_manifest_*.csv")):
                paths.append(extra)
            for extra in sorted(result.run_dir.glob("premier_events_*.csv")):
                paths.append(extra)
            for extra in sorted(result.run_dir.glob("challenge_needs_review_*.csv")):
                paths.append(extra)

        seen: set[str] = set()
        for path in paths:
            if path is None:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            self.files_list.addItem(item)

    def _find_latest_range_dir(self, base_path: Path) -> Optional[Path]:
        """Return the most recently modified run directory under outputs/YYYY/MM/WNN_*."""
        from metagame_input_generator import scan_run_dirs
        runs = scan_run_dirs(base_path)
        if not runs:
            # Fallback: legacy flat structure
            if not base_path.exists():
                return None
            candidates = [p for p in base_path.iterdir() if p.is_dir() and "_to_" in p.name]
            if not candidates:
                return None
            return max(candidates, key=lambda p: p.stat().st_mtime)
        return runs[-1][2]  # newest (week_start, week_end, path)

    def _latest_output_dir(self) -> Optional[Path]:
        # TODO: Implement actual logic. Temporary fix to avoid IndentationError.
        return None

    def _suggest_anchor_date(self) -> "QDate":
        """Return the suggested anchor date (Sunday) based on the latest run in outputs/.
        If the latest run ended on date D, the next anchor is the first Sunday >= D + 7.
        Falls back to last Sunday if no history found."""
        today = QDate.currentDate()
        last_sunday = today.addDays(-(today.dayOfWeek() % 7))
        try:
            from metagame_input_generator import scan_run_dirs
            runs = scan_run_dirs(self.repo_root / "outputs")
            if runs:
                _ws, week_end, _path = runs[-1]
                last_end = QDate.fromString(str(week_end), "yyyy-MM-dd")
                if last_end.isValid():
                    # Next Sunday = last_end + (7 - last_end.dayOfWeek() % 7) days
                    days_to_sunday = 7 - (last_end.dayOfWeek() % 7)
                    if days_to_sunday == 7:
                        days_to_sunday = 0
                    candidate = last_end.addDays(days_to_sunday + 7)
                    # Don't suggest a date in the future beyond today+7
                    if candidate <= today.addDays(7):
                        return candidate
        except Exception:
            pass
        return last_sunday
        if self.last_results:
            return self.last_results[-1].run_dir
        return self._find_latest_range_dir(self.repo_root / "outputs")

    def _load_result_rows(self, csv_path: Path) -> List[dict[str, str]]:
        import csv

        rows: List[dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(
                    {
                        "raw_deck": str(row.get("Source Deck Names") or row.get("Raw Deck") or row.get("Deck") or "").strip(),
                        "deck": str(row.get("Deck") or "").strip(),
                        "archetype": str(row.get("Archetype") or "").strip(),
                        "meta": str(row.get("Meta") or "").strip(),
                        "my_wr": str(row.get("My Deck Winrate") or "").strip(),
                    }
                )
        return rows

    def load_latest_output_for_editor(self) -> None:
        """Load the most recently modified run dir — also refreshes the source combo."""
        self._refresh_editor_source_combo()

    def _refresh_editor_source_combo(self) -> None:
        """Scan outputs/ for metagame + challenge CSV sources and populate the source combo."""
        from metagame_input_generator import scan_run_dirs

        outputs_base = self.repo_root / "outputs"
        runs = scan_run_dirs(outputs_base)

        # Fallback: also pick up legacy flat dirs (YYYY-MM-DD_to_YYYY-MM-DD directly in outputs/)
        legacy: list[tuple] = []
        if outputs_base.exists():
            import re as _re
            _flat_re = _re.compile(r"^(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})$")
            from datetime import date as _date
            for d in sorted(outputs_base.iterdir()):
                if not d.is_dir():
                    continue
                m = _flat_re.match(d.name)
                if not m:
                    continue
                try:
                    ws = _date.fromisoformat(m.group(1))
                    we = _date.fromisoformat(m.group(2))
                    legacy.append((ws, we, d))
                except ValueError:
                    continue

        all_runs = sorted(set(runs) | set(legacy), key=lambda x: x[0])

        self.editor_source_combo.blockSignals(True)
        self.editor_source_combo.clear()

        if not all_runs:
            self.editor_source_combo.addItem("(no run directories found)", None)
            self.editor_source_combo.blockSignals(False)
            self.editor_rows = []
            self.current_editor_source = None
            self.editor_source_label.setText("Source: no generated outputs found yet")
            self.editor_table.setRowCount(0)
            self.editor_status_label.setText("Generate a snapshot first.")
            return

        for week_start, week_end, run_dir in all_runs:
            meta_csv = run_dir / "metagame_input.csv"
            if meta_csv.exists():
                label = f"Meta | {run_dir.name}  ({week_start} → {week_end})"
                self.editor_source_combo.addItem(label, str(meta_csv))

            for challenge_csv in sorted(run_dir.glob("challenge_*_decklist.csv")):
                challenge_label = challenge_csv.stem.replace("challenge_", "").replace("_decklist", "")
                label = f"Challenge | {challenge_label} | {run_dir.name}"
                self.editor_source_combo.addItem(label, str(challenge_csv))

        # Select the newest entry
        self.editor_source_combo.setCurrentIndex(self.editor_source_combo.count() - 1)
        self.editor_source_combo.blockSignals(False)
        self._load_editor_from_combo()

    def _on_editor_source_combo_changed(self, _index: int) -> None:
        self._load_editor_from_combo()

    def _load_editor_from_csv(self, csv_path: Path) -> None:
        if hasattr(self, 'editor_search_input'):
            self.editor_search_input.blockSignals(True)
            self.editor_search_input.clear()
            self.editor_search_input.blockSignals(False)
        if hasattr(self, 'editor_match_label'):
            self.editor_match_label.setText("")

        if not csv_path.exists():
            self.editor_rows = []
            self.current_editor_source = None
            self.editor_source_label.setText(f"Source: missing {csv_path.name}")
            self.editor_table.setRowCount(0)
            return

        self.editor_rows = self._load_result_rows(csv_path)
        self.current_editor_source = csv_path
        source_kind = "Challenge" if csv_path.name.startswith("challenge_") else "Metagame"
        self.editor_source_label.setText(f"Source: {source_kind} | {csv_path.parent.name} | {csv_path.name}")
        if source_kind == "Challenge":
            self.save_mapping_button.setText("Save Challenge Mapping")
            self.save_all_mappings_button.setText("Save All Challenge Mappings")
            self.regenerate_grouped_button.setText("Regenerate Outputs Now")
            self.editor_status_label.setText(
                "Challenge source selected. Edits in this panel write to challenge_deck_mapping.csv and backfill Challenge decklists."
            )
        else:
            self.save_mapping_button.setText("Save Mapping")
            self.save_all_mappings_button.setText("Save All Mappings")
            self.regenerate_grouped_button.setText("Regenerate Grouped Now")
            self.editor_status_label.setText(
                "Metagame source selected. Edits in this panel update the main mapping/alias/archetype config."
            )
        if self.review_queue_dialog is not None:
            self.review_queue_dialog.close()
            self.review_queue_dialog = None
        self._populate_editor_table()

    def _load_editor_from_combo(self) -> None:
        csv_path_str = self.editor_source_combo.currentData()
        if not csv_path_str:
            return
        self._load_editor_from_csv(Path(csv_path_str))


    def _populate_editor_table(self) -> None:
        deck_options = self._load_deck_catalog()
        archetype_options = load_archetype_catalog(self.config_dir)
        self.editor_table.setRowCount(len(self.editor_rows))
        for row_index, row in enumerate(self.editor_rows):
            raw_item = QTableWidgetItem(row.get("raw_deck", ""))
            self.editor_table.setItem(row_index, 0, raw_item)

            deck_combo = QComboBox()
            deck_combo.setEditable(True)
            deck_combo.addItems(deck_options)
            deck_combo.setCurrentText(row.get("deck", ""))
            deck_combo.currentTextChanged.connect(lambda text, idx=row_index: self._on_table_deck_changed(idx, text))
            self.editor_table.setCellWidget(row_index, 1, deck_combo)

            archetype_combo = QComboBox()
            archetype_combo.setEditable(True)
            archetype_combo.addItems(archetype_options)
            archetype_combo.setCurrentText(row.get("archetype", ""))
            archetype_combo.currentTextChanged.connect(lambda text, idx=row_index: self._on_table_archetype_changed(idx, text))
            self._apply_unknown_archetype_style(archetype_combo, row.get("archetype", ""))
            self.editor_table.setCellWidget(row_index, 2, archetype_combo)

            meta_item = QTableWidgetItem(self._format_2dp(row.get("meta", "")))
            meta_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.editor_table.setItem(row_index, 3, meta_item)

            wr_item = QTableWidgetItem(self._format_2dp(row.get("my_wr", "")))
            wr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.editor_table.setItem(row_index, 4, wr_item)

        if self.editor_rows:
            self.editor_table.selectRow(0)
        else:
            self.raw_name_value.setText("-")
            self.editor_canonical_input.clear()

    def _handle_editor_row_selected(self) -> None:
        row_index = self.editor_table.currentRow()
        if row_index < 0 or row_index >= len(self.editor_rows):
            return
        row = self.editor_rows[row_index]
        self.raw_name_value.setText(row.get("raw_deck", ""))
        self.editor_canonical_input.setText(row.get("deck", ""))
        self._select_archetype(row.get("archetype", ""))
        self._apply_unknown_archetype_style(self.editor_archetype_combo, row.get("archetype", ""))
        if self._is_current_source_challenge():
            self.editor_status_label.setText(
                "Editing selected Challenge row. Save writes a Challenge-only override and updates Challenge decklist files."
            )
        else:
            self.editor_status_label.setText(
                "Editing selected Metagame row. Save updates user mapping, alias, and exact archetype rule."
            )

    def _on_table_deck_changed(self, row_index: int, value: str) -> None:
        if row_index < 0 or row_index >= len(self.editor_rows):
            return
        if self.editor_table.currentRow() != row_index:
            self.editor_table.selectRow(row_index)
        self.editor_rows[row_index]["deck"] = str(value).strip()
        if self.editor_table.currentRow() == row_index:
            self.editor_canonical_input.setText(self.editor_rows[row_index]["deck"])

    def _on_table_archetype_changed(self, row_index: int, value: str) -> None:
        if row_index < 0 or row_index >= len(self.editor_rows):
            return
        if self.editor_table.currentRow() != row_index:
            self.editor_table.selectRow(row_index)
        archetype_value = str(value).strip()
        self.editor_rows[row_index]["archetype"] = archetype_value
        cell_widget = self.editor_table.cellWidget(row_index, 2)
        if isinstance(cell_widget, QComboBox):
            self._apply_unknown_archetype_style(cell_widget, archetype_value)
        if self.editor_table.currentRow() == row_index:
            self._select_archetype(self.editor_rows[row_index]["archetype"])
            self._apply_unknown_archetype_style(self.editor_archetype_combo, archetype_value)

    def _is_unknown_archetype(self, value: str) -> bool:
        normalized = " ".join(str(value or "").strip().lower().split())
        if normalized in {"", "unknown", "unkown", "n/a", "na", "none", "null", "?", "-"}:
            return True
        return "unknown" in normalized

    def _normalize_review_key(self, value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _is_short_review_name(self, value: str) -> bool:
        text = str(value or "").strip()
        return bool(text) and len(text) < 4

    def _select_editor_row(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.editor_rows):
            return
        self.editor_table.selectRow(row_index)
        self.editor_table.scrollToItem(self.editor_table.item(row_index, 0))
        self.raise_()
        self.activateWindow()

    def _save_direct_mapping_from_review(self, raw_name: str, canonical_name: str, archetype: str) -> None:
        raw = str(raw_name or "").strip()
        deck = str(canonical_name or "").strip()
        arc = str(archetype or "").strip() or "Unknown"
        if not raw or not deck:
            return

        upsert_archetype_catalog(self.config_dir, arc)
        model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
        raw_names = self._split_raw_names(raw)
        if not raw_names:
            raw_names = [raw]
        for source_name in raw_names:
            model.queue_mapping(source_name, deck, arc)
            model.queue_alias(source_name, deck, match_type="exact", priority=1)
            model.queue_archetype_rule(source_name, arc, match_type="exact", priority=1)
        model.queue_archetype_rule(deck, arc, match_type="exact", priority=1)
        if model.has_changes():
            model.apply(create_backup=True)

    def _update_editor_row_from_review_queue(self, row_index: int, raw_name: str, deck: str, archetype: str) -> None:
        deck_text = str(deck).strip()
        archetype_text = str(archetype).strip()

        if row_index < 0 or row_index >= len(self.editor_rows):
            return

        self.editor_rows[row_index]["deck"] = deck_text
        self.editor_rows[row_index]["archetype"] = archetype_text

        deck_widget = self.editor_table.cellWidget(row_index, 1)
        if isinstance(deck_widget, QComboBox) and deck_widget.currentText().strip() != deck_text:
            deck_widget.setCurrentText(deck_text)

        archetype_widget = self.editor_table.cellWidget(row_index, 2)
        if isinstance(archetype_widget, QComboBox) and archetype_widget.currentText().strip() != archetype_text:
            archetype_widget.setCurrentText(archetype_text)
            self._apply_unknown_archetype_style(archetype_widget, archetype_text)

        if self.editor_table.currentRow() == row_index:
            self.editor_canonical_input.setText(deck_text)
            self._select_archetype(archetype_text)
            self._apply_unknown_archetype_style(self.editor_archetype_combo, archetype_text)

    def _is_review_row_already_handled(self, raw_name: str, deck_name: str, archetype: str) -> bool:
        raw = str(raw_name or "").strip()
        deck = str(deck_name or "").strip()
        arc = str(archetype or "").strip()
        if not raw:
            return False
        raw_names = self._split_raw_names(raw)
        if not raw_names:
            raw_names = [raw]
        challenge_only = self._is_current_source_challenge()
        if deck and not self._is_unknown_archetype(arc):
            if challenge_only:
                if self._verify_saved_challenge_mapping_entries(raw_names, deck, arc):
                    return True
            elif self._verify_saved_mapping_entries(raw_names, deck, arc):
                return True

        return self._read_saved_mapping_entry(raw_names, challenge_only) is not None

    def _save_review_queue_changes(self, payload: object) -> None:
        items = payload if isinstance(payload, list) else [payload]
        saved = 0
        challenge_payloads: List[tuple[List[str], str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            source_row = -1
            try:
                source_row = int(item.get("source_row", -1))
            except Exception:
                source_row = -1
            raw_name = str(item.get("raw_deck") or "").strip()
            deck = str(item.get("deck") or "").strip()
            archetype = str(item.get("archetype") or "").strip()
            if not raw_name or not deck or not archetype:
                continue
            raw_names = self._split_raw_names(raw_name)
            if not raw_names:
                raw_names = [raw_name]

            if 0 <= source_row < len(self.editor_rows):
                self.editor_rows[source_row]["deck"] = deck
                self.editor_rows[source_row]["archetype"] = archetype

            if self._is_current_source_challenge():
                challenge_payloads.append((raw_names, deck, archetype))
            else:
                self._save_direct_mapping_from_review(raw_name, deck, archetype)
            saved += 1

        if challenge_payloads:
            self._upsert_challenge_mapping_payloads(challenge_payloads)
            self._propagate_mappings_to_challenge_decklists(challenge_payloads, self._current_editor_week_end())

        if self.review_queue_dialog is not None:
            refreshed = self._build_review_queue_rows(scan_all_runs=self.review_all_runs_check.isChecked())
            self.review_queue_dialog.set_options(self._load_deck_catalog(), load_archetype_catalog(self.config_dir))
            self.review_queue_dialog.set_rows(refreshed)

        self.editor_status_label.setText(f"Review queue save complete. rows={saved}")

    def _collect_review_candidate_rows(self, scan_all_runs: bool) -> List[dict[str, str]]:
        out: List[dict[str, str]] = []
        current_source = self.current_editor_source.resolve() if self.current_editor_source is not None else None

        if not scan_all_runs:
            for idx, row in enumerate(self.editor_rows):
                out.append(
                    {
                        "source_row": str(idx),
                        "raw_deck": str(row.get("raw_deck") or "").strip(),
                        "deck": str(row.get("deck") or "").strip(),
                        "archetype": str(row.get("archetype") or "").strip(),
                        "meta": str(row.get("meta") or "").strip(),
                        "my_wr": str(row.get("my_wr") or "").strip(),
                    }
                )
            return out

        is_challenge = self._is_current_source_challenge()
        seen: set[tuple[str, str, str]] = set()
        for _week_end, run_dir in self._iter_run_dirs_with_week_end():
            source_paths: List[Path] = []
            if is_challenge:
                source_paths = sorted(run_dir.glob("challenge_*_decklist.csv"))
            else:
                meta_csv = run_dir / "metagame_input.csv"
                if meta_csv.exists():
                    source_paths = [meta_csv]

            for source_path in source_paths:
                try:
                    rows = self._load_result_rows(source_path)
                except Exception:
                    continue
                source_resolved = source_path.resolve()
                for idx, row in enumerate(rows):
                    raw_name = str(row.get("raw_deck") or "").strip()
                    deck_name = str(row.get("deck") or "").strip()
                    archetype = str(row.get("archetype") or "").strip()
                    key = (
                        self._normalize_review_key(raw_name),
                        self._normalize_review_key(deck_name),
                        self._normalize_review_key(archetype),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        {
                            "source_row": str(idx) if current_source is not None and source_resolved == current_source else "-1",
                            "raw_deck": raw_name,
                            "deck": deck_name,
                            "archetype": archetype,
                            "meta": str(row.get("meta") or "").strip(),
                            "my_wr": str(row.get("my_wr") or "").strip(),
                        }
                    )
        return out

    def _collect_prior_seen_names(self) -> tuple[set[str], set[str]]:
        seen_raw: set[str] = set()
        seen_deck: set[str] = set()
        current_is_challenge = self._is_current_source_challenge()
        current_week_end = self._current_editor_week_end()

        for week_end, run_dir in self._iter_run_dirs_with_week_end():
            if current_week_end is not None and week_end is not None and week_end >= current_week_end:
                continue

            source_paths: list[Path] = []
            if current_is_challenge:
                source_paths = sorted(run_dir.glob("challenge_*_decklist.csv"))
            else:
                meta_csv = run_dir / "metagame_input.csv"
                if meta_csv.exists():
                    source_paths = [meta_csv]

            for path in source_paths:
                try:
                    rows = self._load_result_rows(path)
                except Exception:
                    continue
                for row in rows:
                    raw_key = self._normalize_review_key(str(row.get("raw_deck") or ""))
                    deck_key = self._normalize_review_key(str(row.get("deck") or ""))
                    if raw_key:
                        seen_raw.add(raw_key)
                    if deck_key:
                        seen_deck.add(deck_key)

        return seen_raw, seen_deck

    def _build_review_queue_rows(self, scan_all_runs: bool) -> List[dict[str, str]]:
        candidate_rows = self._collect_review_candidate_rows(scan_all_runs=scan_all_runs)
        if not candidate_rows:
            return []

        archetype_catalog = {self._normalize_review_key(value) for value in load_archetype_catalog(self.config_dir)}
        seen_raw, seen_deck = self._collect_prior_seen_names()
        review_rows: List[dict[str, str]] = []

        for row in candidate_rows:
            raw_name = str(row.get("raw_deck") or "").strip()
            deck_name = str(row.get("deck") or "").strip()
            archetype = str(row.get("archetype") or "").strip()
            reasons: List[str] = []

            if self._is_short_review_name(raw_name):
                reasons.append("Short Raw Name")
            if self._is_short_review_name(deck_name):
                reasons.append("Short Deck Name")
            if self._is_unknown_archetype(archetype):
                reasons.append("Unknown Archetype")
            elif archetype and self._normalize_review_key(archetype) not in archetype_catalog:
                reasons.append("Non-catalog Archetype")

            raw_key = self._normalize_review_key(raw_name)
            deck_key = self._normalize_review_key(deck_name)
            if (not scan_all_runs) and raw_key and raw_key not in seen_raw:
                reasons.append("First Seen Raw")
            if (not scan_all_runs) and deck_key and deck_key not in seen_deck:
                reasons.append("First Seen Deck")

            if not reasons:
                continue

            if self._is_review_row_already_handled(raw_name, deck_name, archetype):
                continue

            review_rows.append(
                {
                    "source_row": str(row.get("source_row") or "-1"),
                    "reason": ", ".join(reasons),
                    "raw_deck": raw_name,
                    "deck": deck_name,
                    "archetype": archetype,
                    "meta": self._format_2dp(row.get("meta", "")),
                    "my_wr": self._format_2dp(row.get("my_wr", "")),
                }
            )

        return review_rows

    def _open_review_queue(self) -> None:
        review_rows = self._build_review_queue_rows(scan_all_runs=self.review_all_runs_check.isChecked())
        if not review_rows:
            QMessageBox.information(
                self,
                APP_NAME,
                "No rows matched the review queue filters for the current source.",
            )
            return

        if self.review_queue_dialog is None:
            self.review_queue_dialog = ReviewQueueDialog(
                review_rows,
                deck_options=self._load_deck_catalog(),
                archetype_options=load_archetype_catalog(self.config_dir),
                parent=self,
            )
            self.review_queue_dialog.rowActivated.connect(self._select_editor_row)
            self.review_queue_dialog.rowUpdated.connect(self._update_editor_row_from_review_queue)
            self.review_queue_dialog.saveRequested.connect(self._save_review_queue_changes)
        else:
            self.review_queue_dialog.set_options(self._load_deck_catalog(), load_archetype_catalog(self.config_dir))
            self.review_queue_dialog.set_rows(review_rows)

        self.review_queue_dialog.show()
        self.review_queue_dialog.raise_()
        self.review_queue_dialog.activateWindow()

    def _apply_unknown_archetype_style(self, combo: QComboBox, value: str) -> None:
        if self._is_unknown_archetype(value):
            combo.setStyleSheet("QComboBox { background-color: #fff6bf; }")
        else:
            combo.setStyleSheet("")

    def _on_editor_archetype_text_changed(self, text: str) -> None:
        self._apply_unknown_archetype_style(self.editor_archetype_combo, text)
        row_index = self.editor_table.currentRow()
        if row_index < 0 or row_index >= len(self.editor_rows):
            return
        archetype_value = str(text).strip()
        self.editor_rows[row_index]["archetype"] = archetype_value
        cell_widget = self.editor_table.cellWidget(row_index, 2)
        if isinstance(cell_widget, QComboBox) and cell_widget.currentText().strip() != archetype_value:
            cell_widget.setCurrentText(archetype_value)

    def _format_2dp(self, value: str) -> str:
        try:
            return f"{float(value):.2f}"
        except Exception:
            return str(value)

    def _read_column_values(self, path: Path, column: str) -> List[str]:
        if not path.exists():
            return []
        out: List[str] = []
        seen = set()
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                value = str(row.get(column) or "").strip()
                key = value.lower()
                if not value or key in seen:
                    continue
                seen.add(key)
                out.append(value)
        return out

    def _load_deck_catalog(self) -> List[str]:
        values = []
        values.extend(self._read_column_values(self.config_dir / "deck_aliases.csv", "canonical_name"))
        values.extend(self._read_column_values(self.config_dir / "user_deck_mapping.csv", "canonical_name"))
        for row in self.editor_rows:
            current = str(row.get("deck") or "").strip()
            if current:
                values.append(current)

        deduped: List[str] = []
        seen = set()
        for value in sorted(values, key=lambda s: s.lower()):
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(value)
        self._deck_catalog_cache = deduped
        return deduped

    def _challenge_mapping_csv_path(self) -> Path:
        return self.config_dir / "challenge_deck_mapping.csv"

    def _load_challenge_mapping_table(self) -> None:
        path = self._challenge_mapping_csv_path()
        rows: list[dict[str, str]] = []
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    rows.append(
                        {
                            "raw_name": str(row.get("raw_name") or "").strip(),
                            "canonical_name": str(row.get("canonical_name") or "").strip(),
                            "archetype": str(row.get("archetype") or "").strip(),
                        }
                    )

        if rows:
            self.editor_status_label.setText(f"Loaded saved Challenge mapping rows: {len(rows)}")
        else:
            self.editor_status_label.setText(
                "No saved Challenge mapping rows found in challenge_deck_mapping.csv."
            )

    def _seed_challenge_mapping_from_current_source(self) -> None:
        if not self._is_current_source_challenge():
            QMessageBox.information(
                self,
                APP_NAME,
                "Select a Challenge source in the Editor source list first, then use 'Seed From Source'.",
            )
            return

        if not self.editor_rows:
            QMessageBox.information(self, APP_NAME, "Current Challenge source has no rows to seed.")
            return

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in self.editor_rows:
            raw_name = str(item.get("raw_deck") or "").strip()
            canonical_name = str(item.get("deck") or "").strip()
            archetype = str(item.get("archetype") or "").strip() or "Unknown"
            key = raw_name.lower()
            if not raw_name or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "raw_name": raw_name,
                    "canonical_name": canonical_name,
                    "archetype": archetype,
                }
            )

        self.challenge_mapping_table.setRowCount(0)
        for item in rows:
            row = self.challenge_mapping_table.rowCount()
            self.challenge_mapping_table.insertRow(row)
            self.challenge_mapping_table.setItem(row, 0, QTableWidgetItem(item["raw_name"]))
            self.challenge_mapping_table.setItem(row, 1, QTableWidgetItem(item["canonical_name"]))
            self.challenge_mapping_table.setItem(row, 2, QTableWidgetItem(item["archetype"]))

        self.editor_status_label.setText(
            f"Seeded Challenge mapping table from current source: {len(rows)} unique rows"
        )

    def _add_challenge_mapping_row(self) -> None:
        row = self.challenge_mapping_table.rowCount()
        self.challenge_mapping_table.insertRow(row)
        self.challenge_mapping_table.setItem(row, 0, QTableWidgetItem(""))
        self.challenge_mapping_table.setItem(row, 1, QTableWidgetItem(""))
        self.challenge_mapping_table.setItem(row, 2, QTableWidgetItem("Unknown"))

    def _remove_challenge_mapping_row(self) -> None:
        row = self.challenge_mapping_table.currentRow()
        if row >= 0:
            self.challenge_mapping_table.removeRow(row)

    def _save_challenge_mapping_table(self) -> None:
        path = self._challenge_mapping_csv_path()
        data_rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for r in range(self.challenge_mapping_table.rowCount()):
            raw_item = self.challenge_mapping_table.item(r, 0)
            can_item = self.challenge_mapping_table.item(r, 1)
            arc_item = self.challenge_mapping_table.item(r, 2)

            raw_name = str(raw_item.text() if raw_item else "").strip()
            canonical_name = str(can_item.text() if can_item else "").strip()
            archetype = str(arc_item.text() if arc_item else "").strip() or "Unknown"
            if not raw_name or not canonical_name:
                continue
            key = raw_name.lower()
            if key in seen:
                continue
            seen.add(key)
            data_rows.append(
                {
                    "raw_name": raw_name,
                    "canonical_name": canonical_name,
                    "archetype": archetype,
                }
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["raw_name", "canonical_name", "archetype"])
            writer.writeheader()
            writer.writerows(data_rows)

        self.editor_status_label.setText(f"Challenge mapping saved: {path.name} ({len(data_rows)} rows)")
        self._append_log(f"[OK] Saved challenge mapping rows={len(data_rows)} -> {path}")
        self._load_challenge_mapping_table()

    def _refresh_archetype_combo(self) -> None:
        catalog = load_archetype_catalog(self.config_dir)
        current_text = self.editor_archetype_combo.currentText().strip()
        self.editor_archetype_combo.blockSignals(True)
        self.editor_archetype_combo.clear()
        self.editor_archetype_combo.addItems(catalog)
        self.editor_archetype_combo.blockSignals(False)
        self._select_archetype(current_text)

    def _select_archetype(self, archetype: str) -> None:
        target = str(archetype or "").strip()
        if not target:
            self.editor_archetype_combo.setCurrentIndex(-1)
            self.editor_archetype_combo.setEditText("")
            return
        match_index = self.editor_archetype_combo.findText(target, Qt.MatchFixedString)
        if match_index >= 0:
            self.editor_archetype_combo.setCurrentIndex(match_index)
        else:
            self.editor_archetype_combo.setEditText(target)

    def _add_custom_archetype(self) -> None:
        archetype = self.custom_archetype_input.text().strip()
        if not archetype:
            return
        catalog = upsert_archetype_catalog(self.config_dir, archetype)
        self.editor_archetype_combo.clear()
        self.editor_archetype_combo.addItems(catalog)
        self._select_archetype(archetype)
        self.custom_archetype_input.clear()
        self.editor_status_label.setText(f"Custom archetype '{archetype}' added to catalog.")

    def _delete_selected_archetype(self) -> None:
        archetype = self.editor_archetype_combo.currentText().strip()
        if not archetype:
            QMessageBox.warning(self, APP_NAME, "Select archetype to delete first.")
            return
        if self._is_unknown_archetype(archetype):
            QMessageBox.information(self, APP_NAME, "Unknown cannot be deleted.")
            return

        confirm = QMessageBox.question(
            self,
            APP_NAME,
            (
                f"Delete archetype '{archetype}' from catalog and replace existing references in mappings/rules with 'Unknown'?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        summary = remove_archetype(self.config_dir, archetype, replacement="Unknown", create_backup=True)
        self._refresh_archetype_combo()
        self._select_archetype("Unknown")

        for row in self.editor_rows:
            if str(row.get("archetype") or "").strip().lower() == archetype.lower():
                row["archetype"] = "Unknown"
        self._populate_editor_table()

        self.editor_status_label.setText(
            (
                f"Deleted archetype '{archetype}'. "
                f"rules updated={summary.rules_updated}, mappings updated={summary.mappings_updated}"
            )
        )

    def _split_raw_names(self, raw_value: str) -> List[str]:
        parts = [part.strip() for part in str(raw_value or "").split("|")]
        out: List[str] = []
        seen = set()
        for part in parts:
            key = part.lower()
            if not part or key in seen:
                continue
            seen.add(key)
            out.append(part)
        return out

    def _read_saved_mapping_entry(self, raw_names: List[str], challenge_only: bool) -> Optional[tuple[str, str]]:
        mapping_path = self._challenge_mapping_csv_path() if challenge_only else self.config_dir / "user_deck_mapping.csv"
        if not mapping_path.exists():
            return None

        expected = {name.strip().lower() for name in raw_names if name.strip()}
        if not expected:
            return None

        matched: dict[str, tuple[str, str]] = {}
        with mapping_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw = str(row.get("raw_name") or "").strip().lower()
                if raw not in expected:
                    continue
                canonical_name = str(row.get("canonical_name") or "").strip()
                archetype = str(row.get("archetype") or "").strip()
                if not canonical_name or self._is_unknown_archetype(archetype):
                    continue
                matched[raw] = (canonical_name, archetype)

        if set(matched) != expected:
            return None

        resolved = {value for value in matched.values()}
        if len(resolved) != 1:
            return None
        return next(iter(resolved))

    def _verify_saved_mapping_entries(self, raw_names: List[str], canonical_name: str, archetype: str) -> bool:
        mapping_path = self.config_dir / "user_deck_mapping.csv"
        if not mapping_path.exists():
            return False

        expected = {name.strip().lower() for name in raw_names if name.strip()}
        if not expected:
            return False

        matched = set()
        with mapping_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw = str(row.get("raw_name") or "").strip().lower()
                if raw not in expected:
                    continue
                saved_canonical = str(row.get("canonical_name") or "").strip()
                saved_archetype = str(row.get("archetype") or "").strip()
                if saved_canonical == canonical_name and saved_archetype == archetype:
                    matched.add(raw)

        return matched == expected

    def _verify_saved_challenge_mapping_entries(self, raw_names: List[str], canonical_name: str, archetype: str) -> bool:
        mapping_path = self._challenge_mapping_csv_path()
        if not mapping_path.exists():
            return False

        expected = {name.strip().lower() for name in raw_names if name.strip()}
        if not expected:
            return False

        matched = set()
        with mapping_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw = str(row.get("raw_name") or "").strip().lower()
                if raw not in expected:
                    continue
                saved_canonical = str(row.get("canonical_name") or "").strip()
                saved_archetype = str(row.get("archetype") or "").strip()
                if saved_canonical == canonical_name and saved_archetype == archetype:
                    matched.add(raw)

        return matched == expected

    def _current_editor_week_end(self) -> Optional[date]:
        if self.current_editor_source is None:
            return None
        import re as _re

        run_dir_name = self.current_editor_source.parent.name
        match = _re.search(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})", run_dir_name)
        if not match:
            return None
        try:
            return date.fromisoformat(match.group(2))
        except ValueError:
            return None

    def _latest_available_week_end(self) -> Optional[date]:
        from metagame_input_generator import scan_run_dirs

        outputs_base = self.repo_root / "outputs"
        runs = scan_run_dirs(outputs_base)
        if not runs:
            return None
        return max(week_end for _week_start, week_end, _run_dir in runs)

    def _is_editing_latest_run(self) -> bool:
        current_end = self._current_editor_week_end()
        latest_end = self._latest_available_week_end()
        if current_end is None or latest_end is None:
            return True
        return current_end >= latest_end

    def _read_existing_exact_index(self, path: Path, key_column: str, value_column: str) -> dict[str, str]:
        if not path.exists():
            return {}
        out: dict[str, str] = {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                key = str(row.get(key_column) or "").strip().lower()
                if not key:
                    continue
                match_type = str(row.get("match_type") or "exact").strip().lower()
                if "match_type" in (reader.fieldnames or []) and match_type != "exact":
                    continue
                out[key] = str(row.get(value_column) or "").strip()
        return out

    def _collect_mapping_payloads(self, row_indices: List[int]) -> List[tuple[List[str], str, str]]:
        payloads: List[tuple[List[str], str, str]] = []
        for row_index in row_indices:
            if row_index < 0 or row_index >= len(self.editor_rows):
                continue
            row = self.editor_rows[row_index]
            canonical_name = str(row.get("deck") or "").strip()
            archetype = str(row.get("archetype") or "").strip()
            raw_name = str(row.get("raw_deck") or "").strip()
            if not canonical_name or not archetype or not raw_name:
                continue
            raw_names = self._split_raw_names(raw_name)
            # Safety: grouped rows (multiple source names joined by '|') are
            # ambiguous and must not be propagated as one mapping payload.
            if len(raw_names) != 1:
                continue
            payloads.append((raw_names, canonical_name, archetype))
        return payloads

    def _is_current_source_challenge(self) -> bool:
        if self.current_editor_source is None:
            return False
        return self.current_editor_source.name.startswith("challenge_")

    def _iter_run_dirs_with_week_end(self) -> List[tuple[Optional[date], Path]]:
        from metagame_input_generator import scan_run_dirs

        outputs_base = self.repo_root / "outputs"
        runs = scan_run_dirs(outputs_base)
        out: List[tuple[Optional[date], Path]] = [(week_end, run_dir) for _week_start, week_end, run_dir in runs]
        known_paths = {run_dir.resolve() for _week_end, run_dir in out}

        if outputs_base.exists():
            import re as _re

            flat_re = _re.compile(r"^(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})$")
            for candidate in outputs_base.iterdir():
                if not candidate.is_dir():
                    continue
                try:
                    candidate_resolved = candidate.resolve()
                except Exception:
                    candidate_resolved = candidate
                if candidate_resolved in known_paths:
                    continue
                match = flat_re.match(candidate.name)
                if not match:
                    continue
                try:
                    week_end = date.fromisoformat(match.group(2))
                except ValueError:
                    week_end = None
                out.append((week_end, candidate))

        out.sort(key=lambda item: (item[0] is None, item[0] or date.min, item[1].name))
        return out

    def _propagate_mappings_to_history(self, payloads: List[tuple[List[str], str, str]], limit_week_end: Optional[date]) -> tuple[int, int]:
        if not payloads:
            return 0, 0

        exact_map: dict[str, tuple[str, str]] = {}
        for raw_names, canonical_name, archetype in payloads:
            for raw_name in raw_names:
                key = raw_name.strip().lower()
                if key:
                    exact_map[key] = (canonical_name, archetype)

        if not exact_map:
            return 0, 0

        files_updated = 0
        rows_updated = 0
        for week_end, run_dir in self._iter_run_dirs_with_week_end():
            if limit_week_end is not None and week_end is not None and week_end > limit_week_end:
                continue

            csv_path = run_dir / "metagame_input.csv"
            if not csv_path.exists():
                continue

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])

            if not rows or not fieldnames:
                continue

            source_col = "Source Deck Names" if "Source Deck Names" in fieldnames else ("Raw Deck" if "Raw Deck" in fieldnames else "Deck")
            if "Deck" not in fieldnames:
                fieldnames.append("Deck")
            if "Archetype" not in fieldnames:
                fieldnames.append("Archetype")

            file_changed = False
            for row in rows:
                source_value = str(row.get(source_col) or "").strip()
                raw_candidates = self._split_raw_names(source_value)
                target: Optional[tuple[str, str]] = None
                for candidate in raw_candidates:
                    mapped = exact_map.get(candidate.strip().lower())
                    if mapped is not None:
                        target = mapped
                        break
                if target is None:
                    continue

                target_deck, target_archetype = target
                current_deck = str(row.get("Deck") or "").strip()
                current_archetype = str(row.get("Archetype") or "").strip()
                if current_deck == target_deck and current_archetype == target_archetype:
                    continue

                row["Deck"] = target_deck
                row["Archetype"] = target_archetype
                rows_updated += 1
                file_changed = True

            if not file_changed:
                continue

            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            files_updated += 1

        # metagame_input.csv is the raw per-deck source; the *grouped* XLSX files are a
        # separate materialized aggregate that run_statistics() actually reads for charts,
        # so an archetype edit has to be patched there too or it never shows up on charts.
        grouped_files, grouped_rows = self._propagate_mappings_to_grouped_xlsx(payloads, limit_week_end)
        files_updated += grouped_files
        rows_updated += grouped_rows

        return files_updated, rows_updated

    def _propagate_mappings_to_grouped_xlsx(
        self, payloads: List[tuple[List[str], str, str]], limit_week_end: Optional[date]
    ) -> tuple[int, int]:
        """Patch Deck/Archetype in already-generated grouped XLSX files (matched via
        their 'Source Deck Names' column) so archetype/deck edits appear immediately
        in stats charts, without waiting for a full re-generate."""
        if not payloads:
            return 0, 0

        exact_map: dict[str, tuple[str, str]] = {}
        for raw_names, canonical_name, archetype in payloads:
            for raw_name in raw_names:
                key = raw_name.strip().lower()
                if key:
                    exact_map[key] = (canonical_name, archetype)
        if not exact_map:
            return 0, 0

        files_updated = 0
        rows_updated = 0
        for week_end, run_dir in self._iter_run_dirs_with_week_end():
            if limit_week_end is not None and week_end is not None and week_end > limit_week_end:
                continue

            for xlsx_name in ("metagame_input_grouped.xlsx", "metagame_input_rogue_grouped.xlsx"):
                xlsx_path = run_dir / xlsx_name
                if not xlsx_path.exists():
                    continue
                try:
                    df = pd.read_excel(xlsx_path)
                except Exception:
                    continue
                if "Archetype" not in df.columns or "Deck" not in df.columns:
                    continue
                source_col = "Source Deck Names" if "Source Deck Names" in df.columns else "Deck"

                file_changed = False
                for idx in df.index:
                    raw_candidates = self._split_raw_names(str(df.at[idx, source_col] or ""))
                    target: Optional[tuple[str, str]] = None
                    for candidate in raw_candidates:
                        mapped = exact_map.get(candidate.strip().lower())
                        if mapped is not None:
                            target = mapped
                            break
                    if target is None:
                        continue

                    target_deck, target_archetype = target
                    if (
                        str(df.at[idx, "Deck"] or "").strip() == target_deck
                        and str(df.at[idx, "Archetype"] or "").strip() == target_archetype
                    ):
                        continue

                    df.at[idx, "Deck"] = target_deck
                    df.at[idx, "Archetype"] = target_archetype
                    rows_updated += 1
                    file_changed = True

                if file_changed:
                    try:
                        df.to_excel(xlsx_path, index=False, engine="openpyxl")
                        files_updated += 1
                    except Exception as err:
                        self._append_log(f"[WARN] Could not patch {xlsx_path.name} in place: {err}")

        return files_updated, rows_updated

    def _propagate_mappings_to_challenge_decklists(
        self, payloads: List[tuple[List[str], str, str]], limit_week_end: Optional[date]
    ) -> tuple[int, int]:
        if not payloads:
            return 0, 0

        exact_map: dict[str, tuple[str, str]] = {}
        for raw_names, canonical_name, archetype in payloads:
            for raw_name in raw_names:
                key = raw_name.strip().lower()
                if key:
                    exact_map[key] = (canonical_name, archetype)

        if not exact_map:
            return 0, 0

        files_updated = 0
        rows_updated = 0
        for week_end, run_dir in self._iter_run_dirs_with_week_end():
            if limit_week_end is not None and week_end is not None and week_end > limit_week_end:
                continue

            for csv_path in sorted(run_dir.glob("challenge_*_decklist.csv")):
                with csv_path.open("r", encoding="utf-8", newline="") as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
                    fieldnames = list(reader.fieldnames or [])

                if not rows or not fieldnames:
                    continue
                if "Deck" not in fieldnames:
                    fieldnames.append("Deck")
                if "Archetype" not in fieldnames:
                    fieldnames.append("Archetype")

                file_changed = False
                for row in rows:
                    raw_value = str(row.get("Deck") or "").strip()
                    mapped = exact_map.get(raw_value.lower())
                    if mapped is None:
                        continue
                    target_deck, target_archetype = mapped
                    if str(row.get("Deck") or "").strip() == target_deck and str(row.get("Archetype") or "").strip() == target_archetype:
                        continue
                    row["Deck"] = target_deck
                    row["Archetype"] = target_archetype
                    rows_updated += 1
                    file_changed = True

                if not file_changed:
                    continue

                with csv_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                files_updated += 1

        return files_updated, rows_updated

    def _save_rows_to_config(self, row_indices: List[int]) -> tuple[int, int, bool, Optional[object], int]:
        model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
        verification_items: List[tuple[List[str], str, str]] = []
        processed_rows = 0
        total_source_names = 0
        skipped_existing = 0

        editing_latest = self._is_editing_latest_run()
        existing_mappings = self._read_existing_exact_index(self.config_dir / "user_deck_mapping.csv", "raw_name", "canonical_name") if not editing_latest else {}
        existing_aliases = self._read_existing_exact_index(self.config_dir / "deck_aliases.csv", "pattern", "canonical_name") if not editing_latest else {}
        existing_archetypes = self._read_existing_exact_index(self.config_dir / "archetype_rules.csv", "pattern", "archetype") if not editing_latest else {}

        for row_index in row_indices:
            if row_index < 0 or row_index >= len(self.editor_rows):
                continue

            row = self.editor_rows[row_index]
            raw_name = str(row.get("raw_deck") or "").strip()
            canonical_name = str(row.get("deck") or "").strip()
            archetype = str(row.get("archetype") or "").strip()
            if not raw_name or not canonical_name or not archetype:
                continue

            upsert_archetype_catalog(self.config_dir, archetype)
            raw_names = self._split_raw_names(raw_name)
            if not raw_names:
                continue

            # Safety: do not write config rules from grouped rows like
            # "A | B | C" because that can corrupt multiple unrelated decks.
            if len(raw_names) != 1:
                skipped_existing += len(raw_names)
                self._append_log(
                    f"[WARN] Skipped ambiguous row (multiple source names): raw='{raw_name}'"
                )
                continue

            for source_name in raw_names:
                key = source_name.strip().lower()
                if not editing_latest and (
                    key in existing_mappings or key in existing_aliases or key in existing_archetypes
                ):
                    skipped_existing += 1
                    continue
                model.queue_mapping(source_name, canonical_name, archetype)
                model.queue_alias(source_name, canonical_name, match_type="exact", priority=1)
                model.queue_archetype_rule(source_name, archetype, match_type="exact", priority=1)

            canonical_key = canonical_name.strip().lower()
            if editing_latest or canonical_key not in existing_archetypes:
                model.queue_archetype_rule(canonical_name, archetype, match_type="exact", priority=1)
            elif not editing_latest:
                skipped_existing += 1

            processed_rows += 1
            total_source_names += len(raw_names)
            verification_items.append((raw_names, canonical_name, archetype))

        if not model.has_changes():
            return 0, 0, False, None, skipped_existing

        summary = model.apply(create_backup=True)
        verified = all(self._verify_saved_mapping_entries(raw_names, canonical_name, archetype) for raw_names, canonical_name, archetype in verification_items)
        return processed_rows, total_source_names, verified, summary, skipped_existing

    def _upsert_challenge_mapping_payloads(self, payloads: List[tuple[List[str], str, str]]) -> int:
        path = self._challenge_mapping_csv_path()
        existing: dict[str, dict[str, str]] = {}
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    raw = str(row.get("raw_name") or "").strip()
                    if raw:
                        existing[raw.lower()] = {
                            "raw_name": raw,
                            "canonical_name": str(row.get("canonical_name") or "").strip(),
                            "archetype": str(row.get("archetype") or "").strip() or "Unknown",
                        }

        upserts = 0
        for raw_names, canonical_name, archetype in payloads:
            for raw_name in raw_names:
                key = raw_name.strip().lower()
                if not key:
                    continue
                existing[key] = {
                    "raw_name": raw_name.strip(),
                    "canonical_name": canonical_name,
                    "archetype": archetype or "Unknown",
                }
                upserts += 1

        rows = [existing[key] for key in sorted(existing.keys())]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["raw_name", "canonical_name", "archetype"])
            writer.writeheader()
            writer.writerows(rows)
        return upserts

    def _save_editor_changes(self) -> None:
        row_index = self.editor_table.currentRow()
        if row_index < 0 or row_index >= len(self.editor_rows):
            QMessageBox.warning(self, APP_NAME, "Select a row in the editor table first.")
            return

        row = self.editor_rows[row_index]
        raw_name = str(row.get("raw_deck") or self.raw_name_value.text()).strip()
        canonical_name = self.editor_canonical_input.text().strip() or str(row.get("deck") or "").strip()
        archetype = self.editor_archetype_combo.currentText().strip() or str(row.get("archetype") or "").strip()
        if not raw_name or not canonical_name or not archetype:
            QMessageBox.warning(self, APP_NAME, "Raw name, canonical deck name, and archetype are required.")
            return

        row["deck"] = canonical_name
        row["archetype"] = archetype

        if self._is_current_source_challenge():
            payloads = self._collect_mapping_payloads([row_index])
            upserts = self._upsert_challenge_mapping_payloads(payloads)
            processed_rows, total_source_names, _verified, summary, _skipped = self._save_rows_to_config([row_index])
            backfill_files, backfill_rows = self._propagate_mappings_to_challenge_decklists(payloads, self._current_editor_week_end())
            self._refresh_archetype_combo()
            self._select_archetype(archetype)
            self._populate_editor_table()
            self.editor_table.selectRow(row_index)
            shared_note = (
                f" shared_mappings={summary.mappings_upserted}, aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}"
                if summary is not None
                else ""
            )
            self.editor_status_label.setText(
                f"Saved Challenge mapping. rows={upserts}, source_names={total_source_names}, challenge_files={backfill_files}, challenge_rows={backfill_rows}{shared_note}"
            )
            self._append_log(
                f"[OK] Saved Challenge mapping raw='{raw_name}' -> deck='{canonical_name}', archetype='{archetype}', shared_rows={processed_rows}"
            )
            return

        processed_rows, total_source_names, verified, summary, skipped_existing = self._save_rows_to_config([row_index])
        payloads = self._collect_mapping_payloads([row_index])
        backfill_files, backfill_rows = self._propagate_mappings_to_history(payloads, self._current_editor_week_end())

        if summary is None and backfill_rows == 0:
            QMessageBox.warning(self, APP_NAME, "Nothing to save for selected row.")
            return

        self._refresh_archetype_combo()
        self._select_archetype(archetype)
        self._populate_editor_table()
        self.editor_table.selectRow(row_index)
        verification_text = "Saved and verified in active config." if verified else "Saved, but verification failed."
        skip_note = f" skipped={skipped_existing} (protected newer entries)." if skipped_existing else ""
        summary_note = (
            f" aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}, mappings={summary.mappings_upserted}"
            if summary is not None
            else " aliases=0, archetypes=0, mappings=0"
        )
        backfill_note = f" history_files={backfill_files}, history_rows={backfill_rows}"
        self.editor_status_label.setText(
            f"{verification_text} rows={processed_rows}, source names={total_source_names},{summary_note},{backfill_note}{skip_note}"
        )
        self._append_log(
            f"[OK] Saved editor mapping raw='{raw_name}' ({total_source_names} source name(s)) -> deck='{canonical_name}', archetype='{archetype}'"
        )

    def _save_all_editor_changes(self) -> None:
        if not self.editor_rows:
            QMessageBox.warning(self, APP_NAME, "No editor rows to save.")
            return

        # Synchronize current form edits back to selected row before batch save.
        row_index = self.editor_table.currentRow()
        if 0 <= row_index < len(self.editor_rows):
            selected = self.editor_rows[row_index]
            selected["deck"] = self.editor_canonical_input.text().strip() or str(selected.get("deck") or "").strip()
            selected["archetype"] = self.editor_archetype_combo.currentText().strip() or str(selected.get("archetype") or "").strip()

        all_row_indices = list(range(len(self.editor_rows)))

        if self._is_current_source_challenge():
            payloads = self._collect_mapping_payloads(all_row_indices)
            upserts = self._upsert_challenge_mapping_payloads(payloads)
            processed_rows, total_source_names, _verified, summary, _skipped = self._save_rows_to_config(all_row_indices)
            backfill_files, backfill_rows = self._propagate_mappings_to_challenge_decklists(payloads, self._current_editor_week_end())
            self._refresh_archetype_combo()
            self._populate_editor_table()
            if 0 <= row_index < len(self.editor_rows):
                self.editor_table.selectRow(row_index)
            shared_note = (
                f" shared_mappings={summary.mappings_upserted}, aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}"
                if summary is not None
                else ""
            )
            self.editor_status_label.setText(
                f"Saved ALL Challenge mappings. rows={upserts}, source_names={total_source_names}, challenge_files={backfill_files}, challenge_rows={backfill_rows}{shared_note}"
            )
            self._append_log(
                f"[OK] Saved ALL Challenge editor mappings: rows={upserts}, shared_rows={processed_rows}, challenge_files={backfill_files}, challenge_rows={backfill_rows}"
            )
            return

        processed_rows, total_source_names, verified, summary, skipped_existing = self._save_rows_to_config(all_row_indices)
        payloads = self._collect_mapping_payloads(all_row_indices)
        backfill_files, backfill_rows = self._propagate_mappings_to_history(payloads, self._current_editor_week_end())

        if summary is None and backfill_rows == 0:
            QMessageBox.warning(self, APP_NAME, "Nothing to save across editor rows.")
            return

        self._refresh_archetype_combo()
        self._populate_editor_table()
        if 0 <= row_index < len(self.editor_rows):
            self.editor_table.selectRow(row_index)
        verification_text = "Saved and verified in active config." if verified else "Saved, but verification failed."
        skip_note = f" skipped={skipped_existing} (protected newer entries)." if skipped_existing else ""
        summary_note = (
            f" aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}, mappings={summary.mappings_upserted}"
            if summary is not None
            else " aliases=0, archetypes=0, mappings=0"
        )
        backfill_note = f" history_files={backfill_files}, history_rows={backfill_rows}"
        self.editor_status_label.setText(
            f"{verification_text} rows={processed_rows}, source names={total_source_names},{summary_note},{backfill_note}{skip_note}"
        )
        self._append_log(
            f"[OK] Saved ALL editor mappings: rows={processed_rows}, source names={total_source_names}, aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}, mappings={summary.mappings_upserted}"
        )

    def _regenerate_from_editor(self) -> None:
        # Important safety rule:
        # Regenerate must NOT auto-save editor rows into mapping CSVs.
        # Users should explicitly use Save Mapping / Save All Mappings first.
        # This avoids accidental corruption when editing historical grouped rows.
        self.editor_status_label.setText(
            "Regenerating using currently saved mappings only (no auto-save from editor rows)."
        )

        if self.current_editor_source is not None:
            # Extract dates from run dir name — supports both:
            #   WNN_YYYY-MM-DD_to_YYYY-MM-DD  (new structure)
            #   YYYY-MM-DD_to_YYYY-MM-DD      (legacy flat)
            import re as _re
            range_dir = self.current_editor_source.parent.name
            m = _re.search(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})", range_dir)
            if m:
                try:
                    start_qdate = QDate.fromString(m.group(1), "yyyy-MM-dd")
                    end_qdate = QDate.fromString(m.group(2), "yyyy-MM-dd")
                    if start_qdate.isValid() and end_qdate.isValid():
                        self.history_points_spin.setValue(1)
                        self.anchor_date_edit.setDate(end_qdate)
                except Exception:
                    pass

        if self.week_end_edit.date() < self.week_start_edit.date():
            QMessageBox.warning(self, APP_NAME, "Week end must be greater than or equal to week start.")
            return
        self.editor_status_label.setText("Regenerating grouped outputs using currently saved mapping files...")
        self.start_generation()

    def open_result_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self._open_path(Path(path))

    def open_statistics_result_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self._open_path(Path(path))

    def open_last_output_dir(self) -> None:
        if self.last_results:
            self._open_path(self.last_results[-1].run_dir)

    def open_main_output(self) -> None:
        if self.last_results and self.last_results[-1].xlsx_path is not None:
            self._open_path(self.last_results[-1].xlsx_path)

    def open_statistics_output_dir(self) -> None:
        if self.last_stats_result is not None:
            self._open_path(self.last_stats_result.output_dir)

    def _open_path(self, path: Path) -> None:
        QDesktopServices.openUrl(path.resolve().as_uri())

    def closeEvent(self, event) -> None:
        self._save_state()
        if self.deck_thread is not None:
            self.deck_thread.quit()
            self.deck_thread.wait(3000)
        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait(3000)
        if self.stats_thread is not None:
            self.stats_thread.quit()
            self.stats_thread.wait(3000)
        if self.challenge_stats_thread is not None:
            self.challenge_stats_thread.quit()
            self.challenge_stats_thread.wait(3000)
        super().closeEvent(event)


def _install_crash_logger() -> None:
    """Log unhandled exceptions to a file — pythonw.exe has no console, so without
    this any exception raised inside a Qt slot (e.g. a button click handler) is
    silently discarded and the app just appears to do nothing."""
    repo_root = Path(__file__).resolve().parents[1]
    log_path = repo_root / "outputs" / "gui_error.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    default_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n--- {datetime.now().isoformat()} ---\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
        except Exception:
            pass
        default_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def main() -> int:
    _install_crash_logger()
    app = QApplication([])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = StudioWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())