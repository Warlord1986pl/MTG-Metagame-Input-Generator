from __future__ import annotations

import csv
import traceback
from argparse import Namespace
from datetime import date
from html import escape
from pathlib import Path
from typing import List, Optional

import pandas as pd
from PySide6.QtCore import QDate, QObject, QSettings, Qt, QThread, Signal
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
)

try:
    from metagame_input_generator import GenerationRunResult, fetch_available_decks, parse_args, run_generation
except ModuleNotFoundError:
    from .metagame_input_generator import GenerationRunResult, fetch_available_decks, parse_args, run_generation

try:
    from statistics_engine import StatisticsRunResult, run_statistics
except ModuleNotFoundError:
    from .statistics_engine import StatisticsRunResult, run_statistics

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

    def __init__(self, format_name: str, week_start: str, week_end: str, limit: int) -> None:
        super().__init__()
        self._format_name = format_name
        self._week_start = week_start
        self._week_end = week_end
        self._limit = limit

    def run(self) -> None:
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
        self.last_results: List[GenerationRunResult] = []
        self.last_stats_result: Optional[StatisticsRunResult] = None
        self.deck_refresh_pending = False
        self.repo_root = Path(__file__).resolve().parents[1]
        self.config_dir = self.repo_root / "docs"
        self.editor_rows: List[dict[str, str]] = []
        self.current_editor_source: Optional[Path] = None
        self._deck_catalog_cache: List[str] = []

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self._build_ui()
        self._restore_state()

    def _build_generator_args(self) -> Namespace:
        args = parse_args([])
        args.format_name = self.format_combo.currentText()
        selected_deck = self.my_deck_combo.currentData(Qt.UserRole)
        args.my_deck = str(selected_deck or self.my_deck_combo.currentText() or "Domain Zoo").strip()
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
        args.history_output_dir = str(outputs_dir / "history")
        args.output_csv = str(outputs_dir / "metagame_input.csv")
        args.output_xlsx = str(outputs_dir / "metagame_input.xlsx")
        args.output_csv_rogue_grouped = str(outputs_dir / "metagame_input_rogue_grouped.csv")
        args.output_xlsx_rogue_grouped = str(outputs_dir / "metagame_input_rogue_grouped.xlsx")
        args.output_xml_grouped = str(outputs_dir / "metagame_input_grouped.xml")
        args.unknown_output = str(outputs_dir / "unknown_archetypes.csv")
        args.alias_suggestions_output = str(outputs_dir / "alias_suggestions.csv")
        args.output_profile = "analysis"
        return args

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
        self.week_start_edit.dateChanged.connect(self.refresh_deck_list)

        self.week_end_edit = QDateEdit()
        self.week_end_edit.setCalendarPopup(True)
        self.week_end_edit.setDisplayFormat("yyyy-MM-dd")
        self.week_end_edit.dateChanged.connect(self.refresh_deck_list)

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

        form.addRow("Format", self.format_combo)
        form.addRow("My Deck", deck_row)
        form.addRow("Deck Mode", self.deck_mode_label)
        form.addRow("Week Start", self.week_start_edit)
        form.addRow("Week End", self.week_end_edit)
        form.addRow("My WR Window", self.my_window_spin)
        form.addRow("Fallback Window", self.my_fallback_spin)
        form.addRow("Rogue Threshold", self.rogue_spin)
        form.addRow("Metagame Limit", self.metagame_limit_spin)
        form.addRow("Matchup Limit", self.matchup_limit_spin)
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
            "Deck list is loaded from API for the chosen format and date range. Selecting 'My Deck' forces 50% matchup values."
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
        self.editor_source_label = QLabel("Source: not loaded")
        self.editor_source_label.setWordWrap(True)
        self.editor_source_label.setObjectName("muted")

        self.load_latest_button = QPushButton("Load Latest Output")
        self.load_latest_button.clicked.connect(self.load_latest_output_for_editor)

        source_row.addWidget(self.editor_source_label, 1)
        source_row.addWidget(self.load_latest_button)
        left_layout.addLayout(source_row)

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

        edit_form.addRow("Raw Name", self.raw_name_value)
        edit_form.addRow("Canonical Deck", self.editor_canonical_input)
        edit_form.addRow("Archetype", self.editor_archetype_combo)
        edit_form.addRow("New Archetype", custom_row)
        right_layout.addLayout(edit_form)

        self.editor_status_label = QLabel("Select a row from the table to edit mapping and archetype.")
        self.editor_status_label.setWordWrap(True)
        self.editor_status_label.setObjectName("muted")
        right_layout.addWidget(self.editor_status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.save_mapping_button = QPushButton("Save Mapping")
        self.save_mapping_button.setObjectName("primaryButton")
        self.save_mapping_button.clicked.connect(self._save_editor_changes)

        self.save_all_mappings_button = QPushButton("Save All Mappings")
        self.save_all_mappings_button.clicked.connect(self._save_all_editor_changes)

        self.regenerate_grouped_button = QPushButton("Regenerate Grouped Now")
        self.regenerate_grouped_button.clicked.connect(self._regenerate_from_editor)

        self.reload_archetypes_button = QPushButton("Reload Archetypes")
        self.reload_archetypes_button.clicked.connect(self._refresh_archetype_combo)

        self.open_configs_button = QPushButton("Open Config Folder")
        self.open_configs_button.clicked.connect(lambda: self._open_path(self.config_dir))

        button_row.addWidget(self.save_mapping_button)
        button_row.addWidget(self.save_all_mappings_button)
        button_row.addWidget(self.regenerate_grouped_button)
        button_row.addWidget(self.reload_archetypes_button)
        button_row.addWidget(self.open_configs_button)
        right_layout.addLayout(button_row)
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
        button_row.addWidget(self.stats_run_button)
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

    def _restore_state(self) -> None:
        defaults = parse_args([])
        end_date = QDate.currentDate().addDays(-1)
        start_date = end_date.addDays(-6)

        saved_format = self.settings.value("format_name", defaults.format_name)
        index = self.format_combo.findText(saved_format)
        self.format_combo.setCurrentIndex(index if index >= 0 else 0)

        self.week_start_edit.setDate(self._load_date("week_start", start_date))
        self.week_end_edit.setDate(self._load_date("week_end", end_date))
        self.my_window_spin.setValue(int(self.settings.value("my_window_days", defaults.my_window_days)))
        self.my_fallback_spin.setValue(int(self.settings.value("my_fallback_window_days", defaults.my_fallback_window_days)))
        self.rogue_spin.setValue(float(self.settings.value("rogue_threshold", defaults.rogue_threshold)))
        self.metagame_limit_spin.setValue(int(self.settings.value("metagame_limit", defaults.metagame_limit)))
        self.matchup_limit_spin.setValue(int(self.settings.value("matchup_limit", defaults.matchup_limit)))
        self.auto_stats_after_generate_check.setChecked(
            str(self.settings.value("auto_stats_after_generate", "false")).lower() in {"true", "1", "yes"}
        )

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
        saved_palette = str(self.settings.value("stats_palette", "classic") or "classic").lower()
        self.stats_output_profile_combo.setCurrentText(saved_profile if saved_profile in STATS_OUTPUT_PROFILES else "full")
        self.stats_palette_combo.setCurrentText(saved_palette if saved_palette in STATS_PALETTE_OPTIONS else "classic")
        self.stats_deck_colors_text.setPlainText(str(self.settings.value("stats_deck_colors", "") or ""))
        self.stats_legend_colors_text.setPlainText(str(self.settings.value("stats_legend_colors", "") or ""))
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
        return self.repo_root / "outputs" / "metagame_history.csv"

    def _find_latest_grouped_input(self) -> Optional[Path]:
        run_dir = self._latest_output_dir()
        if run_dir is None:
            return None
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
        return parent / "statistics"

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

    def start_statistics(self) -> None:
        if self.stats_thread is not None:
            QMessageBox.information(self, APP_NAME, "Statistics analysis is already running.")
            return

        self._sync_text_from_color_tables()

        input_path = Path(self.stats_input_edit.text().strip())
        if not input_path.exists():
            QMessageBox.warning(self, APP_NAME, "Choose an existing grouped XLSX input file first.")
            return

        history_text = self.stats_history_edit.text().strip()
        history_path = Path(history_text) if history_text else self._default_statistics_history_path()
        output_dir = self._statistics_output_dir_for_input(input_path)
        deck_colors = self._parse_stats_deck_colors()
        legend_colors = self._parse_stats_legend_colors()
        output_profile = self.stats_output_profile_combo.currentText().strip().lower() or "full"
        palette_name = self.stats_palette_combo.currentText().strip().lower() or "classic"

        self.stats_run_button.setEnabled(False)
        self.stats_open_folder_button.setEnabled(False)
        self.stats_files_list.clear()
        self.stats_log_output.clear()
        self.last_stats_result = None

        self.stats_summary_label.setText("Running statistics analysis...")
        self._append_stats_log(f"[stats] Starting analysis for: {input_path}")
        self._append_stats_log(f"[stats] Profile={output_profile}, palette={palette_name}, custom deck colors={len(deck_colors)}")
        self._append_stats_log(f"[stats] Custom legend colors={len(legend_colors)}")

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
        self.stats_thread.finished.connect(self._cleanup_statistics_worker)
        self.stats_thread.start()

    def _append_stats_log(self, line: str) -> None:
        self.stats_log_output.append(line)

    def _handle_statistics_success(self, result: StatisticsRunResult) -> None:
        self.last_stats_result = result
        self.stats_run_button.setEnabled(True)
        self.stats_open_folder_button.setEnabled(True)

        self.stats_history_edit.setText(str(result.history_csv))
        self.stats_summary_label.setText(
            "\n".join(
                [
                    f"Week index: {result.week_index}",
                    f"Deck rows: {result.deck_rows}",
                    f"Archetype rows: {result.archetype_rows}",
                    f"Output folder: {result.output_dir}",
                ]
            )
        )

        self.stats_files_list.clear()
        for path in result.files:
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            self.stats_files_list.addItem(item)

        self._append_stats_log("[stats] Analysis completed.")

    def _handle_statistics_failure(self, details: str) -> None:
        self.stats_run_button.setEnabled(True)
        self.stats_open_folder_button.setEnabled(False)
        self.stats_summary_label.setText("Statistics analysis failed.")
        self._append_stats_log("[stats][error] Analysis failed.")
        self._append_stats_log(details)
        QMessageBox.critical(self, APP_NAME, details.splitlines()[0] if details else "Statistics analysis failed.")

    def _cleanup_statistics_worker(self) -> None:
        if self.stats_worker is not None:
            self.stats_worker.deleteLater()
        if self.stats_thread is not None:
            self.stats_thread.deleteLater()
        self.stats_worker = None
        self.stats_thread = None

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
        self.deck_worker = DeckListWorker(format_name, week_start, week_end, limit)
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
        for path in [
            result.xlsx_path,
            result.csv_path,
            result.rogue_xml_path,
            result.rogue_xlsx_path,
            result.rogue_csv_path,
            result.unknown_output_path,
            result.alias_suggestions_path,
        ]:
            if path is None:
                continue
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            self.files_list.addItem(item)

    def _find_latest_range_dir(self, base_path: Path) -> Optional[Path]:
        if not base_path.exists():
            return None
        candidates = [path for path in base_path.iterdir() if path.is_dir() and "_to_" in path.name]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _latest_output_dir(self) -> Optional[Path]:
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
        run_dir = self._latest_output_dir()
        if run_dir is None:
            self.editor_rows = []
            self.current_editor_source = None
            self.editor_source_label.setText("Source: no generated outputs found yet")
            self.editor_table.setRowCount(0)
            self.editor_status_label.setText("Generate a snapshot first or place a result folder in outputs/.")
            return

        csv_path = run_dir / "metagame_input.csv"
        if not csv_path.exists():
            self.editor_rows = []
            self.current_editor_source = None
            self.editor_source_label.setText(f"Source: missing {csv_path.name} in {run_dir.name}")
            self.editor_table.setRowCount(0)
            return

        self.editor_rows = self._load_result_rows(csv_path)
        self.current_editor_source = csv_path
        self.editor_source_label.setText(f"Source: {csv_path}")
        self._populate_editor_table()
        self.editor_status_label.setText("Select a row to edit canonical deck name and archetype.")

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
        self.editor_status_label.setText("Editing selected row. Saving updates user mapping, alias, and exact archetype rule.")

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
        normalized = str(value or "").strip().lower()
        return normalized in {"unknown", "unkown"}

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

    def _save_rows_to_config(self, row_indices: List[int]) -> tuple[int, int, bool, Optional[object]]:
        model = ChangeModel(paths=ConfigPaths.from_config_dir(self.config_dir))
        verification_items: List[tuple[List[str], str, str]] = []
        processed_rows = 0
        total_source_names = 0

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

            for source_name in raw_names:
                model.queue_mapping(source_name, canonical_name, archetype)
                model.queue_alias(source_name, canonical_name, match_type="exact", priority=1)
                model.queue_archetype_rule(source_name, archetype, match_type="exact", priority=1)
            model.queue_archetype_rule(canonical_name, archetype, match_type="exact", priority=1)

            processed_rows += 1
            total_source_names += len(raw_names)
            verification_items.append((raw_names, canonical_name, archetype))

        if not model.has_changes():
            return 0, 0, False, None

        summary = model.apply(create_backup=True)
        verified = all(self._verify_saved_mapping_entries(raw_names, canonical_name, archetype) for raw_names, canonical_name, archetype in verification_items)
        return processed_rows, total_source_names, verified, summary

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
        processed_rows, total_source_names, verified, summary = self._save_rows_to_config([row_index])
        if summary is None:
            QMessageBox.warning(self, APP_NAME, "Nothing to save for selected row.")
            return

        self._refresh_archetype_combo()
        self._select_archetype(archetype)
        self._populate_editor_table()
        self.editor_table.selectRow(row_index)
        verification_text = "Saved and verified in active config." if verified else "Saved, but verification failed."
        self.editor_status_label.setText(
            f"{verification_text} rows={processed_rows}, source names={total_source_names}, aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}, mappings={summary.mappings_upserted}"
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

        processed_rows, total_source_names, verified, summary = self._save_rows_to_config(list(range(len(self.editor_rows))))
        if summary is None:
            QMessageBox.warning(self, APP_NAME, "Nothing to save across editor rows.")
            return

        self._refresh_archetype_combo()
        self._populate_editor_table()
        if 0 <= row_index < len(self.editor_rows):
            self.editor_table.selectRow(row_index)
        verification_text = "Saved and verified in active config." if verified else "Saved, but verification failed."
        self.editor_status_label.setText(
            f"{verification_text} rows={processed_rows}, source names={total_source_names}, aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}, mappings={summary.mappings_upserted}"
        )
        self._append_log(
            f"[OK] Saved ALL editor mappings: rows={processed_rows}, source names={total_source_names}, aliases={summary.aliases_upserted}, archetypes={summary.archetypes_upserted}, mappings={summary.mappings_upserted}"
        )

    def _regenerate_from_editor(self) -> None:
        if self.current_editor_source is not None:
            range_dir = self.current_editor_source.parent.name
            if "_to_" in range_dir:
                try:
                    start_text, end_text = range_dir.split("_to_", 1)
                    start_qdate = QDate.fromString(start_text, "yyyy-MM-dd")
                    end_qdate = QDate.fromString(end_text, "yyyy-MM-dd")
                    if start_qdate.isValid() and end_qdate.isValid():
                        self.week_start_edit.setDate(start_qdate)
                        self.week_end_edit.setDate(end_qdate)
                except Exception:
                    pass

        if self.week_end_edit.date() < self.week_start_edit.date():
            QMessageBox.warning(self, APP_NAME, "Week end must be greater than or equal to week start.")
            return
        self.editor_status_label.setText("Regenerating grouped outputs using current generator settings...")
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
        super().closeEvent(event)


def main() -> int:
    app = QApplication([])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = StudioWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())