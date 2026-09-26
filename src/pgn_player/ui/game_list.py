"""A virtualized game table: one model row per header, no per-game widgets."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTableView, QVBoxLayout, QWidget


class GameTableModel(QAbstractTableModel):
    columns = (("#", None), ("White", "White"), ("Black", "Black"), ("Result", "Result"), ("White Elo", "WhiteElo"), ("Black Elo", "BlackElo"), ("Date", "Date"), ("ECO", "ECO"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return index.row() + 1 if index.column() == 0 else self.entries[index.row()].headers.get(self.columns[index.column()][1], "")
        if role == Qt.ItemDataRole.ToolTipRole:
            headers = self.entries[index.row()].headers
            return " · ".join(headers.get(key, "") for key in ("Event", "Site", "Opening") if headers.get(key) and headers[key] != "?")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][0]
        return None


class GameFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.field = "All tags"
        self.result = "Any result"

    def filterAcceptsRow(self, source_row, source_parent):
        # The default unfiltered view must not join every header string in a
        # multi-million-game collection just to accept every row.
        if not self.query.strip() and self.result == "Any result":
            return True
        headers = self.sourceModel().entries[source_row].headers
        if self.result != "Any result" and headers.get("Result", "*") != self.result:
            return False
        if not self.query.strip():
            return True
        fields = {"Players": ("White", "Black"), "Event": ("Event", "Site"), "Opening / ECO": ("ECO", "Opening", "Variation")}.get(self.field, tuple(headers))
        text = " ".join(str(headers.get(key, "")) for key in fields).casefold()
        return all(word in text for word in self.query.casefold().split())


class GameListWidget(QWidget):
    game_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this PGN…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search games")
        self.field = QComboBox()
        self.field.addItems(["All tags", "Players", "Event", "Opening / ECO"])
        self.field.setAccessibleName("Search field")
        self.result = QComboBox()
        self.result.addItems(["Any result", "1-0", "0-1", "1/2-1/2", "*"])
        self.result.setAccessibleName("Result filter")
        self.count = QLabel("0 games")
        self.count.setObjectName("muted")
        row.addWidget(self.search, 1)
        row.addWidget(self.field)
        row.addWidget(self.result)
        row.addWidget(self.count)
        layout.addLayout(row)
        self.table = QTableView()
        self.table.setAccessibleName("Games in PGN")
        self.model = GameTableModel(self)
        self.proxy = GameFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(29)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 55), (3, 90), (4, 90), (5, 90), (6, 110), (7, 70)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        self.search.textChanged.connect(self._filter)
        self.field.currentTextChanged.connect(self._filter)
        self.result.currentTextChanged.connect(self._filter)
        self.table.clicked.connect(lambda index: self.game_selected.emit(self.proxy.mapToSource(index).row()))
        self.table.activated.connect(lambda index: self.game_selected.emit(self.proxy.mapToSource(index).row()))

    def _filter(self, *_):
        self.proxy.query, self.proxy.field, self.proxy.result = self.search.text(), self.field.currentText(), self.result.currentText()
        self.proxy.invalidate()
        self.count.setText(f"{self.proxy.rowCount():,} / {self.model.rowCount():,} games")

    def set_collection(self, collection):
        self.model.set_entries(collection.entries)
        self._filter()

    def select_game(self, index):
        proxy_index = self.proxy.mapFromSource(self.model.index(index, 0))
        if proxy_index.isValid():
            self.table.selectRow(proxy_index.row())
            self.table.scrollTo(proxy_index, QAbstractItemView.ScrollHint.EnsureVisible)
