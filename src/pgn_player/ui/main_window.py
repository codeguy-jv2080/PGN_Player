"""Desktop player presentation; all board state lives in PlaybackController."""
from __future__ import annotations

from html import escape
from pathlib import Path
import os
import tempfile

import chess
import chess.pgn
from PySide6.QtCore import QByteArray, QEvent, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from ..autoplay import AutoplayController
from ..guess_move import GuessMoveController
from ..pgn_loader import IndexingCancelled, index_pgn
from ..playback import PlaybackController
from .board import BoardWidget
from .game_list import GameListWidget
from .notation import NotationWidget
from .preferences import PreferencesDialog
from .themes import apply_theme


class IndexWorker(QThread):
    indexed = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            collection = index_pgn(self.path, progress=self.progress.emit, cancel=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.indexed.emit(collection)
        except IndexingCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    file_loaded = Signal(str)

    def __init__(self, store, paths):
        super().__init__()
        self.store, self.paths = store, paths
        self.settings = store.get_settings()
        self.theme = self.settings["theme"]
        self.player = PlaybackController()
        self.autoplay = AutoplayController(self.player)
        self.guess = GuessMoveController(self.player)
        self._worker = None
        self._loading = False
        self._closing = False
        self._pending_open = None
        self._restore_requested = True
        self._last_rendered_game = None
        self._status_error = ""
        self._play_timer = QTimer(self)
        self._play_timer.setSingleShot(True)
        self._play_timer.timeout.connect(self._autoplay_tick)
        self._opponent_timer = QTimer(self)
        self._opponent_timer.setSingleShot(True)
        self._opponent_timer.timeout.connect(self._opponent_tick)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_resume)
        self.setWindowTitle("PGN Player")
        self.setMinimumSize(820, 620)
        self.resize(1220, 860)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_menus()
        self._apply_settings()
        apply_theme(self.theme)
        self.board.theme = self.theme
        self.board.orientation = self.settings["orientation"]
        self._restore_window()
        self._refresh()
        QApplication.instance().installEventFilter(self)

    def _button(self, text, callback, tooltip="", primary=False):
        button = QPushButton(text)
        button.clicked.connect(callback)
        if tooltip:
            button.setToolTip(tooltip)
        if primary:
            button.setObjectName("primary")
        return button

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(9)
        header = QHBoxLayout()
        header.addWidget(self._button("Open PGN…", self.choose_file, "Open a PGN file (Ctrl+O)"))
        self.file_label = QLabel("PGN Player")
        self.file_label.setObjectName("title")
        self.file_label.setTextFormat(Qt.TextFormat.PlainText)
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.file_label, 1)
        self.theme_button = self._button("Dark mode", self.toggle_theme)
        header.addWidget(self.theme_button)
        self.guess_button = self._button("Guess the Move", self._toggle_guess, "Train with recorded PGN moves (Ctrl+G)")
        self.guess_button.setCheckable(True)
        header.addWidget(self.guess_button)
        layout.addLayout(header)

        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setChildrenCollapsible(False)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.board = BoardWidget()
        self.board.move_requested.connect(self._board_move)
        self.main_splitter.addWidget(self.board)
        right = QWidget()
        right.setMinimumWidth(290)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(5, 0, 0, 0)
        self.players_label = QLabel("Open a PGN to begin")
        self.players_label.setObjectName("title")
        self.players_label.setWordWrap(True)
        self.players_label.setMaximumHeight(80)
        self.players_label.setTextFormat(Qt.TextFormat.PlainText)
        right_layout.addWidget(self.players_label)
        self.metadata = QLabel("Drop a PGN file here, or choose Open PGN.")
        self.metadata.setObjectName("muted")
        self.metadata.setWordWrap(True)
        self.metadata.setMaximumHeight(120)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.metadata)
        notation_header = QHBoxLayout()
        moves_label = QLabel("MOVES")
        moves_label.setObjectName("muted")
        notation_header.addWidget(moves_label, 1)
        self.mainline_button = self._button("Return to main line", lambda: self._navigate(self.player.return_mainline))
        notation_header.addWidget(self.mainline_button)
        right_layout.addLayout(notation_header)
        self.notation = NotationWidget()
        self.notation.move_selected.connect(lambda path: self._navigate(lambda: self.player.go_to(path)))
        right_layout.addWidget(self.notation, 1)
        self.main_splitter.addWidget(right)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([710, 455])
        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(self.main_splitter, 1)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        self.previous_game_button = self._button("‹ Game", lambda: self._navigate(self.player.previous_game), "Previous game (Ctrl+Left)")
        self.first_button = self._button("|‹", lambda: self._navigate(self.player.first), "First position (Home)")
        self.previous_button = self._button("‹", lambda: self._navigate(self.player.previous), "Previous move (Left)")
        self.play_button = self._button("▶ Play", self.toggle_play, "Play / pause (Space)", primary=True)
        self.play_button.setMinimumWidth(100)
        self.next_button = self._button("›", lambda: self._navigate(self.player.next), "Next move (Right)")
        self.last_button = self._button("›|", lambda: self._navigate(self.player.last), "Last position (End)")
        self.next_game_button = self._button("Game ›", lambda: self._navigate(self.player.next_game), "Next game (Ctrl+Right)")
        for widget in (self.previous_game_button, self.first_button, self.previous_button, self.play_button, self.next_button, self.last_button, self.next_game_button):
            controls.addWidget(widget)
        controls.addStretch()
        self.turn_label = QLabel("White to move")
        self.turn_label.setObjectName("muted")
        controls.addWidget(self.turn_label)
        self.flip_button = self._button("Flip board", self.flip_board, "Flip board (F)")
        controls.addWidget(self.flip_button)
        upper_layout.addLayout(controls)

        secondary = QHBoxLayout()
        secondary.addWidget(QLabel("Move delay"))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(.1, 120)
        self.speed.setSingleStep(.5)
        self.speed.setDecimals(1)
        self.speed.setSuffix(" sec")
        self.speed.setAccessibleName("Autoplay move delay")
        self.speed.setValue(self.settings["delay_seconds"])
        self.speed.valueChanged.connect(self._speed_changed)
        secondary.addWidget(self.speed)
        self.continue_box = QCheckBox("Continue to next game")
        self.continue_box.setChecked(self.settings["continue_next"])
        self.continue_box.toggled.connect(self._continue_changed)
        secondary.addWidget(self.continue_box)
        secondary.addStretch()
        self.game_position_label = QLabel("No file loaded")
        self.game_position_label.setObjectName("muted")
        secondary.addWidget(self.game_position_label)
        self.games_button = self._button("Hide games", self._toggle_games)
        secondary.addWidget(self.games_button)
        upper_layout.addLayout(secondary)

        self.guess_panel = QGroupBox("Guess the Move")
        guess_layout = QVBoxLayout(self.guess_panel)
        guess_options = QHBoxLayout()
        self.guess_color = QComboBox()
        for label, data in (("Guess White", "white"), ("Guess Black", "black"), ("Guess Both", "both")):
            self.guess_color.addItem(label, data)
        self.guess_color.setCurrentIndex(max(0, self.guess_color.findData(self.settings["guess_color"])))
        self.guess_color.currentIndexChanged.connect(self._guess_preferences_changed)
        self.guess_variations = QCheckBox("Accept PGN variations")
        self.guess_variations.setChecked(self.settings["guess_variations"])
        self.guess_variations.toggled.connect(self._guess_preferences_changed)
        guess_options.addWidget(self.guess_color)
        guess_options.addWidget(self.guess_variations)
        guess_options.addStretch()
        self.guess_stats = QLabel("Guesses: 0   Correct: 0   Incorrect: 0   Score: 0%")
        guess_options.addWidget(self.guess_stats)
        guess_options.addWidget(self._button("Reset", self._reset_guess))
        guess_layout.addLayout(guess_options)
        guess_feedback = QHBoxLayout()
        self.guess_feedback = QLabel("Click or drag a piece to guess the recorded move.")
        self.guess_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.guess_feedback.setWordWrap(True)
        guess_feedback.addWidget(self.guess_feedback, 1)
        self.guess_continue = self._button("Continue with recorded move", self._continue_guess)
        guess_feedback.addWidget(self.guess_continue)
        guess_layout.addLayout(guess_feedback)
        self.guess_panel.hide()
        upper_layout.addWidget(self.guess_panel)
        self.vertical_splitter.addWidget(upper)
        self.game_list = GameListWidget()
        self.game_list.setMinimumHeight(110)
        self.game_list.game_selected.connect(lambda index: self._navigate(lambda: self.player.select_game(index)))
        self.vertical_splitter.addWidget(self.game_list)
        self.vertical_splitter.setStretchFactor(0, 4)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([640, 155])
        layout.addWidget(self.vertical_splitter, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.cancel_load = self._button("Cancel", self._cancel_loading)
        self.cancel_load.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_load)
        self.edition_label = QLabel(f"{str(self.paths.edition).capitalize()} edition")
        self.statusBar().addPermanentWidget(self.edition_label)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("&Open PGN…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_file)
        file_menu.addAction(self.open_action)
        self.recent_menu = file_menu.addMenu("Recent files")
        self.recent_menu.aboutToShow.connect(self._populate_recent)
        self.export_action = QAction("Export current game…", self)
        self.export_action.triggered.connect(self.export_game)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        preferences_action = QAction("Preferences…", self)
        preferences_action.triggered.connect(self.show_preferences)
        file_menu.addAction(preferences_action)
        file_menu.addSeparator()
        close_action = QAction("E&xit", self)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)
        view_menu = self.menuBar().addMenu("&View")
        for label, callback in (("Flip board", self.flip_board), ("Light / dark mode", self.toggle_theme), ("Show / hide games", self._toggle_games)):
            action = QAction(label, self)
            action.triggered.connect(callback)
            view_menu.addAction(action)
        help_menu = self.menuBar().addMenu("&Help")
        shortcuts = QAction("Keyboard shortcuts", self)
        shortcuts.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts)
        about = QAction("About PGN Player", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _save_setting(self, key, value):
        self.settings[key] = value
        try:
            self.store.set(key, value)
        except Exception as exc:
            self._error(f"Could not save preferences: {exc}")

    def _restore_window(self):
        for key, widget, method in (("window_geometry", self, "restoreGeometry"), ("main_splitter", self.main_splitter, "restoreState"), ("vertical_splitter", self.vertical_splitter, "restoreState")):
            value = self.store.get(key)
            if isinstance(value, str):
                try:
                    getattr(widget, method)(QByteArray.fromBase64(value.encode("ascii")))
                except (ValueError, TypeError, UnicodeError):
                    pass
        screens = QApplication.screens()
        if screens and not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in screens):
            self.move(screens[0].availableGeometry().topLeft())
        if not self.store.get("game_list_visible", True):
            self.game_list.hide()
            self.games_button.setText("Show games")

    def _apply_settings(self):
        self.autoplay.delay_seconds = self.settings["delay_seconds"]
        self.autoplay.between_games_seconds = self.settings["between_games_seconds"]
        self.autoplay.continue_next = self.settings["continue_next"]
        self.autoplay.include_variations = self.settings["include_variations"]
        self.guess.include_variations = self.settings["guess_variations"]

    def _error(self, message):
        self._status_error = message
        self.statusBar().showMessage(message)

    def _populate_recent(self):
        self.recent_menu.clear()
        for entry in self.store.recent_files():
            path = entry["path"]
            action = self.recent_menu.addAction(entry["name"])
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, path=path: self.open_file(path))
        if self.recent_menu.isEmpty():
            action = self.recent_menu.addAction("No recent files")
            action.setEnabled(False)

    def choose_file(self):
        self._pause_playback()
        self._refresh()
        path, _ = QFileDialog.getOpenFileName(self, "Open PGN", "", "PGN files (*.pgn);;All files (*)")
        if path:
            self.open_file(path)
        else:
            self._schedule_opponent()

    def reopen_last_file(self):
        if self.settings["reopen_last"]:
            path = self.store.get("last_file")
            if isinstance(path, str) and path:
                self.open_file(path)

    def open_file(self, path, restore=True):
        path = str(Path(path).expanduser().resolve())
        if self._closing:
            return
        self._pause_playback()
        self._save_resume()
        if self._worker is not None:
            self._pending_open = (path, restore)
            self._worker.requestInterruption()
            return
        self._status_error = ""
        self._restore_requested = restore
        self._loading = True
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_load.show()
        self.statusBar().showMessage(f"Indexing {Path(path).name}…")
        self._refresh_controls()
        self._worker = IndexWorker(path, self)
        self._worker.indexed.connect(self._indexed)
        self._worker.failed.connect(self._load_failed)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._index_finished)
        self._worker.start()

    def _indexed(self, collection):
        if self._closing or self._pending_open is not None or self._worker is None or self._worker.isInterruptionRequested():
            return
        try:
            if not len(collection):
                self._error("This file contains no readable PGN games.")
                return
            self.player.set_collection(collection)
            notices = []
            try:
                resume = self.store.load_resume(collection.path, collection.fingerprint) if self._restore_requested else None
                if resume:
                    if self.player.select_game(resume["game_index"]):
                        self.player.go_to(resume["variation_path"])
                    self.board.orientation = resume.get("orientation", self.board.orientation)
            except Exception as exc:
                notices.append(f"Could not restore the saved position: {exc}")
            try:
                self.store.add_recent(collection.path)
                self.store.set("last_file", str(collection.path))
            except Exception as exc:
                notices.append(f"Could not save the recent file: {exc}")
            self.game_list.set_collection(collection)
            self.file_label.setText(Path(collection.path).name)
            self.file_label.setToolTip(str(collection.path))
            self.setWindowTitle(f"{Path(collection.path).name} — PGN Player")
            self.statusBar().showMessage(f"Loaded {len(collection):,} games. Arrow keys step through moves; Space plays.")
            if collection.warnings or notices:
                self._error("; ".join([*collection.warnings, *notices]))
            self._refresh()
            self.file_loaded.emit(str(collection.path))
        except Exception as exc:
            self._error(f"Could not open PGN: {exc}")
            self._refresh()

    def _load_failed(self, message):
        if not self._closing and self._pending_open is None:
            self._error(f"Could not open PGN: {message}")

    def _index_finished(self):
        worker = self._worker
        self._worker = None
        self._loading = False
        self.progress.hide()
        self.cancel_load.hide()
        if worker is not None:
            worker.deleteLater()
        if self._closing:
            QTimer.singleShot(0, self.close)
        elif self._pending_open is not None:
            path, restore = self._pending_open
            self._pending_open = None
            self.open_file(path, restore)
        else:
            self._resume_guess()
        self._refresh()

    def _cancel_loading(self):
        self._pending_open = None
        if self._worker is not None:
            self._worker.requestInterruption()
            self.statusBar().showMessage("Canceling file load…")

    def _pause_autoplay(self):
        self._play_timer.stop()
        self.autoplay.pause()

    def _pause_playback(self):
        self._pause_autoplay()
        self._opponent_timer.stop()

    def _stop_guess(self):
        self._opponent_timer.stop()
        self.guess.stop()
        self.guess_button.setChecked(False)
        self.guess_panel.hide()
        self.board.input_enabled = False

    def _stop_modes(self):
        self._pause_autoplay()
        self._stop_guess()

    def _navigate(self, callback):
        if self._loading:
            return
        self._pause_playback()
        try:
            callback()
        except Exception as exc:
            self._error(f"Could not navigate: {exc}")
        finally:
            self._resume_guess()
            self._refresh()

    def _resume_guess(self):
        # Keep the session, side and scores; start() would reset them. Accessing
        # pending_answer discards an answer belonging to the previous position.
        if self.guess.mode != "off" and not self.guess.pending_answer:
            self.guess_feedback.setText("Click or drag a piece to guess the recorded move.")
        self._schedule_opponent()

    def _refresh(self):
        game = self.player.game
        node = self.player.node
        training = self.guess.mode != "off"
        self.board.set_position(self.player.board, node.move if node and node.parent else None)
        self.board.input_enabled = self.guess.waiting_for_guess and not self._loading
        self.notation.show_game(game, node, self.theme, training)
        self.theme_button.setText("Light mode" if self.theme == "dark" else "Dark mode")
        if game is not None:
            headers = game.headers
            def player_name(color):
                name, elo = headers.get(color, "?"), headers.get(color + "Elo", "")
                return name + (f"  {elo}" if elo and elo != "?" else "")
            self.players_label.setText(f"{player_name('White')}\n{player_name('Black')}")
            groups = []
            event = " · ".join(headers.get(key, "") for key in ("Event", "Site", "Date") if headers.get(key) and headers[key] not in {"?", "????.??.??"})
            if event:
                groups.append(event)
            detail = " · ".join(f"{key}: {headers[key]}" for key in ("Round", "Result", "ECO") if headers.get(key) and headers[key] != "?" and not (training and key == "Result"))
            if detail:
                groups.append(detail)
            if not training:
                opening = " · ".join(headers[key] for key in ("Opening", "Variation") if headers.get(key) and headers[key] != "?")
                if opening:
                    groups.append(opening)
            self.metadata.setText("\n".join(groups))
            self.game_position_label.setText(f"Game {self.player.game_index + 1:,} / {len(self.player.collection):,}")
            if self._last_rendered_game != (id(game), self.player.game_index):
                self.game_list.select_game(self.player.game_index)
                self._last_rendered_game = (id(game), self.player.game_index)
            errors = self.player.collection.errors.get(self.player.game_index, [])
            if errors:
                self._error("PGN contains an invalid continuation; showing readable moves. " + "; ".join(errors))
            self._save_timer.start()
        else:
            self.players_label.setText("Open a PGN to begin")
            self.metadata.setText("A focused player for watching and studying chess games.\nDrop a PGN here, or choose Open PGN.")
        turn = "White" if self.player.board.turn else "Black"
        self.turn_label.setText(f"{turn} to move" + (" · check" if self.player.board.is_check() else ""))
        self.guess_stats.setText(f"Guesses: {self.guess.guesses}   Correct: {self.guess.correct}   Incorrect: {self.guess.incorrect}   Score: {self.guess.percentage:.0f}%")
        self.guess_continue.setVisible(bool(self.guess.pending_answer))
        self._refresh_controls()

    def _refresh_controls(self):
        active = self.player.game is not None and not self._loading
        path = self.player.path
        self.first_button.setEnabled(active and bool(path))
        self.previous_button.setEnabled(active and bool(path))
        self.next_button.setEnabled(active and not self.player.at_end)
        self.last_button.setEnabled(active and not self.player.at_end)
        self.mainline_button.setEnabled(active and any(path))
        self.previous_game_button.setEnabled(active and self.player.game_index > 0)
        self.next_game_button.setEnabled(active and self.player.game_index + 1 < len(self.player.collection))
        training = self.guess.mode != "off"
        self.play_button.setEnabled(active and not training)
        self.play_button.setToolTip("Turn off Guess the Move to use autoplay." if training else "Play / pause (Space)")
        self.play_button.setText("❚❚ Pause" if self.autoplay.playing else "▶ Play")
        self.guess_button.setEnabled(active or training)
        self.guess_panel.setEnabled(active)
        self.export_action.setEnabled(active)
        self.board.input_enabled = active and self.guess.waiting_for_guess

    def toggle_play(self):
        if self._loading or self.player.game is None or self.guess.mode != "off":
            return
        if self.autoplay.playing:
            self._pause_autoplay()
        else:
            self._pause_playback()
            if self.autoplay.start():
                self._play_timer.start(max(1, int(self.autoplay.next_delay * 1000)))
        self._refresh()

    def _autoplay_tick(self):
        if not self.autoplay.playing or self._loading or self._closing:
            return
        try:
            self.autoplay.step()
            self._refresh()
            if self.autoplay.playing:
                self._play_timer.start(max(1, int(self.autoplay.next_delay * 1000)))
            else:
                self.statusBar().showMessage("Playback finished.")
        except Exception as exc:
            self.autoplay.pause()
            self._error(f"Playback stopped: {exc}")
            self._refresh_controls()

    def _speed_changed(self, value):
        self._save_setting("delay_seconds", value)
        self.autoplay.delay_seconds = value
        if self.autoplay.playing:
            self._play_timer.start(max(1, int(self.autoplay.next_delay * 1000)))

    def _continue_changed(self, checked):
        self._save_setting("continue_next", checked)
        self.autoplay.continue_next = checked

    def flip_board(self):
        self.board.orientation = "black" if self.board.orientation == "white" else "white"
        self._save_setting("orientation", self.board.orientation)
        self.board.update()
        self._save_resume()

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self._save_setting("theme", self.theme)
        apply_theme(self.theme)
        self.board.theme = self.theme
        self._refresh()

    def _toggle_games(self):
        visible = not self.game_list.isHidden()
        self.game_list.setVisible(not visible)
        self.games_button.setText("Show games" if visible else "Hide games")
        self._save_setting("game_list_visible", not visible)

    def show_preferences(self):
        dialog = PreferencesDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for key, value in dialog.values().items():
                self._save_setting(key, value)
            self.continue_box.setChecked(self.settings["continue_next"])
            self._apply_settings()
            if self.autoplay.playing:
                self._play_timer.start(max(1, int(self.autoplay.next_delay * 1000)))

    def _toggle_guess(self, checked=None):
        enabled = self.guess_button.isChecked() if checked is None else checked
        if enabled and (self.player.game is None or self._loading):
            self.guess_button.setChecked(self.guess.mode != "off")
            return
        self._pause_autoplay()
        if enabled:
            self.guess_button.setChecked(True)
            self.guess_panel.show()
            self.guess.start(self.guess_color.currentData())
            self.guess.include_variations = self.guess_variations.isChecked()
            self.guess_feedback.setText("Click or drag a piece to guess the recorded move.")
            self._schedule_opponent()
        else:
            self._stop_guess()
        self._refresh()

    def _guess_preferences_changed(self, *_):
        self._save_setting("guess_color", self.guess_color.currentData())
        self._save_setting("guess_variations", self.guess_variations.isChecked())
        self.guess.include_variations = self.guess_variations.isChecked()
        if self.guess.mode != "off":
            self._opponent_timer.stop()
            self.guess.start(self.guess_color.currentData())
            self.guess_feedback.setText("Click or drag a piece to guess the recorded move.")
            self._schedule_opponent()
            self._refresh()

    def _reset_guess(self):
        self.guess.reset_stats()
        self._refresh()

    def _schedule_opponent(self):
        self._opponent_timer.stop()
        if self._closing or self._loading or self.guess.mode == "off" or self.player.game is None:
            return
        if not self.guess.waiting_for_guess and not self.guess.pending_answer and not self.player.at_end:
            self._opponent_timer.start(650)
        elif self.player.at_end:
            self.guess_feedback.setText("End of recorded line. Select another game or position to continue studying.")

    def _opponent_tick(self):
        if self._closing or self._loading or self.guess.mode == "off":
            return
        if QApplication.activeModalWidget() is not None:
            self._schedule_opponent()
            return
        self.guess.play_opponent()
        self._refresh()
        self._schedule_opponent()

    def _board_move(self, origin, destination):
        if self._loading or self._closing or not self.guess.waiting_for_guess:
            return
        candidates = [move for move in self.player.board.legal_moves if move.from_square == origin and move.to_square == destination]
        if not candidates:
            self.guess_feedback.setText("That move is not legal in this position. Try again.")
            return
        move = candidates[0]
        if len(candidates) > 1:
            names = {chess.QUEEN: "Queen", chess.ROOK: "Rook", chess.BISHOP: "Bishop", chess.KNIGHT: "Knight"}
            choices = [names[candidate.promotion] for candidate in candidates]
            chosen, accepted = QInputDialog.getItem(self, "Promote pawn", "Choose promotion piece", choices, 0, False)
            if not accepted:
                return
            move = candidates[choices.index(chosen)]
        result = self.guess.submit(move)
        if result.status == "correct":
            self.guess_feedback.setText(f"Correct — {result.played_san}.")
        elif result.status == "incorrect":
            self.guess_feedback.setText(f"{result.played_san} does not match. The recorded move is {result.expected_san}.")
        elif result.status == "illegal":
            self.guess_feedback.setText("That move is not legal. Try again.")
        self._refresh()
        self._schedule_opponent()

    def _continue_guess(self):
        self.guess.continue_recorded()
        self.guess_feedback.setText("Continue by guessing the next recorded move.")
        self._refresh()
        self._schedule_opponent()

    def _save_resume(self):
        if self.player.collection is None or self.player.game is None:
            return
        try:
            self.store.save_resume(self.player.collection.path, self.player.collection.fingerprint,
                self.player.game_index, self.player.path, self.board.orientation)
        except Exception as exc:
            self._error(f"Could not save playback position: {exc}")

    def export_game(self):
        if self.player.game is None:
            return
        self._pause_playback()
        self._refresh()
        game = self.player.game
        source = Path(self.player.collection.path).resolve()
        path, _ = QFileDialog.getSaveFileName(self, "Export current game", "game.pgn", "PGN files (*.pgn)")
        self._schedule_opponent()
        if not path:
            return
        destination = Path(path).resolve()
        temporary = None
        try:
            if os.path.normcase(str(destination)) == os.path.normcase(str(source)) or (destination.exists() and source.exists() and os.path.samefile(destination, source)):
                self._error("Choose a different file for export; the open PGN is preserved.")
                return
            text = game.accept(chess.pgn.StringExporter(headers=True, variations=True, comments=True))
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=".pgn-player-", suffix=".tmp", dir=destination.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(text + "\n\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
            self.statusBar().showMessage(f"Exported current game to {destination.name}.")
        except Exception as exc:
            self._error(f"Could not export game: {exc}")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress or not self.isActiveWindow():
            return super().eventFilter(watched, event)
        if QApplication.activeModalWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit)) or (isinstance(focus, QTextEdit) and not focus.isReadOnly()):
            return False
        key, modifiers = event.key(), event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            return False
        action = None
        if ctrl:
            action = {Qt.Key.Key_Left: lambda: self._navigate(self.player.previous_game), Qt.Key.Key_Right: lambda: self._navigate(self.player.next_game),
                Qt.Key.Key_G: lambda: self._toggle_guess(not self.guess_button.isChecked())}.get(key)
        elif not (modifiers & Qt.KeyboardModifier.ShiftModifier):
            action = {Qt.Key.Key_Left: lambda: self._navigate(self.player.previous), Qt.Key.Key_Right: lambda: self._navigate(self.player.next),
                Qt.Key.Key_Home: lambda: self._navigate(self.player.first), Qt.Key.Key_End: lambda: self._navigate(self.player.last),
                Qt.Key.Key_Space: self.toggle_play, Qt.Key.Key_F: self.flip_board}.get(key)
        if action is not None:
            if not event.isAutoRepeat():
                action()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".pgn" for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".pgn":
                self.open_file(url.toLocalFile())
                event.acceptProposedAction()
                break

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", "Left / Right — Previous / next move\nHome / End — First / last position\nCtrl+Left / Ctrl+Right — Previous / next game\nSpace — Play / pause (outside Guess the Move)\nF — Flip board\nCtrl+O — Open PGN\nCtrl+G — Toggle Guess the Move\n\nPlayback shortcuts are inactive in text-entry fields.\nManual navigation pauses autoplay. Guess the Move stays active across games, positions and files until you toggle it off.")

    def _about(self):
        QMessageBox.about(self, "About PGN Player", f"<b>PGN Player</b><p>Local chess playback and recorded-move training for Windows.</p><p>{escape(str(self.paths.edition).capitalize())} edition · No account or network required.</p><p>GPL-3.0-or-later. Built with Python, PySide6, python-chess and SQLite.</p>")

    def closeEvent(self, event):
        self._closing = True
        self._pending_open = None
        self._stop_modes()
        self._save_timer.stop()
        if self._worker is not None:
            self._worker.requestInterruption()
            event.ignore()
            self.statusBar().showMessage("Finishing file operation before closing…")
            return
        self._save_resume()
        for key, value in (("window_geometry", self.saveGeometry()), ("main_splitter", self.main_splitter.saveState()), ("vertical_splitter", self.vertical_splitter.saveState())):
            self._save_setting(key, bytes(value.toBase64()).decode("ascii"))
        QApplication.instance().removeEventFilter(self)
        event.accept()
